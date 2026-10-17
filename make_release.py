"""Build the Windows console release and stage its sample project."""

import csv
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RELEASE_DIR = PROJECT_ROOT / "output" / "release"
SAMPLE_NAME = "sample_project"
SAMPLE_AUDIO = (
    "0_SAMAY.mp3",
    "40_Sahadeva.mp3",
    "86_Bhishma.mp3",
)
SAMPLE_ASSETS = (
    "NewMahaIntro.mp3",
    "shakuni.mp3",
    "dice.mp3",
    "short_filler.mp3",
    "long_filler.mp3",
    "krishna_flute_background.mp3",
)
SAMPLE_ROWS = (
    (
        "0",
        "SAMAY",
        "I am Time.",
        "SAMAY",
        "NewMahaIntro.mp3",
        "shakuni.mp3",
        "krishna_flute_background.mp3",
        "t-m-loop",
    ),
    (
        "40",
        "SAHADEVA",
        "We are yours to stake, Bhratashree. But tell me — was this wisdom, or was it pride?",
        "SAHADEVA",
        "dice.mp3",
        "short_filler.mp3",
        "shakuni.mp3",
        "b-1s-m",
    ),
    (
        "86",
        "BHISHMA",
        "Putri... Dharma, in this moment, is very difficult to understand.",
        "BHISHMA",
        "N",
        "long_filler.mp3",
        "krishna_flute_background.mp3",
        "t-b-2s-m",
    ),
)
SAMPLE_COLUMNS = (
    "sequence_number",
    "character_name",
    "dialogue_text",
    "speaker",
    "filler_before",
    "filler_after",
    "background_score",
    "score_cues",
)

RELEASE_README = """MODAUD WINDOWS CONSOLE RELEASE
==============================

The sample_project folder contains a small example with three recorded lines,
fillers and background-score cues.

To build the sample master from PowerShell:

  .\\modaud.exe .\\sample_project

To validate the sample without building the master:

  .\\modaud.exe .\\sample_project dry_run=1

To use another project, pass its folder as the first argument:

  .\\modaud.exe "C:\\Projects\\my audio"
  .\\modaud.exe "C:\\Projects\\my audio" strict=0

The project folder must contain input\\dialogues_new.csv. Its optional
dice-skit.json controls settings and paths. The master and run log are written
to the configured output folders. Existing stitch_mp3.py options are accepted
after the project-folder argument.

FFmpeg and FFprobe are bundled in the sibling ffmpeg folder. Keep that folder
beside modaud.exe. This console build stitches audio locally; TTS generation
and GUI features are not included in this prototype.
"""


class ReleaseError(RuntimeError):
    """Raised when the release cannot be built safely."""


def find_tool(name, path_value=None):
    """Locate a required executable on the supplied PATH."""
    found = shutil.which(name, path=path_value)

    if found is None:
        raise ReleaseError(f"{name}.exe was not found on PATH")

    return Path(found).resolve()


