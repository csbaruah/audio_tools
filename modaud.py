"""Console entry point for the packaged modaud application."""

import os
import sys
from pathlib import Path

import audio_common as common
import audio_tools
import stitch_mp3

USAGE = """Usage:
    modaud.exe <project-folder> [stitch options]

The project folder must contain config.json and input\\<csv_file>.
config.json names the CSV file and contains all project settings.
The old dice-skit.json filename is deprecated.
Stitch options are the same as stitch_mp3.py, for example:
    modaud.exe "C:\\Projects\\my audio" dry_run=1
    modaud.exe "C:\\Projects\\my audio" strict=0
    modaud.exe "C:\\Projects\\my audio" clean yes=1
"""


def bundled_ffmpeg_directory(frozen=None, executable=None, source_file=None):
    """Return the release's bundled FFmpeg folder, if one is present."""
    if frozen is None:
        frozen = getattr(sys, "frozen", False)

    if frozen:
        base = Path(executable or sys.executable).resolve().parent
    else:
        source = Path(source_file or __file__).resolve()
        base = source.parent / "output" / "release"

    candidate = base / "ffmpeg"
    return candidate if candidate.is_dir() else None


def run(argv=None):
    """Run the stitch command against the project folder in argv[0]."""
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0].strip().lower() in ("-h", "--help", "/?", "help"):
        print(USAGE)
        return 0

    if not argv:
        print(USAGE)
        return 2

    project_arg, *stitch_args = argv
    project = Path(project_arg).expanduser().resolve()

    if not project.is_dir():
        print(f"ERROR: project folder does not exist: {project}")
        return 2

    bundled = bundled_ffmpeg_directory()

    if bundled is not None:
        os.environ["DICE_SKIT_FFMPEG_DIR"] = str(bundled)
        audio_tools.reset()

    try:
        common.configure(project)
    except Exception as exc:
        print(f"ERROR: cannot open project {project}: {exc}")
        return 1

    command_argv = ["stitch_mp3.py", *stitch_args]

    if stitch_args and stitch_args[0].strip().lower() == "clean":
        try:
            return stitch_mp3.clean_command(command_argv)
        except (KeyboardInterrupt, EOFError):
            print("\nProcess interrupted by user.")
            return 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 1

    original_argv = sys.argv
    sys.argv = command_argv

    try:
        return stitch_mp3.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
        return 1
    except Exception as exc:
        print("\n" + "=" * 70)
        print("ERROR")
        print("=" * 70)
        print(exc)
        return 1
    finally:
        sys.argv = original_argv
        log_path = common.close_run_log()

        if log_path is not None:
            print(f"Run log written: {log_path}")


def main():
    return run()


if __name__ == "__main__":
    sys.exit(main())
