"""
Stitch the dialogue MP3s in dialogues-mp3/ into one master file.

This is the runnable half of the pipeline. It assumes the per-dialogue MP3s
already exist; it never calls Sarvam, never reads .env, and needs no API key.
TTS generation lives in generate_mp3.py and is currently disabled.

    python stitch_mp3.py                  # stitch everything, strict
    python stitch_mp3.py 100              # only the first 100 dialogue rows
    python stitch_mp3.py strict=0         # stitch despite missing/bad inputs
    python stitch_mp3.py 10 strict=0      # both, in either order
    python stitch_mp3.py dry_run=1        # run every check, stitch nothing
Arguments are optional and order-independent. `strict=1` (the default, and the
value of run.strict in dice-skit.json) aborts before writing anything if
any expected dialogue MP3 is missing, corrupt, or in a mismatched audio format.
`strict=0` skips those inputs and stitches the rest, reporting what it left out.

Four mandatory checks abort regardless of `strict`, with no override: the CSV
is missing, a file's character name differs from its row, a file has no
character name, or two files claim one take in the same directory. These guard
the immutable take-ID scheme in section 9 of docs/audio-concat-study.md.

`dry_run=1` runs every check, including normalization, then stops before
stitching. `strict` is ignored. It exits non-zero if a mandatory check failed.
It does write to work/ (normalized cache, generated fillers) -- only the master
is guaranteed untouched.

Output sequence: each CSV row contributes a filler, its dialogue, and
optionally a trailing filler, in row order.

    filler_before(row 0)   e.g. new-mahabharata-instrumental.m4a
    dialogue 0             0-SAMAY.mp3
    filler_after(row 0)    e.g. long_filler.mp3   (only when named)
    filler_before(row 1)   empty cell -> short_filler.mp3
    dialogue 1
                           row 1 names no filler_after -> nothing
                           row 2 has filler_before "N" -> no gap here
    dialogue 2
    ...

The "filler_before" CSV column names the audio to place before that row. An
empty cell means the default 2-second silence, and a cell holding "N" means no
leading audio at all, so that row's dialogue follows the previous row directly.
There is no separate intro setting: the opening music is simply row 0's
filler_before.

The "filler_after" column names audio to place after that row, and its empty
cell rule is the opposite: blank means nothing is appended at all. Only a named
file is ever added, which is what keeps the master from ending on a gap. Either
column may name a generated filler (short_filler.mp3, long_filler.mp3) and it
will be synthesised on demand; "N" is accepted in either, though in
"filler_after" it is merely a louder way of leaving the cell empty.

Every dialogue line is first brought to one loudness (loudness.py; turn it
off for a run with loudness=0). Settings come from dice-skit.json through
settings.py.

A row may also name a background track in "background_score", with a cue
string in "score_cues" saying which track is prominent when. The row's dialogue
is then replaced by the mix; see background_score.py.

Concatenation is delegated to a backend -- FFmpeg's concat demuxer, or a
dependency-free MPEG frame splicer when FFmpeg is not installed. See
concat_backends.py and docs/audio-concat-study.md.
"""

import sys
from pathlib import Path

import audio_common as common
import background_score
import loudness
import settings
import concat_backends
from concat_backends import select_backend, temp_path_for

# ============================================================
# INPUT DISCOVERY
# ============================================================


def resolve_from_csv(row_limit=None):
    """
    Resolve dialogue audio paths from the CSV, which defines both the set and
    the order.

    Files are matched on sequence number alone, so a row for KRISHNA is
    satisfied by `129-KRISHNA.mp3`, `129_Krishna.m4a`, or any other extension
    in common.INPUT_EXTENSIONS. Whether the character name agrees is checked
    afterwards, by mandatory_checks().

    Returns a list of dicts: {path, label, record, alternatives, collisions}.
    """
    records = common.load_csv(row_limit=row_limit)

    if not records:
        raise RuntimeError(f"No usable dialogue rows in {common.CSV_FILE.name}.")

    resolved = []

    for record in records:
        path, alternatives, source, collisions = common.find_dialogue_file(record)

        expected = common.dialogue_path(record).name

        resolved.append(
            {
                "path": path,
                "source": source,
                "expected": expected,
                "label": (
                    f"row {record['row_number']} / "
                    f"{path.name if path else expected}"
                ),
                "record": record,
                "alternatives": alternatives,
                "collisions": collisions,
            }
        )

    return resolved


