"""
Fetch dialogue audio from a public Google Drive folder.

Walks a shared Drive folder recursively, picks out every audio file, and
downloads the ones this project does not already have into human_gen/,
flattening Drive's subdirectory structure.

    python fetch_drive.py https://drive.google.com/drive/folders/<id>
    python fetch_drive.py <url> dry=1            # list, download nothing
    python fetch_drive.py <url> limit=10         # fetch at most 10 files
    python fetch_drive.py <url> overwrite=1      # re-fetch even if present
    python fetch_drive.py <url> cookies=1        # use browser cookies

The folder must be shared as "Anyone with the link" -- this uses anonymous
access and never touches your Google credentials. A quick way to check: open
the link in a private/incognito window without signing in. If it loads, this
will work.

Where files land
----------------
Everything goes into human_gen/ as one flat directory, whatever Drive's folder
structure was. The Drive folder holds human-recorded takes, which is why they
are kept apart from machine_gen/ (Sarvam TTS output) and from the curated files
and from the finished master in dialogues-mp3/. stitch_mp3.py searches these
in precedence order:

    1. human_gen/       recordings from Drive     <- this script writes here
    2. machine_gen/     TTS output

Flattening is deliberate: stitch_mp3.py matches files by their leading sequence
number, so Drive's folder layout carries no information the pipeline needs.

What gets skipped
-----------------
A file is skipped when this project already has audio for that sequence number
in ANY of those directories -- regardless of extension, so an existing
16_YUDHISHTHIRA.m4a satisfies a remote 16-YUDHISHTHIRA.mp3. Files with no
leading sequence number (filler audio such as dice.mp3) are skipped only when a
file of the same name already exists.

Requires:  pip install gdown truststore

truststore is needed on this machine because Qualcomm's network terminates TLS
with an internal certificate authority. Python's own SSL default consults the
Windows certificate store and works, but gdown uses requests, which ships
certifi's fixed CA bundle and rejects the corporate certificate. truststore
points verification back at the OS store. See enable_system_certificates().
"""

import re
import sys
from pathlib import Path

import audio_common as common
from concat_backends import atomic_install, temp_path_for

# ============================================================
# CONFIGURATION
# ============================================================

# Optional default so the URL need not be pasted every run: drive.folder_url
# in dice-skit.json.
DRIVE_FOLDER_URL = common.SETTINGS["drive"]["folder_url"]

# Anonymous access by default: drive.use_cookies in dice-skit.json. Set
# cookies=1 on the command line to fall back to your browser session if Google
# starts throttling bulk anonymous downloads.
USE_COOKIES = common.SETTINGS["drive"]["use_cookies"]


USAGE = """\
Usage:
    python fetch_drive.py [FOLDER_URL] [dry=1] [overwrite=1] [limit=N] [cookies=1]

    FOLDER_URL   a Google Drive folder link or bare folder ID. Optional if
                 drive.folder_url is set in dice-skit.json.

    dry=1        list what would be fetched, download nothing
    overwrite=1  download even when the file is already present locally
    limit=N      stop after N downloads (useful for a trial run)
    cookies=1    authenticate with browser cookies instead of anonymously

Examples:
    python fetch_drive.py https://drive.google.com/drive/folders/1AbCdEf
    python fetch_drive.py https://drive.google.com/drive/folders/1AbCdEf dry=1
    python fetch_drive.py <url> limit=5\
"""


# ============================================================
# ARGUMENTS
# ============================================================

_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


def _flag(value, name):
    """
    Interpret a key=value flag as a boolean.
    """
    value = value.strip().lower()

    if value in _TRUE_WORDS:
        return True

    if value in _FALSE_WORDS:
        return False

    print(f'ERROR: {name} must be 0 or 1, got "{value}".')
    print()
    print(USAGE)
    sys.exit(1)


