"""
Audio tools adapter: the one place the pipeline runs FFmpeg or ffprobe.

Section 13.3 of docs/audio-concat-study.md specifies this. Every external
audio tool call goes through run() here, for three reasons:

  * Security (sec. 13.8): tools are started by absolute path with an argument
    list and no shell, so text from a CSV or a filename can never become a
    command. A bundled FFmpeg is preferred over whatever "ffmpeg" happens to
    be first on PATH.

  * Portability (sec. 13.2): a platform where programs cannot be started (iOS,
    Android) can replace this one module with a library-based implementation;
    nothing else in the pipeline knows how FFmpeg is reached.

  * One place to time out, log or sandbox tool calls later.

Lookup order for each tool:

  1. DICE_SKIT_FFMPEG_DIR, if set: a folder holding ffmpeg.exe and
     ffprobe.exe (the installed app sets this to its bundled copy)
  2. an "ffmpeg" folder beside this file (a bundled build next to the code)
  3. PATH, as before this adapter existed
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Resolved absolute paths, cached after the first lookup. A missing tool is
# cached as None so the lookup is not repeated on every call.
_resolved = {}


class ToolMissing(RuntimeError):
    """
    Raised when a tool is required but cannot be found.
    """


def _candidates(name):
    exe = f"{name}.exe" if sys.platform == "win32" else name

    folder = os.environ.get("DICE_SKIT_FFMPEG_DIR")

    if folder:
        yield Path(folder) / exe

    yield _HERE / "ffmpeg" / exe
    yield _HERE / "ffmpeg" / "bin" / exe


def find(name):
    """
    Absolute path of `name` ("ffmpeg" or "ffprobe"), or None.
    """
    if name in _resolved:
        return _resolved[name]

    found = None

    for candidate in _candidates(name):
        if candidate.is_file():
            found = str(candidate)
            break

    if found is None:
        on_path = shutil.which(name)
        found = str(Path(on_path).resolve()) if on_path else None

    _resolved[name] = found

    return found


def available(name):
    """
    True when `name` exists and answers -version.
    """
    path = find(name)

    if path is None:
        return False

    try:
        result = subprocess.run(
            [path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_no_window(),
        )

    except OSError:
        return False

    return result.returncode == 0


def run(args, check=False):
    """
    Run an audio tool. `args[0]` is the tool name ("ffmpeg" or "ffprobe");
    the rest are its arguments, passed as a list with no shell.

    Returns the subprocess.CompletedProcess, with text output. Raises
    ToolMissing when the tool cannot be found. With check=True, raises
    RuntimeError on a non-zero exit.
    """
    args = [str(arg) for arg in args]
    name = args[0]

    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError(f"not an audio tool: {name!r}")

    path = find(name)

    if path is None:
        raise ToolMissing(f"{name} was not found")

    result = subprocess.run(
        [path, *args[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_no_window(),
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"{name} failed with exit code {result.returncode}: "
            + (result.stderr.strip().splitlines()[-1:] or ["no output"])[0]
        )

    return result


def _no_window():
    """
    On Windows, stop each tool call flashing a console window when the
    pipeline runs inside the desktop app.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}

    return {}


def reset():
    """
    Forget cached lookups, e.g. after DICE_SKIT_FFMPEG_DIR changes.
    """
    _resolved.clear()