def resolve_from_glob(row_limit=None):
    """
    Resolve dialogue audio paths by scanning the search directories.

    Used when the CSV is absent. Accepts every extension in
    common.INPUT_EXTENSIONS and excludes the master, the default filler,
    and any underscore-prefixed temporary or cache directory. Files with no
    leading sequence number are skipped, which is what keeps named filler
    audio out of the dialogue set.

    Directory precedence matches find_dialogue_file(): a sequence number found
    in human_gen/ is never overridden from machine_gen/.
    """
    directories = common.search_directories()

    # Directory order is precedence order, so record where each file came from
    # and keep the first directory that claims a sequence number.
    by_sequence = {}
    unnumbered = []

    for directory in directories:
        for path in common.iter_candidate_files(directory):
            sequence = common.sequence_of(path)

            if sequence is None:
                unnumbered.append(path)
                continue

            by_sequence.setdefault(sequence, []).append((directory, path))

    if unnumbered:
        print()
        print(
            f"Skipping {len(unnumbered)} file(s) with no leading sequence "
            "number (filler audio and other non-dialogue material):"
        )

        for path in unnumbered:
            print(f"    {path.name}")

    resolved = []

    for key in sorted(by_sequence):
        group = by_sequence[key]

        # First directory wins; newest wins within it.
        winning_directory = group[0][0]

        same = sorted(
            (p for d, p in group if d == winning_directory),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        others = [p for d, p in group if d != winning_directory]

        resolved.append(
            {
                "path": same[0],
                "source": Path(winning_directory),
                "expected": same[0].name,
                "label": same[0].name,
                "record": None,
                "alternatives": same[1:] + others,
                "collisions": same[1:],
            }
        )

    if row_limit is not None:
        resolved = resolved[:row_limit]

    return resolved


def resolve_inputs(row_limit=None):
    """
    Decide which dialogue MP3s to stitch, and in what order.

    The CSV is preferred because it is the authoritative running order. When
    it is missing, fall back to a numerically sorted scan of the directory --
    but only so the rest of the report can still be produced. A missing CSV is
    a mandatory-check failure (see mandatory_checks()), so the glob order is
    never stitched.
    """
    print()
    print("=" * 70)
    print("RESOLVING INPUTS")
    print("=" * 70)

    if common.CSV_FILE.exists():
        print(f"Manifest : {common.CSV_FILE.name}")

        resolved = resolve_from_csv(row_limit=row_limit)

        print(f"Order    : CSV row order ({len(resolved)} dialogue rows)")

    else:
        print(f"Manifest : none ({common.CSV_FILE.name} not found)")

        resolved = resolve_from_glob(row_limit=row_limit)

        print(
            f"Order    : numeric by leading sequence number "
            f"({len(resolved)} files found)"
        )

    if row_limit is not None:
        print(f"Row limit: {row_limit}")

    return resolved


# ============================================================
# PRE-FLIGHT
# ============================================================


def preflight(resolved):
    """
    Verify every input exists and decodes before any stitching begins.

    Under strict mode the caller aborts on any problem; otherwise
    the bad entries are dropped and the rest is stitched.

    Returns (usable, missing, corrupt).
    """
    print()
    print("=" * 70)
    print("PRE-FLIGHT")
    print("=" * 70)

    usable = []
    missing = []
    corrupt = []

    total = len(resolved)

    for index, entry in enumerate(resolved, start=1):
        path = entry["path"]

        if path is None or not path.exists():
            missing.append(entry)
            continue

        # verbose=False: one consolidated report is printed below instead of
        # a wall of ffmpeg diagnostics per file.
        if not common.is_valid_audio(path, verbose=False):
            corrupt.append(entry)
            continue

        usable.append(entry)

    print(f"Checked  : {total} expected dialogue file(s)")
    print(f"Usable   : {len(usable)}")

    # Where the usable files came from, so a run makes clear how much is
    # Which source each usable row resolved to. A per-row breakdown goes to the
    # run log; the console gets counts plus the fallback list.
    by_source = {}

    for entry in usable:
        source = entry.get("source")

        if source is not None:
            by_source.setdefault(source, []).append(entry)

    for directory in common.search_directories():
        found = by_source.get(directory, [])
        print(f"  from {directory.name:<16}: {len(found)}")

    # Rows that fell through to a lower-precedence source are worth naming:
    # those are the lines still waiting on a real recording.
    for directory in common.search_directories()[1:]:
        found = by_source.get(directory, [])

        if not found:
            continue

        higher = ", ".join(
            d.name
            for d in common.search_directories()[
                : common.search_directories().index(directory)
            ]
        )

        print()
        print(
            f"FELL BACK TO {directory.name.upper()} ({len(found)}) - "
            f"not in {higher}:"
        )

        for entry in found:
            print(f"    {entry['path'].name}")

    # Surface files that satisfied a row under a non-canonical name, and any
    # slot that more than one file claimed.
    renamed = [
        entry
        for entry in usable
        if entry.get("expected") and entry["path"].name != entry["expected"]
    ]

    duplicated = [entry for entry in usable if entry.get("alternatives")]

    if renamed:
        print()
        print(
            f"MATCHED BY SEQUENCE NUMBER ({len(renamed)}) - filename differs "
            "from the CSV convention:"
        )

        for entry in renamed:
            print(f"    {entry['expected']:<28} <- {entry['path'].name}")

    if duplicated:
        print()
        print(
            f"MULTIPLE FILES PER SEQUENCE ({len(duplicated)}) - "
            "directory precedence, then newest:"
        )

        for entry in duplicated:
            print(f"    using {entry['path'].name}  ({entry['source'].name})")

            for other in entry["alternatives"]:
                print(f"      ignoring {other.name}  ({other.parent.name})")

    if missing:
        print()
        print(f"MISSING ({len(missing)}):")

        searched = ", ".join(d.name for d in common.search_directories())

        for entry in missing:
            print(f"    {entry['label']}")
            common.warn(f"no audio for {entry['label']} - searched {searched}")

    if corrupt:
        print()
        print(f"CORRUPT ({len(corrupt)}):")

        for entry in corrupt:
            print(f"    {entry['label']}")
            common.warn(f"corrupt audio: {entry['path']}")

    log_row_sources(resolved)

    return usable, missing, corrupt


def log_row_sources(resolved):
    """
    Write a per-row manifest to the run log naming the file chosen for each
    sequence number and which directory it came from.

    Log-only: at 154 rows this would swamp the console, but it is exactly what
    you want when auditing which take ended up in the master.
    """
    common.log_only()
    common.log_only("=" * 70)
    common.log_only("ROW SOURCES")
    common.log_only("=" * 70)
    common.log_only(f"{'seq':>5}  {'source':<12}  {'file':<34}  status")
    common.log_only("-" * 70)

    counts = {}

    for entry in resolved:
        record = entry.get("record") or {}
        sequence = record.get("sequence_number", "?")
        path = entry.get("path")
        source = entry.get("source")

        if path is None:
            source_name = "-"
            file_name = entry.get("expected", "?")
            status = "MISSING"
        else:
            source_name = source.name if source else "?"
            file_name = path.name
            status = "ok"

        counts[source_name] = counts.get(source_name, 0) + 1

        common.log_only(
            f"{str(sequence):>5}  {source_name:<12}  {file_name:<34}  {status}"
        )

    common.log_only("-" * 70)

    for name in sorted(counts):
        label = "not found" if name == "-" else name
        common.log_only(f"  {label:<14} {counts[name]}")


# ============================================================
# MANDATORY CHECKS
# ============================================================


def mandatory_checks(resolved):
    """
    The four checks that abort a run regardless of `strict`.

    Each catches a way of producing a complete, plausible-sounding master that
    is nonetheless wrong -- the wrong actor on a line, or lines in the wrong
    order. `strict=0` means "build what exists despite gaps", which is no
    defence against inputs that are present but mispaired, so these bypass it.
    There is deliberately no override. See section 9 of
    docs/audio-concat-study.md.

        1. dialogues_new.csv is absent, so the order would be numeric.
        2. A file's character name differs from its CSV row's.
        3. A file carries no character name at all, so 2 cannot be checked.
        4. More than one file claims a take within the winning directory.

    Every row is checked before anything is reported, so one run surfaces
    every fault. Nothing here feeds the `problems` counter; the caller gates
    on the returned list independently of `strict`.

    Returns a list of (title, count), one per failing check.
    """
    print()
    print("=" * 70)
    print("MANDATORY CHECKS")
    print("=" * 70)

    csv_missing = not common.CSV_FILE.exists()

    mismatched = []
    unnamed = []
    collided = []
    seen_takes = set()

    for entry in resolved:
        path = entry.get("path")

        if path is None or not path.exists():
            continue  # already reported as MISSING

        token = common.character_token(path)
        record = entry.get("record")

        if not token:
            unnamed.append(entry)

        elif record is not None and token != common.normalize_character(
            record["character_name"]
        ):
            mismatched.append(entry)

        # Two CSV rows naming the same take would otherwise list it twice.
        take = (entry.get("source"), common.sequence_of(path))

        if entry.get("collisions") and take not in seen_takes:
            seen_takes.add(take)
            collided.append(entry)

    fatal = []

    if csv_missing:
        fatal.append(("manifest missing", 1))

        print()
        print(f"MANIFEST MISSING - {common.CSV_FILE.name} not found:")
        print("    Without it the running order falls back to numeric order of")
        print("    the take IDs, which is not script order once IDs are frozen.")
        common.warn(
            f"{common.CSV_FILE.name} not found; refusing to stitch in "
            "numeric take-ID order"
        )

    if mismatched:
        fatal.append(("character mismatch", len(mismatched)))

        print()
        print(
            f"CHARACTER MISMATCH ({len(mismatched)}) - file's character "
            "differs from the CSV row:"
        )

        for entry in mismatched:
            wanted = entry["record"]["character_name"] or "(blank)"
            row = entry["record"]["row_number"]

            print(
                f"    row {row:<4} CSV {wanted:<16} <- "
                f"{entry['path'].name}  ({entry['source'].name})"
            )
            common.warn(
                f"character mismatch: row {row} is {wanted} but resolved to "
                f"{entry['path']}"
            )

    if unnamed:
        fatal.append(("no character in filename", len(unnamed)))

        print()
        print(
            f"NO CHARACTER IN FILENAME ({len(unnamed)}) - cannot be checked "
            "against the CSV:"
        )

        for entry in unnamed:
            print(f"    {entry['label']:<34} ({entry['source'].name})")
            common.warn(f"no character name in filename: {entry['path']}")

    if collided:
        fatal.append(("take-id collision", len(collided)))

        print()
        print(
            f"TAKE-ID COLLISION ({len(collided)}) - more than one file for "
            "one take in the same directory:"
        )

        for entry in collided:
            take = common.sequence_of(entry["path"])
            names = [entry["path"]] + list(entry["collisions"])

            print(f"    take {take} in {entry['source'].name}:")

            for other in names:
                print(f"        {other.name}")

            common.warn(
                f"take {take} has {len(names)} files in {entry['source']}: "
                + ", ".join(p.name for p in names)
            )

    if fatal:
        print()
        print(
            f"FAILED   : {sum(count for _, count in fatal)} exception(s) in "
            f"{len(fatal)} mandatory check(s) - these abort regardless of "
            "strict."
        )
    else:
        print(
            "Passed   : manifest present, character names agree, one file " "per take"
        )

    return fatal


def first_audio_header(path):
    """
    Return the FrameHeader of the first real audio frame in an MP3, skipping
    any Xing/Info seek header. None when the file yields no frames.
    """
    path = Path(path)

    if path.suffix.lower() != ".mp3":
        return None

    try:
        data = path.read_bytes()
    except OSError:
        return None

    for offset, header in common.iter_frames(data):
        if common.is_seek_header_frame(data, offset, header):
            continue

        return header

    return None


def survey_formats(paths):
    """
    Group inputs by MPEG frame format.

    Concatenating by stream copy -- whether through FFmpeg's concat demuxer or
    the pure-Python splicer -- requires every input to share one format. A
    mixed-format result is not merely suboptimal: sample rate is stored per
    frame, so a joined stream that switches rate midway is mis-timed by many
    players and its total duration is computed wrongly.

    Returns {format_key: [paths]}, insertion-ordered by first appearance.
    """
    groups = {}

    for path in paths:
        header = first_audio_header(path)

        if header is None:
            continue

        groups.setdefault(
            common.format_key(header),
            {"header": header, "paths": []},
        )[
            "paths"
        ].append(Path(path))

    return groups


def report_format_survey(groups):
    """
    Print the format survey. Returns True when the inputs are consistent.
    """
    if len(groups) <= 1:
        if groups:
            only = next(iter(groups.values()))
            print(
                f"Format   : {common.describe_format(only['header'])} "
                f"(consistent across {len(only['paths'])} file(s))"
            )

        return True

    print()
    print(f"FORMAT MISMATCH ({len(groups)} distinct formats):")

    # Largest group first: that is almost certainly the intended format.
    ordered = sorted(
        groups.values(),
        key=lambda group: len(group["paths"]),
        reverse=True,
    )

    for index, group in enumerate(ordered):
        role = "majority" if index == 0 else "outlier"

        print()
        print(
            f"    [{role}] {common.describe_format(group['header'])} "
            f"- {len(group['paths'])} file(s)"
        )

        shown = group["paths"][:5]

        for path in shown:
            print(f"        {path.name}")

        remaining = len(group["paths"]) - len(shown)

        if remaining > 0:
            print(f"        ... and {remaining} more")

    print()
    print("    Sample rate and channel mode are stored per MPEG frame, so a")
    print("    stream-copy concatenation of mixed formats produces a file that")
    print("    many players mis-time and whose reported duration is wrong.")
    print()
    print("    Fix one of these ways:")
    print("      - re-encode the outliers to the majority format, e.g.")
    print("        ffmpeg -i outlier.mp3 -ar 24000 -ac 1 -b:a 128k fixed.mp3")
    print("      - check TARGET_SAMPLE_RATE / TARGET_BITRATE in " "audio_common.py")

    return False


def resolve_row_fillers(entries):
    """
    Resolve each entry's "filler_before" and "filler_after" audio.

    Sets entry["filler_source"] to the path that should precede that entry's
    dialogue, or None when the row's "filler_before" cell says "N" -- that row
    then follows the previous one with no gap. A missing named filler falls back
    to SHORT_FILLER and is reported, so a typo or an undelivered sound effect
    degrades to a plain gap rather than failing the run outright.

    Sets entry["filler_after_source"] to the path that should follow it, or
    None when the CSV leaves "filler_after" empty -- which is the common case
    and not a problem. A named-but-absent trailing filler is reported and
    appends nothing: unlike the leading filler there is no sensible default to
    fall back to, and inserting silence would invent a gap the CSV never asked
    for.

    Returns (named, missing, after_named, after_missing, suppressed).
    """
    named = []
    missing = []
    after_named = []
    after_missing = []
    suppressed = []

    for entry in entries:
        record = entry.get("record")

        if record is None:
            entry["filler_source"] = common.SHORT_FILLER
            entry["filler_after_source"] = None
            continue

        path, is_default = common.filler_for(record)

        if is_default:
            entry["filler_source"] = common.SHORT_FILLER

        elif path is None:
            # The cell said "N": no leading filler for this row.
            entry["filler_source"] = None
            suppressed.append(entry)

        elif not path.exists() or not common.is_valid_audio(path, verbose=False):
            entry["filler_source"] = common.SHORT_FILLER
            entry["filler_problem"] = path
            missing.append(entry)

        else:
            entry["filler_source"] = path
            named.append(entry)

        # --- trailing filler -------------------------------------------
        after_path, found = common.filler_after_for(record)

        if after_path is None:
            entry["filler_after_source"] = None
            continue

        if not found or not common.is_valid_audio(after_path, verbose=False):
            entry["filler_after_source"] = None
            entry["filler_after_problem"] = after_path
            after_missing.append(entry)
            continue

        entry["filler_after_source"] = after_path
        after_named.append(entry)

    return named, missing, after_named, after_missing, suppressed


def report_row_fillers(entries, named, missing, after_named, after_missing, suppressed):
    """
    Summarise which filler precedes and which follows each row.
    """
    print()
    print("=" * 70)
    print("ROW FILLERS")
    print("=" * 70)

    default_count = len(entries) - len(named) - len(suppressed)

    print(f"Default ({common.SHORT_FILLER.name}) : {default_count} row(s)")
    print(f"Named in {common.FILLER_COLUMN:<19}: {len(named)} row(s)")
    print(f"Suppressed with \"N\"{'':<9}: {len(suppressed)} row(s)")
    print(f"Named in {common.FILLER_AFTER_COLUMN:<19}: {len(after_named)} row(s)")
    print(f"No trailing filler{'':<10}: " f"{len(entries) - len(after_named)} row(s)")

    if named:
        print()

        for entry in named:
            print(
                f"    before {entry['label']:<34} " f"<- {entry['filler_source'].name}"
            )

    if suppressed:
        print()

        for entry in suppressed:
            print(f"    before {entry['label']:<34} <- (none)")

    if after_named:
        print()

        for entry in after_named:
            print(
                f"    after  {entry['label']:<34} "
                f"-> {entry['filler_after_source'].name}"
            )

    if missing:
        print()
        print(
            f"NAMED FILLER NOT FOUND ({len(missing)}) - falling back to "
            f"{common.SHORT_FILLER.name}:"
        )

        for entry in missing:
            print(f"    {entry['label']:<34} wanted " f"{entry['filler_problem'].name}")
            common.warn(
                f"filler_before \"{entry['filler_problem'].name}\" not found "
                f"for {entry['label']}; used {common.SHORT_FILLER.name}"
            )

    if after_missing:
        print()
        print(
            f"NAMED TRAILING FILLER NOT FOUND ({len(after_missing)}) - "
            "nothing appended:"
        )

        for entry in after_missing:
            print(
                f"    {entry['label']:<34} wanted "
                f"{entry['filler_after_problem'].name}"
            )
            common.warn(
                f"filler_after \"{entry['filler_after_problem'].name}\" not "
                f"found for {entry['label']}; appended nothing"
            )


# ============================================================
# DIALOGUE LOUDNESS
# ============================================================


def apply_loudness(entries):
    """
    Bring every row's dialogue to DIALOGUE_TARGET_LUFS.

    Sets entry["leveled_path"] on success; the background-score mix and
    normalization then use that file. Dialogue only: fillers and background
    scores are never touched. A line that cannot be measured keeps its own
    level and counts as an ordinary problem. See section 11 of
    docs/audio-concat-study.md.

    Returns (leveled, failed).
    """
    print()
    print("=" * 70)
    print("LOUDNESS")
    print("=" * 70)
    print(
        f"Target      : {common.DIALOGUE_TARGET_LUFS:g} LUFS, true peak "
        f"<= {common.DIALOGUE_TRUE_PEAK_DBFS:g} dBFS (dialogue only)"
    )

    leveled = []
    failed = []
    counts = {"leveled": 0, "cached": 0, "unchanged": 0}

    if not common.have_ffmpeg():
        for entry in entries:
            entry["loudness_problem"] = "FFmpeg was not found on PATH"
            failed.append(entry)

    else:
        for entry in entries:
            try:
                result = loudness.level(entry["path"])

            except loudness.LoudnessError as exc:
                entry["loudness_problem"] = str(exc)
                failed.append(entry)
                continue

            entry["leveled_path"] = result["path"]
            entry["loudness"] = result
            counts[result["action"]] += 1
            leveled.append(entry)

    print(f"Lines       : {len(leveled)} at target")
    print(f"  adjusted  : {counts['leveled']}")
    print(f"  from cache: {counts['cached']}")
    print(f"  unchanged : {counts['unchanged']} (already within 0.1 dB)")

    if leveled:
        measured = [e["loudness"]["measured"] for e in leveled]

        print(
            f"Before      : {min(measured):.1f} to {max(measured):.1f} LUFS, "
            f"a spread of {max(measured) - min(measured):.1f} dB"
        )
        print(f"Cache       : {common.LOUDNESS_DIRECTORY}")

    # The full per-line table goes to the run log; at 154 rows it would bury
    # the console.
    common.log_only()
    common.log_only("LOUDNESS PER LINE")
    common.log_only(
        f"    {'line':<34} {'measured':>9} {'peak':>7} {'gain':>7} {'limited':>8}"
    )

    for entry in leveled:
        r = entry["loudness"]
        common.log_only(
            f"    {entry['label']:<34} {r['measured']:>8.1f}  {r['peak']:>+6.1f} "
            f"{r['gain']:>+6.1f}  {r['limited']:>6.1f}"
        )

    limited = [e for e in leveled if e["loudness"]["limited"] > 0]
    short = [e for e in leveled if e["loudness"]["short"]]

    if limited:
        print()
        print(
            f"PEAK-LIMITED ({len(limited)}) - the gain would have pushed peaks "
            f"past {common.DIALOGUE_TRUE_PEAK_DBFS:g} dBFS:"
        )

        for entry in limited:
            r = entry["loudness"]
            print(
                f"    {entry['label']:<34} gain {r['gain']:+.1f} dB, "
                f"limited {r['limited']:.1f} dB"
            )
            common.warn(
                f"loudness: {entry['label']} gained {r['gain']:+.1f} dB and "
                f"peak-limited by {r['limited']:.1f} dB"
            )

    clipped = [e for e in leveled if e["loudness"]["peak"] > 0]

    if clipped:
        print()
        print(
            f"ALREADY CLIPPED ({len(clipped)}) - the source peaks above 0 dBFS; "
            "turning it down cannot remove distortion already there:"
        )

        for entry in clipped:
            print(
                f"    {entry['label']:<34} peak {entry['loudness']['peak']:+.1f} dBFS"
            )
            common.warn(
                f"loudness: {entry['label']} source is clipped "
                f"(peak {entry['loudness']['peak']:+.1f} dBFS)"
            )

    if short:
        print()
        print(
            f"SHORT LINES ({len(short)}) - under {loudness.SHORT_LINE_SECONDS:g}s, "
            "so the measurement and gain may be imprecise:"
        )

        for entry in short:
            print(f"    {entry['label']}")
            common.warn(f"loudness: {entry['label']} is too short to measure reliably")

    if failed:
        print()
        print(f"LOUDNESS NOT APPLIED ({len(failed)}) - stitched at the original level:")

        for entry in failed:
            print(f"    {entry['label']}")
            print(f"        {entry['loudness_problem']}")
            common.warn(
                f"loudness not applied to {entry['label']}: "
                f"{entry['loudness_problem']}"
            )

    return leveled, failed


# ============================================================
# BACKGROUND SCORE
# ============================================================


def apply_background_scores(entries):
    """
    Mix each row that names a background score with its dialogue.

    Sets entry["mixed_path"] on success; normalization then stitches that file
    in place of the bare dialogue. A row whose mix fails keeps its plain
    dialogue, so a fault leaves the master missing a score rather than missing
    a line -- which is why these are ordinary problems under `strict`, not
    mandatory checks. See section 10 of docs/audio-concat-study.md.

    Returns (mixed, failed, warned).
    """
    requested = []
    failed = []

    for entry in entries:
        record = entry.get("record")

        if record is None:
            continue

        try:
            request = background_score.score_request(record)
        except background_score.CueError as exc:
            entry["score_problem"] = str(exc)
            failed.append(entry)
            continue

        if request is not None:
            requested.append((entry, request))

    if not requested and not failed:
        return [], [], []

    print()
    print("=" * 70)
    print("BACKGROUND SCORE")
    print("=" * 70)
    print(
        f"Faded track : {common.SCORE_DUCK_DB:+g} dB, "
        f"{common.SCORE_RAMP_SECONDS:g}s ramps, loudness match "
        f"{'on' if common.SCORE_MATCH_LOUDNESS else 'off'}"
    )

    mixed = []
    warned = []
    counts = {"mixed": 0, "cached": 0}

    for entry, (name, cues) in requested:
        background = common.find_named_audio(name)

        if background is None:
            entry["score_problem"] = (
                f'background "{name}" not found in '
                + ", ".join(d.name for d in common.search_directories())
                + f", {common.WORK_DIRECTORY.name}"
            )
            failed.append(entry)
            continue

        try:
            result = background_score.mix(
                entry.get("leveled_path") or entry["path"], background, cues
            )

        except (background_score.CueError, background_score.MixError) as exc:
            entry["score_problem"] = str(exc)
            failed.append(entry)
            continue

        entry["mixed_path"] = result["path"]
        entry["score"] = result
        entry["score_name"] = background.name
        counts[result["action"]] += 1
        mixed.append(entry)

        if result["warnings"]:
            warned.append(entry)

    print(f"Rows mixed  : {len(mixed)}")
    print(f"  mixed now : {counts['mixed']}")
    print(f"  from cache: {counts['cached']}")

    if mixed:
        print(f"Cache       : {common.MIXED_DIRECTORY}")
        print()

        for entry in mixed:
            score = entry["score"]

            print(
                f"    {entry['label']:<34} + {entry['score_name']}  "
                f"[{entry['record']['score_cues']}]  "
                f"{common.format_duration(score['total'])}"
            )
            print(
                "        "
                + background_score.describe_cues(score["together"], score["segments"])
            )

    if warned:
        print()
        print(f"CUE STRING LONGER THAN THE AUDIO ({len(warned)}) - mixed anyway:")

        for entry in warned:
            print(f"    {entry['label']}")

            for message in entry["score"]["warnings"]:
                print(f"        {message}")
                common.warn(f"background score for {entry['label']}: {message}")

    if failed:
        print()
        print(
            f"BACKGROUND SCORE FAILED ({len(failed)}) - stitching the plain "
            "dialogue:"
        )

        for entry in failed:
            print(f"    {entry['label']}")
            print(f"        {entry['score_problem']}")
            common.warn(
                f"background score for {entry['label']} not applied: "
                f"{entry['score_problem']}"
            )

    return mixed, failed, warned


# ============================================================
# NORMALIZATION
# ============================================================


def normalize_entries(entries, target):
    """
    Bring every input into the target audio format.

    Covers both the dialogue audio and the per-row filler audio, since a filler
    named in the CSV can be any format too -- the opening instrumental arrives
    as 44.1 kHz stereo AAC.

    Files already in the target format are passed through untouched, so the
    common case stays on the lossless stream-copy path. Anything else is
    transcoded once into the _normalized cache and reused on later runs. Each
    distinct source is converted only once even when many rows reference it.

    Sets entry["stitch_path"], entry["filler_path"], and
    entry["filler_after_path"] on success. The filler paths are None for rows
    with no leading filler ("N") and no trailing filler respectively.
    Returns (ok, failed).
    """
    print()
    print("=" * 70)
    print("NORMALIZING")
    print("=" * 70)
    print(f"Target format: {common.describe_format(target)}")

    ok = []
    failed = []
    counts = {"pass-through": 0, "cached": 0, "transcoded": 0}

    # source path -> normalized path, so a filler shared by 150 rows is
    # converted once.
    resolved = {}

    def resolve(source):
        key = Path(source).resolve()

        if key not in resolved:
            path, action = concat_backends.normalize_input(
                source,
                header=target,
            )
            resolved[key] = path
            counts[action] += 1

        return resolved[key]

    for entry in entries:
        try:
            # A row mixed with a background score stitches the mix, and a
            # loudness-matched row its leveled copy, instead of the bare
            # dialogue. Both are already in target format.
            entry["stitch_path"] = resolve(
                entry.get("mixed_path") or entry.get("leveled_path") or entry["path"]
            )

            filler_source = entry.get("filler_source")
            entry["filler_path"] = resolve(filler_source) if filler_source else None

            after_source = entry.get("filler_after_source")
            entry["filler_after_path"] = resolve(after_source) if after_source else None

        except concat_backends.NormalizationError as exc:
            entry["problem"] = str(exc)
            failed.append(entry)
            continue

        ok.append(entry)

    print()
    print(f"Distinct source files    : {len(resolved)}")
    print(f"Already in target format : {counts['pass-through']}")
    print(f"Reused from cache        : {counts['cached']}")
    print(f"Transcoded now           : {counts['transcoded']}")

    if counts["cached"] or counts["transcoded"]:
        print(f"Cache directory          : {common.NORMALIZED_DIRECTORY}")

    if failed:
        print()
        print(f"CONVERSION FAILED ({len(failed)}):")

        for entry in failed:
            print(f"    {entry['label']}")
            first_line = str(entry.get("problem", "")).splitlines()[:1]

            for line in first_line:
                print(f"        {line}")

            common.warn(
                f"could not convert {entry['path'].name} to the target "
                f"format: {first_line[0] if first_line else 'unknown error'}"
            )

    return ok, failed


# ============================================================
# FILLERS
# ============================================================


def ensure_filler(backend, target_path, seconds, reference):
    """
    Create or repair a filler file.

    An existing filler is reused only when it is both valid AND already in the
    required format -- a filler left over from a run with a different target
    would silently break the splice.
    """
    if target_path.exists():
        existing = first_audio_header(target_path)

        if existing is None or not common.is_valid_audio(target_path, verbose=False):
            print(f"Removing invalid filler: {target_path.name}")
            target_path.unlink()

        elif common.format_key(existing) != common.format_key(reference):
            print(
                f"Removing filler in the wrong format: {target_path.name} "
                f"is {common.describe_format(existing)}"
            )
            target_path.unlink()

        else:
            duration = common.get_audio_duration(target_path)

            print(
                f"Valid filler found: {target_path.name} "
                f"({common.format_duration(duration)})"
            )

            return target_path

    backend.make_silence(target_path, seconds, reference=reference)

    return target_path


# ============================================================
# SEQUENCE ASSEMBLY
# ============================================================


def build_sequence(entries):
    """
    Build the output sequence: each row's leading filler, that row's dialogue,
    then its trailing filler if the CSV named one.

    Rows contribute their leading filler by default, so the master opens with
    row 0's filler (the instrumental). A row whose "filler_before" cell says
    "N" contributes none, following the previous row's audio directly. Trailing
    fillers are opt-in per row via "filler_after", so there is still no gap
    after the last line unless that row explicitly asks for one. All paths come
    from the normalization stage.
    """
    sequence = []

    for entry in entries:
        before = entry.get("filler_path")

        if before is not None:
            sequence.append(before)

        sequence.append(entry["stitch_path"])

        after = entry.get("filler_after_path")

        if after is not None:
            sequence.append(after)

    return sequence


# ============================================================
# STITCHING
# ============================================================


def stitch_single_pass(backend, sequence, target):
    """
    Concatenate the whole sequence in one operation.
    """
    print()
    print("=" * 70)
    print("STITCHING")
    print("=" * 70)
    print(f"Segments : {len(sequence)}")
    print(f"Master   : {target}")
    print()

    backend.concat(sequence, target)


def stitch_batched(backend, sequence, target, batch_size):
    """
    Concatenate in batches, appending each batch to the master.

    Retained for parity with the original script. Note that appending
    batch-by-batch rewrites the entire master once per batch, so total bytes
    written grows with the square of the input count; single-pass mode
    (BATCH_SIZE = None) avoids that.
    """
    print()
    print("=" * 70)
    print("STITCHING IN BATCHES")
    print("=" * 70)
    print(f"Segments   : {len(sequence)}")
    print(f"Batch size : {batch_size}")
    print(f"Master     : {target}")

    batch_number = 1
    start = 0

    while start < len(sequence):
        end = min(start + batch_size, len(sequence))
        batch = sequence[start:end]

        print()
        print("-" * 70)
        print(f"BATCH {batch_number}: segments {start + 1} - {end}")
        print("-" * 70)

        batch_file = temp_path_for(target, f"batch_{batch_number:04d}")

        if batch_file.exists():
            batch_file.unlink()

        backend.concat(batch, batch_file)

        try:
            if not target.exists():
                print(f"Installing batch {batch_number} as the initial master.")
                batch_file.replace(target)

            else:
                # Concat into a temporary master first; the existing master is
                # only replaced once the new one validates.
                backend.concat([target, batch_file], target)

            duration = common.get_audio_duration(target)

            print(
                f"Master duration after batch {batch_number}: "
                f"{common.format_duration(duration)}"
            )

        finally:
            if batch_file.exists():
                batch_file.unlink()

        start = end
        batch_number += 1


# ============================================================
# DRY RUN
# ============================================================


def report_dry_run(fatal, problems):
    """
    Close a dry run: summarise what the checks found and return the exit code.

    Reached after normalization and the format survey, so everything a real
    run would check has been checked. `strict` plays no part -- a dry run
    reports both classes of fault and stitches neither way. The exit code is
    non-zero exactly when a mandatory check failed -- the faults no `strict`
    setting would let through -- so a validate-then-build wrapper never
    proceeds to a run that is certain to abort or to build a mispaired master.
    """
    print()
    print("=" * 70)
    print("DRY RUN COMPLETE")
    print("=" * 70)
    print(f"Mandatory check failures : {sum(c for _, c in fatal)}")

    for title, count in fatal:
        print(f"    {title:<26}: {count}")

    print(f"Other problems           : {problems}")
    print(f"Warnings                 : {len(common.logged_warnings())}")
    print()
    print(
        f"The master was NOT written or modified. {common.WORK_DIRECTORY.name}/ "
        "may have been updated"
    )
    print("(normalized cache, generated fillers); a real run reuses it.")

    if fatal:
        print()
        print(
            "A real run would ABORT regardless of strict. Fix the exceptions "
            "listed above."
        )
    elif problems:
        print()
        print(
            "A real run would abort under strict=1, or build an INCOMPLETE "
            "master under strict=0."
        )
    else:
        print()
        print("No problems found. A real run would build the full master.")

    print()

    return 1 if fatal else 0


# ============================================================
# MAIN
# ============================================================


def main():

    log_file = common.start_run_log("run")

    print()
    print("=" * 70)
    print("DIALOGUE MP3 STITCHER")
    print("=" * 70)

    arguments = common.parse_arguments(sys.argv)

    row_limit = arguments.row_limit
    strict = arguments.strict
    dry_run = arguments.dry_run

    if dry_run:
        print(
            "Mode        : DRY RUN - every check runs, nothing is stitched; "
            "strict is ignored"
        )
    else:
        print(
            f"Strict mode : {'on' if strict else 'OFF'} " f"({arguments.strict_source})"
        )

        if not strict:
            print(
                "              missing, corrupt and format-mismatched inputs "
                "will be skipped."
            )

    print(f"Run log     : {log_file}")
    print(
        f"Loudness    : "
        + (
            f"{common.DIALOGUE_TARGET_LUFS:g} LUFS"
            if arguments.loudness
            else "OFF - lines keep their recorded level"
        )
    )
    print(
        f"Settings    : {settings.SETTINGS_FILE.name} "
        + (
            "found"
            if settings.SETTINGS_FILE.exists()
            else "not found; built-in defaults"
        )
    )

    # Every setting and where it came from, so a master can be traced back to
    # the values that built it.
    common.log_only()
    common.log_only("SETTINGS")

    for line in settings.describe(common.SETTINGS, common.SETTINGS_SOURCES):
        common.log_only(line)

    common.log_only(
        f"  {'command line':<34} strict={int(strict)} dry_run={int(dry_run)} "
        f"loudness={int(arguments.loudness)}"
    )
    print(
        f"Sources     : " + " then ".join(str(d) for d in common.search_directories())
    )

    common.OUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    common.WORK_DIRECTORY.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # Resolve and pre-flight every input BEFORE touching the
    # existing master. A failed run leaves the previous master
    # intact.
    # --------------------------------------------------------

    resolved = resolve_inputs(row_limit=row_limit)

    if not resolved:
        print()
        print(
            f"ERROR: No dialogue audio to stitch. Expected files in "
            f"{common.OUT_DIRECTORY}"
        )
        print()
        print("Generation is disabled; see generate_mp3.py for how to " "re-enable it.")

        return 1

    usable, missing, corrupt = preflight(resolved)

    problems = len(missing) + len(corrupt)

    # Collected now, acted on only after every other check has reported, so a
    # single run shows the whole picture.
    fatal = mandatory_checks(resolved)

    target = common.canonical_target_header()

    # --------------------------------------------------------
    # Default filler
    #
    # Created before the row fillers are resolved, because most
    # rows fall back to it. Silence is synthesised natively, so
    # this needs no external tools regardless of which backend
    # ends up doing the concatenation.
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("DEFAULT FILLER")
    print("=" * 70)
    print(f"Target format: {common.describe_format(target)}")

    ensure_filler(
        concat_backends.RawMp3Backend(),
        common.SHORT_FILLER,
        common.SHORT_FILLER_SECONDS,
        target,
    )

    # --------------------------------------------------------
    # Generated fillers named by the CSV
    #
    # A row may ask for a synthesised gap by name (e.g.
    # long_filler.mp3) in either filler column. Those are
    # created here, before the row fillers are resolved, so the
    # name resolves like any other file instead of being
    # reported missing.
    # --------------------------------------------------------

    referenced = set()

    for entry in usable:
        record = entry.get("record") or {}

        for column in ("filler_before", "filler_after"):
            name = (record.get(column) or "").strip()

            if name in common.GENERATED_FILLERS:
                referenced.add(name)

    for name in sorted(referenced):
        path, seconds = common.GENERATED_FILLERS[name]

        if path == common.SHORT_FILLER:
            continue  # already handled above

        ensure_filler(
            concat_backends.RawMp3Backend(),
            path,
            seconds,
            target,
        )

    # --------------------------------------------------------
    # Per-row fillers from the CSV's filler_before and
    # filler_after columns
    # --------------------------------------------------------

    (
        named_fillers,
        filler_missing,
        after_named,
        after_missing,
        filler_suppressed,
    ) = resolve_row_fillers(usable)

    report_row_fillers(
        usable,
        named_fillers,
        filler_missing,
        after_named,
        after_missing,
        filler_suppressed,
    )

    problems += len(filler_missing) + len(after_missing)

    # --------------------------------------------------------
    # Dialogue loudness
    #
    # Every line is brought to one loudness first, so that mixed
    # and unmixed lines come out equally loud and each mix works
    # on dialogue that is already at the target. A line that
    # cannot be measured is stitched at its own level.
    # --------------------------------------------------------

    if arguments.loudness:
        _, loudness_failed = apply_loudness(usable)

        problems += len(loudness_failed)

    # --------------------------------------------------------
    # Background score
    #
    # Rows naming a background in the CSV are mixed with it here,
    # before normalization, and the mix replaces the bare
    # dialogue for every later stage. A failed mix keeps the
    # plain dialogue and counts as an ordinary problem.
    # --------------------------------------------------------

    _, score_failed, _ = apply_background_scores(usable)

    problems += len(score_failed)

    # --------------------------------------------------------
    # Normalization
    #
    # Every segment must share one audio format before it can be
    # spliced. .m4a inputs carry AAC, which cannot live in an MP3
    # stream, so they are transcoded here; conforming MP3s pass
    # straight through and stay on the lossless path. Fillers go
    # through the same path -- the opening instrumental arrives as
    # 44.1 kHz stereo AAC.
    # --------------------------------------------------------

    usable, conversion_failed = normalize_entries(usable, target)

    problems += len(conversion_failed)

    # --------------------------------------------------------
    # Format consistency
    #
    # A safety net after normalization: everything should now
    # agree, and a mismatch here means a transcode did not land
    # on the target format.
    # --------------------------------------------------------

    surveyed = []

    for entry in usable:
        if entry.get("filler_path") is not None:
            surveyed.append(entry["filler_path"])

        surveyed.append(entry["stitch_path"])

        if entry.get("filler_after_path") is not None:
            surveyed.append(entry["filler_after_path"])

    # De-duplicate while preserving order; the default filler appears once per
    # row and would otherwise dominate the survey counts.
    seen = set()
    surveyed = [
        path
        for path in surveyed
        if not (path.resolve() in seen or seen.add(path.resolve()))
    ]

    print()
    format_groups = survey_formats(surveyed)
    formats_consistent = report_format_survey(format_groups)

    if not formats_consistent:
        problems += 1

    if dry_run:
        return report_dry_run(fatal, problems)

    if fatal:
        print()
        print("=" * 70)
        print("ABORTED - MANDATORY CHECKS FAILED")
        print("=" * 70)

        for title, count in fatal:
            print(f"    {title:<26}: {count}")

        print()
        print(
            "The master has NOT been written or modified. These checks apply "
            "regardless of"
        )
        print(
            "strict and cannot be overridden; fix the CSV or the filenames "
            "listed above."
        )

        if problems:
            print(f"A further {problems} ordinary problem(s) were also reported.")

        print()

        return 1

    if problems and strict:
        print()
        print("=" * 70)
        print("ABORTED")
        print("=" * 70)
        print(
            f"{problems} problem(s) found. The master has NOT been "
            "written or modified."
        )
        print()
        print("To stitch the usable files anyway, either:")
        print("    python stitch_mp3.py strict=0")
        print('or set "run": {"strict": false} in dice-skit.json.')
        print()

        return 1

    if problems:
        print()
        print(
            f"Continuing with {len(usable)} usable file(s); "
            f"{problems} problem(s) skipped or worked around "
            "(strict is off)."
        )

    if not formats_consistent:
        # Non-strict mode means "stitch what you can", so drop the minority
        # formats rather than failing in the splicer. Keeping them would
        # produce a mis-timed master even where the backend tolerates them.
        majority = max(
            format_groups.values(),
            key=lambda group: len(group["paths"]),
        )

        keep = {path.resolve() for path in majority["paths"]}

        dropped = [path for path in surveyed if path.resolve() not in keep]

        print()
        print(
            f"Dropping {len(dropped)} file(s) whose format differs from the "
            f"majority ({common.describe_format(majority['header'])}):"
        )

        for path in dropped:
            print(f"    {path.name}")

        # A trailing filler is optional, so an off-format one is dropped on its
        # own rather than taking the whole dialogue row down with it.
        for entry in usable:
            after = entry.get("filler_after_path")

            if after is not None and after.resolve() not in keep:
                entry["filler_after_path"] = None

        usable = [
            entry
            for entry in usable
            if entry["stitch_path"].resolve() in keep
            and (entry["filler_path"] is None or entry["filler_path"].resolve() in keep)
        ]

        if not usable:
            print()
            print("ERROR: no dialogue files remain in the majority format.")

            return 1

    if not usable:
        print()
        print("ERROR: No usable dialogue audio remains. Nothing to stitch.")

        return 1

    # --------------------------------------------------------
    # Assemble the output sequence
    # --------------------------------------------------------

    sequence = build_sequence(usable)

    # --------------------------------------------------------
    # Choose a backend
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("BACKEND")
    print("=" * 70)

    backend = select_backend(
        sequence,
        output_format=common.OUTPUT_FORMAT,
        preference=common.CONCAT_BACKEND,
    )

    print(f"Preference   : {common.CONCAT_BACKEND}")
    print(f"Selected     : {backend.name}")
    print(f"Requirements : {backend.requirements}")
    print(f"Output format: {common.OUTPUT_FORMAT}")

    if backend.name == "python":
        print()
        print(
            "NOTE: using the pure-Python MPEG frame splicer. No external "
            "tools are required."
        )

    # --------------------------------------------------------
    # Fillers
    # --------------------------------------------------------

    # --------------------------------------------------------
    # Stitch
    # --------------------------------------------------------

    if common.BATCH_SIZE:
        # Batched mode grows the master in place, so it must start absent.
        if common.MASTER_FILE.exists():
            print()
            print(f"Deleting existing master: {common.MASTER_FILE}")
            common.MASTER_FILE.unlink()

        stitch_batched(
            backend,
            sequence,
            common.MASTER_FILE,
            common.BATCH_SIZE,
        )
    else:
        # Single-pass mode writes to a temporary and only replaces the old
        # master once the new one validates, so it is left alone until then.
        stitch_single_pass(backend, sequence, common.MASTER_FILE)

    # --------------------------------------------------------
    # Seek metadata
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("FINALIZING")
    print("=" * 70)

    backend.finalize(common.MASTER_FILE)

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------

    if not common.MASTER_FILE.exists():
        raise RuntimeError("Master file was not created.")

    if not common.is_valid_audio(common.MASTER_FILE):
        raise RuntimeError("Final master failed validation.")

    duration = common.get_audio_duration(common.MASTER_FILE)
    size_mb = common.MASTER_FILE.stat().st_size / (1024 * 1024)

    print()
    print("=" * 70)
    print("ALL DONE")
    print("=" * 70)
    print()
    print(f"Master file : {common.MASTER_FILE}")
    print(f"Duration    : {common.format_duration(duration)}")
    print(f"Size        : {size_mb:.2f} MB")
    print(f"Dialogues   : {len(usable)}")
    print(f"Segments    : {len(sequence)}")
    print(f"Backend     : {backend.name}")
    print(f"Warnings    : {len(common.logged_warnings())}")
    print(f"Run log     : {common.log_path()}")

    if problems:
        print()
        print(f"WARNING: master is INCOMPLETE - {problems} input(s) were " "skipped.")

    print()

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        try:
            code = main()

        finally:
            # Written even on failure, so an aborted run still leaves a log
            # containing every warning it raised.
            path = common.close_run_log()

            if path is not None:
                print(f"Run log written: {path}")

        sys.exit(code)

    except KeyboardInterrupt:
        print()
        print("Process interrupted by user.")
        sys.exit(1)

    except Exception as exc:
        print()
        print("=" * 70)
        print("ERROR")
        print("=" * 70)
        print(exc)
        print()
        sys.exit(1)