def parse_arguments(argv):
    """
    Parse the command line into (url, dry_run, overwrite, limit, cookies).

    Arguments are order-independent; the one bare token that is not a key=value
    pair is taken as the folder URL or ID.
    """
    url = DRIVE_FOLDER_URL
    dry_run = False
    overwrite = False
    limit = None
    cookies = USE_COOKIES

    for argument in argv[1:]:
        token = argument.strip()

        if not token:
            continue

        if token.lower() in ("-h", "--help", "/?", "help"):
            print(USAGE)
            sys.exit(0)

        key, separator, value = token.lstrip("-").partition("=")
        key = key.lower()

        # "drive.google.com/..." contains no '=' before the query string, and a
        # URL with a query is still not a flag, so only treat short bare keys
        # as flags.
        is_flag = separator and key in (
            "dry",
            "dry_run",
            "overwrite",
            "limit",
            "cookies",
        )

        if is_flag:
            if key in ("dry", "dry_run"):
                dry_run = _flag(value, "dry")

            elif key == "overwrite":
                overwrite = _flag(value, "overwrite")

            elif key == "cookies":
                cookies = _flag(value, "cookies")

            else:
                try:
                    limit = int(value)

                    if limit <= 0:
                        raise ValueError

                except ValueError:
                    print(
                        f"ERROR: limit must be a positive integer, got " f'"{value}".'
                    )
                    print()
                    print(USAGE)
                    sys.exit(1)

            continue

        if url is not None and url != DRIVE_FOLDER_URL:
            print(f'ERROR: more than one folder URL given ("{url}", "{token}").')
            print()
            print(USAGE)
            sys.exit(1)

        url = token

    if not url:
        print("ERROR: no Google Drive folder URL given.")
        print()
        print(USAGE)
        sys.exit(1)

    return url, dry_run, overwrite, limit, cookies


# Drive folder links come in a few shapes; a bare 20+ character ID is also
# accepted so the argument can be copied from either place.
_FOLDER_PATTERNS = (
    re.compile(r"/folders/([A-Za-z0-9_-]{10,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"),
    re.compile(r"^([A-Za-z0-9_-]{10,})$"),
)


def extract_folder_id(url):
    """
    Pull the folder ID out of a Drive URL, or return the input if it already
    looks like a bare ID.
    """
    text = str(url).strip().strip("/")

    for pattern in _FOLDER_PATTERNS:
        match = pattern.search(text)

        if match:
            return match.group(1)

    print(f'ERROR: could not find a Drive folder ID in "{url}".')
    print()
    print("Expected something like:")
    print("    https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp")
    print()
    print(USAGE)
    sys.exit(1)


# ============================================================
# LISTING
# ============================================================


def enable_system_certificates():
    """
    Make requests/urllib3 trust the operating system's certificate store.

    Needed on machines behind a TLS-inspecting corporate proxy. Python's own
    SSL default already consults the Windows certificate store, so urllib
    works, but requests -- and therefore gdown -- ships with certifi's fixed CA
    bundle, which does not contain a company's internal root CA. The result is:

        SSLError: certificate verify failed: self-signed certificate
        in certificate chain

    truststore redirects verification at the OS store, which does have it.
    Returns True when injection succeeded.
    """
    try:
        import truststore

    except ImportError:
        return False

    try:
        truststore.inject_into_ssl()

    except Exception:
        return False

    return True


def load_gdown():
    """
    Import gdown, with an actionable message when it is not installed.

    System certificates are enabled first, because gdown builds its HTTPS
    session at import time.
    """
    if not enable_system_certificates():
        print(
            "NOTE: truststore is not installed. If Drive access fails with a "
            "certificate error,"
        )
        print("      run: pip install truststore")

    try:
        import gdown

    except ImportError:
        print("ERROR: gdown is not installed.")
        print()
        print("Install it with:")
        print("    pip install gdown truststore")
        print()
        sys.exit(1)

    return gdown


def list_remote_audio(gdown, folder_id, cookies):
    """
    Crawl the Drive folder and return every audio file it contains.

    Returns (audio, skipped_other) lists of dicts with keys: id, remote_path,
    name.
    """
    print()
    print("=" * 70)
    print("LISTING DRIVE FOLDER")
    print("=" * 70)
    print(f"Folder ID : {folder_id}")
    print(f"Access    : {'browser cookies' if cookies else 'anonymous'}")
    print()
    print("Crawling (this walks every subdirectory)...")

    try:
        entries = gdown.download_folder(
            id=folder_id,
            skip_download=True,
            quiet=True,
            use_cookies=cookies,
        )

    except Exception as exc:
        print()
        print("ERROR: could not list the Drive folder.")
        print(f"  {exc}")
        print()
        print("Common causes:")
        print('  - the folder is not shared as "Anyone with the link"')
        print("  - the link points at a file rather than a folder")
        print("  - network or proxy blocking drive.google.com")
        print()
        print("Check by opening the link in a private browser window while")
        print("signed out. If it does not load, sharing is the problem.")
        sys.exit(1)

    if not entries:
        print()
        print("No files found in that folder.")
        print()
        print(
            "If the folder definitely has contents, it is probably not " "link-shared."
        )
        sys.exit(1)

    allowed = {ext.lower() for ext in common.INPUT_EXTENSIONS}

    audio = []
    other = []

    for entry in entries:
        # gdown returns namedtuples of (id, path, local_path); `path` is the
        # location inside the Drive folder, including subdirectories.
        remote_path = Path(str(entry.path))

        record = {
            "id": entry.id,
            "remote_path": remote_path,
            "name": remote_path.name,
        }

        if remote_path.suffix.lower() in allowed:
            audio.append(record)
        else:
            other.append(record)

    print()
    print(f"Files found      : {len(entries)}")
    print(f"Audio files      : {len(audio)}")
    print(f"Ignored (other)  : {len(other)}")

    if other:
        shown = other[:5]

        print()
        print("Ignored, not an accepted audio extension:")

        for record in shown:
            print(f"    {record['remote_path'].as_posix()}")

        if len(other) - len(shown) > 0:
            print(f"    ... and {len(other) - len(shown)} more")

    return audio, other