def verify_tool(path):
    """Fail the build if a discovered FFmpeg tool cannot run."""
    try:
        result = subprocess.run(
            [str(path), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseError(f"cannot run {path}: {exc}") from exc

    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[0] if detail else f"exit code {result.returncode}"
        raise ReleaseError(f"{path} did not pass its version check: {message}")


def copy_ffmpeg_tools(release_dir, path_value=None):
    """Copy FFmpeg, FFprobe, adjacent runtime DLLs and available notices."""
    ffmpeg = find_tool("ffmpeg", path_value)
    ffprobe = find_tool("ffprobe", path_value)
    verify_tool(ffmpeg)
    verify_tool(ffprobe)

    destination = Path(release_dir) / "ffmpeg"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ffmpeg, destination / "ffmpeg.exe")
    shutil.copy2(ffprobe, destination / "ffprobe.exe")

    source_dirs = {ffmpeg.parent, ffprobe.parent}

    for source_dir in source_dirs:
        for dll in source_dir.glob("*.dll"):
            target = destination / dll.name
            if not target.exists():
                shutil.copy2(dll, target)

    for source_dir in source_dirs | {ffmpeg.parent.parent, ffprobe.parent.parent}:
        for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
            for notice in source_dir.glob(pattern):
                if notice.is_file():
                    target = destination / notice.name
                    if not target.exists():
                        shutil.copy2(notice, target)

    return destination


def write_sample_project(project_root, release_dir):
    """Create the sample project by copying only its declared media inputs."""
    source_media = Path(project_root) / "input" / "human_gen"
    sample = Path(release_dir) / SAMPLE_NAME
    input_dir = sample / "input"
    human_dir = input_dir / "human_gen"

    for folder in (
        human_dir,
        sample / "in-out" / "machine_gen",
        sample / "output",
    ):
        folder.mkdir(parents=True, exist_ok=True)

    for filename in SAMPLE_AUDIO + SAMPLE_ASSETS:
        source = source_media / filename
        if not source.is_file():
            raise ReleaseError(f"sample asset is missing: {source}")
        shutil.copy2(source, human_dir / filename)

    shutil.copy2(Path(project_root) / "dice-skit.json", sample / "dice-skit.json")

    with (input_dir / "dialogues_new.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(SAMPLE_COLUMNS)
        writer.writerows(SAMPLE_ROWS)

    return sample


def build_release(project_root=PROJECT_ROOT, release_dir=RELEASE_DIR):
    """Package modaud.exe and stage its FFmpeg tools and sample project."""
    project_root = Path(project_root).resolve()
    release_dir = Path(release_dir).resolve()

    if os.name != "nt":
        raise ReleaseError("the modaud.exe release must be built on Windows")

    release_dir.mkdir(parents=True, exist_ok=True)
    copy_ffmpeg_tools(release_dir)
    write_sample_project(project_root, release_dir)
    (release_dir / "README.txt").write_text(RELEASE_README, encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="modaud-build-") as temporary:
        temporary = Path(temporary)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--console",
            "--noconfirm",
            "--clean",
            "--name",
            "modaud",
            "--distpath",
            str(release_dir),
            "--workpath",
            str(temporary / "build"),
            "--specpath",
            str(temporary / "spec"),
            str(project_root / "modaud.py"),
        ]

        try:
            subprocess.run(command, cwd=project_root, check=True)
        except FileNotFoundError as exc:
            raise ReleaseError(
                "PyInstaller is not installed for the build Python; install "
                "requirements-build.txt"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise ReleaseError(
                f"PyInstaller failed with exit code {exc.returncode}"
            ) from exc

    executable = release_dir / "modaud.exe"
    if not executable.is_file():
        raise ReleaseError(f"PyInstaller did not create {executable}")

    return executable


def clean_release(release_dir=RELEASE_DIR):
    """Remove only the generated output/release tree."""
    release_dir = Path(os.path.abspath(release_dir))
    expected = Path(os.path.abspath(PROJECT_ROOT / "output" / "release"))

    if release_dir != expected:
        raise ReleaseError(
            f"refusing to clean outside the release folder: {release_dir}"
        )

    is_junction = getattr(release_dir, "is_junction", lambda: False)
    if release_dir.is_symlink() or is_junction():
        raise ReleaseError(f"refusing to clean a release-folder link: {release_dir}")

    if release_dir.exists():
        if not release_dir.is_dir():
            raise ReleaseError(f"release path is not a directory: {release_dir}")

        def make_writable_and_retry(function, path, error):
            try:
                os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
                function(path)
            except OSError:
                raise error[1]

        try:
            shutil.rmtree(release_dir, onerror=make_writable_and_retry)
        except OSError as exc:
            raise ReleaseError(f"could not clean release artifacts: {exc}") from exc

    return release_dir


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if argv == ["clean"]:
        try:
            cleaned = clean_release()
        except ReleaseError as exc:
            print(f"CLEAN ERROR: {exc}", file=sys.stderr)
            return 1

        print(f"Cleaned release artifacts: {cleaned}")
        return 0

    if argv:
        print("Usage: python make_release.py [clean]", file=sys.stderr)
        return 2

    try:
        executable = build_release()
    except ReleaseError as exc:
        print(f"RELEASE ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Release created: {executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