# ============================================================
# PLANNING
# ============================================================


def local_inventory():
    """
    What this project already has, across every search directory.

    Returns (sequences, names): the set of sequence numbers already satisfied
    anywhere, and the set of filenames already present anywhere. Both are used
    to decide what does not need downloading.
    """
    sequences = set()
    names = set()

    for directory in common.search_directories():
        for path in common.iter_candidate_files(directory):
            names.add(path.name.lower())

            sequence = common.sequence_of(path)

            if sequence is not None:
                sequences.add(sequence)

        # Named filler audio has no sequence number and is excluded from
        # iter_candidate_files() only by that rule, so pick it up separately.
        if directory.exists():
            for path in directory.iterdir():
                if path.is_file() and not path.name.startswith("_"):
                    names.add(path.name.lower())

    return sequences, names


def plan_downloads(audio, overwrite=False, limit=None):
    """
    Decide what to fetch.

    Returns (to_fetch, already_present, collisions).

    A numbered file is considered present when this project already has audio
    for that sequence number, whatever its extension. An unnumbered file --
    filler audio like dice.mp3 -- is present only when the same filename
    exists.
    """
    print()
    print("=" * 70)
    print("PLANNING")
    print("=" * 70)

    sequences, names = local_inventory()

    print(
        f"Searched               : "
        + ", ".join(d.name for d in common.search_directories())
    )
    print(f"Local sequence numbers : {len(sequences)}")
    print(f"Local files            : {len(names)}")
    print(f"Download destination   : {common.HUMAN_GEN_DIRECTORY}")

    to_fetch = []
    already_present = []
    collisions = []

    claimed_sequences = {}
    claimed_names = {}

    for record in sorted(audio, key=lambda r: r["remote_path"].as_posix()):
        sequence = common.sequence_of(record["name"])
        key = record["name"].lower()

        record["sequence"] = sequence
        record["target"] = common.HUMAN_GEN_DIRECTORY / record["name"]

        # Two remote files flattening onto the same local name, or claiming the
        # same sequence number, would silently overwrite one another.
        if key in claimed_names:
            record["conflict_with"] = claimed_names[key]
            collisions.append(record)
            continue

        if sequence is not None and sequence in claimed_sequences:
            record["conflict_with"] = claimed_sequences[sequence]
            collisions.append(record)
            continue

        claimed_names[key] = record

        if sequence is not None:
            claimed_sequences[sequence] = record

        if not overwrite:
            if sequence is not None and sequence in sequences:
                already_present.append(record)
                continue

            if sequence is None and key in names:
                already_present.append(record)
                continue

        to_fetch.append(record)

    if limit is not None and len(to_fetch) > limit:
        print()
        print(f"Limiting to the first {limit} of {len(to_fetch)} download(s).")
        to_fetch = to_fetch[:limit]

    print()
    print(f"To download            : {len(to_fetch)}")
    print(f"Already present        : {len(already_present)}")

    if collisions:
        print(f"Conflicting            : {len(collisions)}")

    if collisions:
        print()
        print(
            f"CONFLICTS ({len(collisions)}) - these remote files clash with "
            "another and were NOT queued:"
        )

        for record in collisions:
            print(f"    {record['remote_path'].as_posix()}")
            print(
                f"        clashes with "
                f"{record['conflict_with']['remote_path'].as_posix()}"
            )

    return to_fetch, already_present, collisions


# ============================================================
# DOWNLOADING
# ============================================================


def download_one(gdown, record, cookies):
    """
    Download a single Drive file into place.

    Written to a temporary sibling and validated before replacing anything, so
    an interrupted or corrupt transfer cannot leave a bad file where the
    pipeline will pick it up.
    """
    target = record["target"]
    temp_file = temp_path_for(target, "drive")

    if temp_file.exists():
        temp_file.unlink()

    gdown.download(
        id=record["id"],
        output=str(temp_file),
        quiet=True,
        use_cookies=cookies,
        resume=False,
    )

    if not temp_file.exists() or temp_file.stat().st_size == 0:
        if temp_file.exists():
            temp_file.unlink()

        raise RuntimeError("download produced no data")

    size = temp_file.stat().st_size

    # atomic_install validates before replacing.
    atomic_install(temp_file, target)

    return size


def run_downloads(gdown, to_fetch, cookies):
    """
    Download every queued file, reporting progress per file.

    Returns (succeeded, failed).
    """
    print()
    print("=" * 70)
    print("DOWNLOADING")
    print("=" * 70)
    print(f"Destination: {common.HUMAN_GEN_DIRECTORY}")

    succeeded = []
    failed = []
    total_bytes = 0

    for index, record in enumerate(to_fetch, start=1):
        print()
        print(f"[{index}/{len(to_fetch)}] {record['remote_path'].as_posix()}")

        try:
            size = download_one(gdown, record, cookies)

        except Exception as exc:
            print(f"    FAILED: {exc}")
            record["problem"] = str(exc)
            failed.append(record)
            continue

        total_bytes += size

        duration = common.get_audio_duration(record["target"])

        print(
            f"    -> {record['target'].name}  "
            f"{size / 1024:.0f} KB  "
            f"{common.format_duration(duration)}"
        )

        succeeded.append(record)

    print()
    print(f"Downloaded : {len(succeeded)} file(s), {total_bytes / 1048576:.2f} MB")

    if failed:
        print(f"Failed     : {len(failed)}")

        for record in failed:
            print(f"    {record['remote_path'].as_posix()}: {record['problem']}")

    return succeeded, failed


# ============================================================
# MAIN
# ============================================================


def main():

    print()
    print("=" * 70)
    print("GOOGLE DRIVE AUDIO FETCHER")
    print("=" * 70)

    url, dry_run, overwrite, limit, cookies = parse_arguments(sys.argv)

    if dry_run:
        print("Mode: DRY RUN - nothing will be downloaded")

    if overwrite:
        print("Mode: OVERWRITE - existing files will be re-fetched")

    gdown = load_gdown()

    folder_id = extract_folder_id(url)

    common.OUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    common.HUMAN_GEN_DIRECTORY.mkdir(parents=True, exist_ok=True)

    audio, _other = list_remote_audio(gdown, folder_id, cookies)

    if not audio:
        print()
        print(
            "No audio files in that folder. Accepted extensions: "
            + ", ".join(common.INPUT_EXTENSIONS)
        )

        return 1

    to_fetch, already_present, collisions = plan_downloads(
        audio,
        overwrite=overwrite,
        limit=limit,
    )

    if dry_run:
        print()
        print("=" * 70)
        print("WOULD DOWNLOAD")
        print("=" * 70)

        if not to_fetch:
            print("Nothing - everything is already present locally.")
        else:
            for record in to_fetch:
                sequence = record["sequence"]
                label = "seq " + str(sequence) if sequence is not None else "filler"

                print(
                    f"    {label:<12} {record['remote_path'].as_posix()}"
                    f"  ->  {record['target'].name}"
                )

        print()
        print(f"{len(to_fetch)} file(s) would be downloaded.")
        print("Re-run without dry=1 to fetch them.")
        print()

        return 0

    if not to_fetch:
        print()
        print("=" * 70)
        print("NOTHING TO DO")
        print("=" * 70)
        print("Every audio file in the Drive folder is already present locally.")
        print("Use overwrite=1 to re-fetch anyway.")
        print()

        return 0

    succeeded, failed = run_downloads(gdown, to_fetch, cookies)

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
    print(f"Downloaded      : {len(succeeded)}")
    print(f"Already present : {len(already_present)}")

    if collisions:
        print(f"Conflicts       : {len(collisions)} (not downloaded)")

    if failed:
        print(f"Failed          : {len(failed)}")

    print()
    print("Next step:")
    print("    python stitch_mp3.py strict=0")
    print()

    return 1 if failed else 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:
        print()
        print("Interrupted by user.")
        sys.exit(1)

    except Exception as exc:
        print()
        print("=" * 70)
        print("ERROR")
        print("=" * 70)
        print(exc)
        print()
        sys.exit(1)
