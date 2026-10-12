"""
Shared configuration and helpers for the dice-skit audio pipeline.

This module is the common foundation for both halves of the pipeline:

    generate_mp3.py   Sarvam AI TTS -> one MP3 per dialogue row
                      (currently DISABLED / commented out)

    stitch_mp3.py     dialogues-mp3/*.mp3 -> master_dialogues.mp3

Nothing in here talks to Sarvam or to FFmpeg's concat demuxer. It holds the
configuration block, the CSV loader, the file validators, and a small set of
native MPEG-frame primitives that let the pipeline validate and splice MP3s
without any external binary.
"""

import csv
import re
import sys
from collections import namedtuple
from datetime import datetime
from pathlib import Path

import audio_tools


# ============================================================
# CONFIGURATION
# ============================================================
#
# Values come from dice-skit.json through settings.py (section 12 of
# docs/audio-concat-study.md). The defaults live in settings.SPEC, so a missing
# file or key changes nothing. The names below are unchanged so the rest of the
# pipeline reads them as before; edit dice-skit.json, not this block.

import settings as _settings

# The folder holding this code. Used as the project folder when the scripts
# are run from the command line, which is how they have always worked.
CODE_DIRECTORY = Path(__file__).resolve().parent

# Extensions accepted as dialogue audio. Anything that is not already an MP3
# in the target format below is transcoded once into NORMALIZED_DIRECTORY
# before stitching; see concat_backends.normalize_input().
INPUT_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".mp4",
    ".aac",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
)


# Every name configure() binds. Listed so the global statement below stays in
# step with the assignments.
_CONFIGURED = (
    "SETTINGS", "SETTINGS_SOURCES", "BASE_DIRECTORY", "SETTINGS_FILE",
    "_RUN", "_PATHS", "_OUTPUT", "_FILLERS", "_SCORE", "_LOUDNESS", "_TTS",
    "INPUT_DIRECTORY", "IN_OUT_DIRECTORY", "OUTPUT_DIRECTORY",
    "CSV_FILE", "OUT_DIRECTORY", "HUMAN_GEN_DIRECTORY", "MACHINE_GEN_DIRECTORY",
    "WORK_DIRECTORY", "LOG_DIRECTORY", "MASTER_FILE", "_SOURCE_DIRECTORIES",
    "TTS_ENABLED", "TTS_API_KEY_SOURCE", "LANGUAGE_CODE", "MODEL", "PACE",
    "SPEECH_SAMPLE_RATE", "OUTPUT_AUDIO_CODEC", "OVERWRITE_EXISTING",
    "SHORT_FILLER", "SHORT_FILLER_SECONDS", "LONG_FILLER", "LONG_FILLER_SECONDS",
    "GENERATED_FILLERS", "CONCAT_BACKEND", "OUTPUT_FORMAT",
    "TARGET_SAMPLE_RATE", "TARGET_BITRATE", "TARGET_MONO", "FILLER_BITRATE",
    "NORMALIZED_DIRECTORY", "DIALOGUE_LOUDNESS", "DIALOGUE_TARGET_LUFS",
    "DIALOGUE_TRUE_PEAK_DBFS", "LOUDNESS_DIRECTORY", "MIXED_DIRECTORY",
    "SCORE_DUCK_DB", "SCORE_RAMP_SECONDS", "SCORE_MATCH_LOUDNESS", "SCORE_LIMIT",
    "STRICT_MISSING", "BATCH_SIZE",
)


def _path(value):
    path = Path(value)

    return path if path.is_absolute() else BASE_DIRECTORY / path


def _under(parent, value):
    """
    `value` resolved inside `parent`, unless it is absolute.
    """
    path = Path(value)

    return path if path.is_absolute() else parent / path


def _check_layout(input_dir, in_out_dir, output_dir):
    """
    Refuse an output folder that overlaps an input folder: "clean" deletes
    inside output, so an overlap could delete recordings.
    """
    out = output_dir.resolve()

    for name, other in (("input", input_dir), ("in_out", in_out_dir)):
        other = other.resolve()

        if out == other or out in other.parents or other in out.parents:
            raise _settings.SettingsError(
                f"paths.output ({output_dir}) must not be the same as, inside, "
                f"or contain paths.{name} ({other})"
            )


def configure(project=None):
    """
    Bind every setting below for one project folder.

    `project` holds dialogues_new.csv, human_gen/, machine_gen/ and, if it
    has one, dice-skit.json. Relative paths in that file are relative to the
    project. None means the folder holding this code, which is what the
    command-line scripts use.

    Raises settings.SettingsError for an invalid dice-skit.json, leaving the
    previous configuration in place.
    """
    global SETTINGS, SETTINGS_SOURCES, BASE_DIRECTORY, SETTINGS_FILE
    global _RUN, _PATHS, _OUTPUT, _FILLERS, _SCORE, _LOUDNESS, _TTS
    global INPUT_DIRECTORY, IN_OUT_DIRECTORY, OUTPUT_DIRECTORY
    global CSV_FILE, OUT_DIRECTORY, HUMAN_GEN_DIRECTORY, MACHINE_GEN_DIRECTORY
    global WORK_DIRECTORY, LOG_DIRECTORY, MASTER_FILE, _SOURCE_DIRECTORIES
    global TTS_ENABLED, TTS_API_KEY_SOURCE, LANGUAGE_CODE, MODEL, PACE
    global SPEECH_SAMPLE_RATE, OUTPUT_AUDIO_CODEC, OVERWRITE_EXISTING
    global SHORT_FILLER, SHORT_FILLER_SECONDS, LONG_FILLER, LONG_FILLER_SECONDS
    global GENERATED_FILLERS, CONCAT_BACKEND, OUTPUT_FORMAT
    global TARGET_SAMPLE_RATE, TARGET_BITRATE, TARGET_MONO, FILLER_BITRATE
    global NORMALIZED_DIRECTORY, DIALOGUE_LOUDNESS, DIALOGUE_TARGET_LUFS
    global DIALOGUE_TRUE_PEAK_DBFS, LOUDNESS_DIRECTORY, MIXED_DIRECTORY
    global SCORE_DUCK_DB, SCORE_RAMP_SECONDS, SCORE_MATCH_LOUDNESS, SCORE_LIMIT
    global STRICT_MISSING, BATCH_SIZE

    project = Path(project).resolve() if project else CODE_DIRECTORY
    settings_file = project / _settings.SETTINGS_FILE.name

    # An old-layout project (everything at the top) is moved into input /
    # in-out / output on its first run, before its settings are read
    # (doc section 14). layout.migrate() moves nothing if any destination
    # already exists, and puts things back if a move fails.
    import layout

    if layout.needs_migration(project):
        layout.migrate(project)

    # Load first: a bad file must not leave half the names rebound.
    loaded, sources = _settings.load(settings_file)

    SETTINGS, SETTINGS_SOURCES = loaded, sources
    SETTINGS_FILE = settings_file

    # All paths are anchored to the project folder rather than the current
    # working directory, so the pipeline behaves the same no matter where it
    # is invoked from.
    BASE_DIRECTORY = project

    _RUN = SETTINGS["run"]
    _PATHS = SETTINGS["paths"]
    _OUTPUT = SETTINGS["output"]
    _FILLERS = SETTINGS["fillers"]
    _SCORE = SETTINGS["background_score"]
    _LOUDNESS = SETTINGS["loudness"]
    _TTS = SETTINGS["tts"]


    # The three top-level folders (doc section 14). Each may be relative to
    # the project or absolute.
    INPUT_DIRECTORY = _path(_PATHS["input"])
    IN_OUT_DIRECTORY = _path(_PATHS["in_out"])
    OUTPUT_DIRECTORY = _path(_PATHS["output"])

    _check_layout(INPUT_DIRECTORY, IN_OUT_DIRECTORY, OUTPUT_DIRECTORY)

    # The dialogue manifest, inside input.
    CSV_FILE = _under(INPUT_DIRECTORY, _PATHS["csv"])

    # Human-recorded dialogue, fetched from Google Drive by fetch_drive.py. A flat
    # folder of .mp3/.m4a files whose names start with the sequence number they
    # satisfy; Drive's subfolder structure is not preserved. Named filler audio
    # (sound effects, opening music) lives here too.
    HUMAN_GEN_DIRECTORY = _under(INPUT_DIRECTORY, _PATHS["human_gen"])

    # Machine-generated dialogue, i.e. Sarvam TTS output from generate_mp3.py.
    # Written by one stage and read by another, so it lives in in-out.
    MACHINE_GEN_DIRECTORY = _under(IN_OUT_DIRECTORY, _PATHS["machine_gen"])

    # Destination for the finished master, and nothing else. This directory is
    # deliberately NOT a source: it is never searched for dialogue audio. The
    # name OUT_DIRECTORY is kept for the code that already uses it.
    OUT_DIRECTORY = _under(OUTPUT_DIRECTORY, _PATHS["master_dir"])

    # Derived artifacts: generated silence and the transcode cache. Everything here
    # is rebuildable and safe to delete.
    WORK_DIRECTORY = _under(OUTPUT_DIRECTORY, _PATHS["work"])

    # Per-run logs, one file per invocation: log/run<YYYYmmdd-HHMMSS>.log
    LOG_DIRECTORY = _under(OUTPUT_DIRECTORY, _PATHS["log"])

    MASTER_FILE = OUT_DIRECTORY / _PATHS["master"]


    # ------------------------------------------------------------
    # Sarvam TTS settings
    #
    # Only consumed by generate_mp3.py. The API key is NOT a setting: see
    # TTS_API_KEY_SOURCE.
    # ------------------------------------------------------------

    TTS_ENABLED = _TTS["enabled"]
    TTS_API_KEY_SOURCE = _TTS["api_key_source"]
    LANGUAGE_CODE = _TTS["language"]
    MODEL = _TTS["model"]
    PACE = _TTS["pace"]
    SPEECH_SAMPLE_RATE = _TTS["sample_rate"]
    OUTPUT_AUDIO_CODEC = "mp3"

    # If True, generate_mp3.py regenerates every dialogue MP3.
    # If False, valid existing MP3s are reused.
    OVERWRITE_EXISTING = _TTS["overwrite_existing"]

    # ------------------------------------------------------------
    # Fillers
    # ------------------------------------------------------------

    # Default audio inserted before a dialogue line when the CSV's
    # "filler_before" cell is empty. Generated, so it lives with the other derived
    # artifacts rather than alongside source audio.
    SHORT_FILLER = WORK_DIRECTORY / "short_filler.mp3"

    SHORT_FILLER_SECONDS = _FILLERS["short_seconds"]

    # A longer generated gap. Not used by default: it enters the sequence only when
    # a row names it in "filler_before" or "filler_after", and is synthesised on
    # demand at that point (see GENERATED_FILLERS).
    LONG_FILLER = WORK_DIRECTORY / "long_filler.mp3"
    LONG_FILLER_SECONDS = _FILLERS["long_seconds"]

    # Fillers this pipeline synthesises rather than expects on disk, keyed by the
    # filename a CSV cell would use to name one. A row may reference either by
    # name; the stitch stage creates whichever ones are actually referenced before
    # resolving row fillers, so the CSV can call for a 10-second gap without anyone
    # having to produce long_filler.mp3 by hand.
    GENERATED_FILLERS = {
        SHORT_FILLER.name: (SHORT_FILLER, SHORT_FILLER_SECONDS),
        LONG_FILLER.name: (LONG_FILLER, LONG_FILLER_SECONDS),
    }

    # ------------------------------------------------------------
    # Stitching
    # ------------------------------------------------------------

    # Which concatenation backend to use:
    #
    #   "auto"    prefer ffmpeg when it is on PATH (proven, format-agnostic),
    #             otherwise fall back to the pure-Python MP3 splicer
    #   "ffmpeg"  force the ffmpeg concat demuxer
    #   "python"  force the dependency-free MP3 frame splicer (MP3 only)
    #
    # See docs/audio-concat-study.md for why both exist.
    CONCAT_BACKEND = _RUN["backend"]

    # Container/codec of the stitched master.
    # Anything other than "mp3" forces the ffmpeg backend, because non-MP3
    # containers cannot be spliced at the byte level.
    OUTPUT_FORMAT = _OUTPUT["format"]

    # ------------------------------------------------------------
    # Input formats and normalisation target
    # ------------------------------------------------------------


    # The one audio format every segment must share before it can be spliced.
    #
    # The default matches the Sarvam TTS output, which is the bulk of the
    # material: keeping it there means those files need no transcode at all and
    # stay on the lossless stream-copy path. Inputs at other rates (44.1 kHz .m4a
    # files, the 22.05 kHz intro) are resampled to this once and cached.
    TARGET_SAMPLE_RATE = _OUTPUT["sample_rate"]
    TARGET_BITRATE = _OUTPUT["bitrate"]
    TARGET_MONO = _OUTPUT["mono"]

    # Generated silence is written in the target format, so fillers always join by
    # stream copy.
    FILLER_BITRATE = TARGET_BITRATE

    # Cache for transcoded inputs, under the work directory so that no source or
    # output directory is polluted with derived files.
    NORMALIZED_DIRECTORY = WORK_DIRECTORY / "normalized"

    # ------------------------------------------------------------
    # Dialogue loudness (docs/audio-concat-study.md section 11)
    # ------------------------------------------------------------

    # Bring every dialogue line to one loudness before mixing and stitching.
    # Overridable per run from the command line: `loudness=0` / `loudness=1`.
    DIALOGUE_LOUDNESS = _LOUDNESS["enabled"]

    # Integrated loudness every line is brought to, in LUFS.
    DIALOGUE_TARGET_LUFS = _LOUDNESS["target_lufs"]

    # True-peak ceiling after the gain, in dBFS. The limiter acts only where the
    # gain would push a peak past it.
    DIALOGUE_TRUE_PEAK_DBFS = _LOUDNESS["true_peak_dbfs"]

    # Cache for loudness-matched dialogue.
    LOUDNESS_DIRECTORY = WORK_DIRECTORY / "loudness"

    # ------------------------------------------------------------
    # Background score (docs/audio-concat-study.md section 10)
    # ------------------------------------------------------------

    # Cache for dialogue rows mixed with a background score.
    MIXED_DIRECTORY = WORK_DIRECTORY / "mixed"

    # How far the non-prominent track is turned down. Quieter, never silent: the
    # faded track must stay faintly audible. Tune by ear.
    SCORE_DUCK_DB = _SCORE["duck_db"]

    # Length of the linear volume ramp at every change of prominent track.
    SCORE_RAMP_SECONDS = _SCORE["ramp_seconds"]

    # Before mixing, the background is gained to match the dialogue's integrated
    # loudness, so that "prominent" reliably means "louder".
    SCORE_MATCH_LOUDNESS = _SCORE["match_loudness"]

    # Peak ceiling for the summed mix, as a linear fraction of full scale.
    SCORE_LIMIT = _SCORE["limit"]


    # What to do when an expected dialogue MP3 is missing, corrupt, or in a
    # mismatched audio format:
    #
    #   True   pre-flight the whole set, print one consolidated report, abort
    #          without writing a master
    #   False  warn per file, drop it, stitch the remainder
    #
    # Overridable per run from the command line: `strict=0` / `strict=1`.
    STRICT_MISSING = _RUN["strict"]

    # Number of dialogue files per intermediate batch.
    #
    # None means "single pass": build one concat list for the whole sequence.
    # Set to an integer (e.g. 40) to restore the original batch-and-append
    # behaviour, which rewrites the entire master once per batch.
    BATCH_SIZE = _RUN["batch_size"]

    _SOURCE_DIRECTORIES = {
        "human_gen": HUMAN_GEN_DIRECTORY,
        "machine_gen": MACHINE_GEN_DIRECTORY,
    }

    return project


def search_directories():
    """
    Directories searched for a row's dialogue audio, in precedence order.

    By default:

        1. human_gen/       human recordings fetched from Drive
        2. machine_gen/     Sarvam TTS output

    The first directory holding a given sequence number wins outright, so a
    real recording is never displaced by synthesised speech however new the
    synthesised file is. Modification time only breaks ties within a single
    directory. The order is paths.search_order in dice-skit.json.

    OUT_DIRECTORY is absent by design: it holds only the finished master.
    """
    return tuple(_SOURCE_DIRECTORIES[name] for name in _PATHS["search_order"])


try:
    configure()
except _settings.SettingsError as _exc:
    # Fail before any audio is touched, with the message alone.
    print(f"ERROR in settings: {_exc}", file=sys.stderr)
    sys.exit(1)


# ============================================================
# RUN LOG
# ============================================================
#
# Every run writes a full transcript to log/run<timestamp>.log. Warnings are
# additionally collected so a run can summarise them, and are prefixed with
# "WARNING:" so the log can be grepped.

_log_handle = None
_log_path = None
_saved_stdout = None
_saved_stderr = None
_warnings = []


class _Tee:
    """
    Write to several streams at once, so console output is also captured.
    """

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, text):
        for stream in self._streams:
            try:
                stream.write(text)
            except Exception:
                pass

        return len(text)

    def flush(self):
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def start_run_log(name="run"):
    """
    Begin capturing this run's output to log/run<timestamp>.log.

    Returns the log path. Safe to call more than once; later calls are ignored.
    """
    global _log_handle, _log_path, _saved_stdout, _saved_stderr

    if _log_handle is not None:
        return _log_path

    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _log_path = LOG_DIRECTORY / f"{name}{stamp}.log"

    _log_handle = open(_log_path, "w", encoding="utf-8", newline="\n")

    _log_handle.write(
        f"{name} log - {datetime.now().isoformat(timespec='seconds')}\n"
    )
    _log_handle.write(f"command: {' '.join(sys.argv)}\n")
    _log_handle.write("=" * 70 + "\n")

    _saved_stdout = sys.stdout
    _saved_stderr = sys.stderr

    sys.stdout = _Tee(_saved_stdout, _log_handle)
    sys.stderr = _Tee(_saved_stderr, _log_handle)

    _warnings.clear()

    return _log_path


def log_only(message=""):
    """
    Write a line to the run log without echoing it to the console.

    Used for per-row detail that is valuable in the log but would flood the
    terminal at 154 rows.
    """
    if _log_handle is None:
        return

    try:
        _log_handle.write(f"{message}\n")
    except Exception:
        pass


def warn(message, echo=False):
    """
    Record a warning in the run log.

    Written straight to the log file rather than through stdout, because the
    console already carries its own consolidated summaries and echoing every
    warning there would bury them. Pass echo=True for a warning the operator
    should see live.
    """
    message = str(message)

    _warnings.append(message)

    if _log_handle is not None:
        try:
            _log_handle.write(f"WARNING: {message}\n")
        except Exception:
            pass

    if echo:
        print(f"WARNING: {message}")


def logged_warnings():
    """
    Every warning raised through warn() so far, in order.
    """
    return list(_warnings)


def log_path():
    """
    Path of the current run log, or None when logging was never started.
    """
    return _log_path


def close_run_log():
    """
    Append a warning summary, restore the streams, and close the log.
    """
    global _log_handle, _saved_stdout, _saved_stderr

    if _log_handle is None:
        return None

    try:
        _log_handle.write("\n")
        _log_handle.write("=" * 70 + "\n")
        _log_handle.write(f"WARNING SUMMARY ({len(_warnings)})\n")
        _log_handle.write("=" * 70 + "\n")

        if _warnings:
            for index, message in enumerate(_warnings, start=1):
                _log_handle.write(f"{index:4}. {message}\n")
        else:
            _log_handle.write("none\n")

        _log_handle.flush()

    except Exception:
        pass

    if _saved_stdout is not None:
        sys.stdout = _saved_stdout

    if _saved_stderr is not None:
        sys.stderr = _saved_stderr

    try:
        _log_handle.close()
    except Exception:
        pass

    _log_handle = None

    return _log_path


# ============================================================
# GENERAL HELPERS
# ============================================================

def run_command(command, description=None):
    """
    Run an audio tool command and raise a useful error if it fails.

    Goes through audio_tools, so the tool is found by absolute path and run
    without a shell.
    """
    if description:
        print(description)

    result = audio_tools.run(command)

    if result.returncode != 0:
        print()
        print("COMMAND FAILED:")
        print(" ".join(str(x) for x in command))
        print()
        print(result.stderr)
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}"
        )

    return result


def have_ffmpeg():
    """
    Return True when ffmpeg is available: bundled, or on PATH (see
    audio_tools.find()).

    Unlike the original check_ffmpeg(), this reports rather than exiting, so
    the backend selector can probe for ffmpeg and fall back gracefully.
    """
    return _probe_tool("ffmpeg")


def have_ffprobe():
    """
    Return True when ffprobe is available: bundled, or on PATH.
    """
    return _probe_tool("ffprobe")


# Probed once per run: the answer cannot change mid-run, and the pipeline
# asks before every file.
_tool_cache = {}


def _probe_tool(name):
    """
    Confirm an external tool exists and responds to -version.
    """
    if name not in _tool_cache:
        _tool_cache[name] = audio_tools.available(name)

    return _tool_cache[name]


def safe_filename(text):
    """
    Convert a character/speaker name into a Windows-safe filename.
    """
    text = str(text).strip()

    invalid_chars = '<>:"/\\|?*'

    for char in invalid_chars:
        text = text.replace(char, "_")

    text = text.strip(". ")

    return text


def format_duration(seconds):
    """
    Render a duration in seconds as H:MM:SS.mmm for human-readable logs.
    """
    if seconds is None:
        return "unknown"

    total_ms = int(round(seconds * 1000))

    hours, remainder = divmod(total_ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, millis = divmod(remainder, 1000)

    return f"{hours}:{minutes:02d}:{secs:02d}.{millis:03d}"


# ============================================================
# NATIVE MPEG AUDIO FRAME PARSING
# ============================================================
#
# An MP3 file is not a container. It is a bare sequence of self-contained
# MPEG audio frames, optionally wrapped in ID3 tags. That is what makes
# byte-level splicing possible, and it is what the parsing below exposes.
#
# Frame header layout (4 bytes, big-endian bit fields):
#
#     AAAAAAAA AAABBCCD EEEEFFGH IIJJKLMM
#
#     A (11)  frame sync, all ones
#     B (2)   MPEG version ID
#     C (2)   layer
#     D (1)   CRC protection (0 = protected, adds 2 bytes after the header)
#     E (4)   bitrate index
#     F (2)   sample rate index
#     G (1)   padding (adds one slot to this frame)
#     H (1)   private
#     I (2)   channel mode
#     J (2)   mode extension
#     K (1)   copyright
#     L (1)   original
#     M (2)   emphasis
#
# See docs/audio-concat-study.md for the full derivation.

# MPEG version IDs
MPEG_V25 = "2.5"
MPEG_V2 = "2"
MPEG_V1 = "1"
MPEG_RESERVED = None

_VERSION_TABLE = {
    0b00: MPEG_V25,
    0b01: MPEG_RESERVED,
    0b10: MPEG_V2,
    0b11: MPEG_V1,
}

# Layer bits -> layer number
_LAYER_TABLE = {
    0b00: None,
    0b01: 3,
    0b10: 2,
    0b11: 1,
}

_SAMPLE_RATES = {
    MPEG_V1: (44100, 48000, 32000),
    MPEG_V2: (22050, 24000, 16000),
    MPEG_V25: (11025, 12000, 8000),
}

# Bitrates in bits per second, indexed by the 4-bit bitrate index.
# Index 0 is "free format" and index 15 is invalid; both are None here.
_BITRATES_V1_L1 = (
    None, 32, 64, 96, 128, 160, 192, 224,
    256, 288, 320, 352, 384, 416, 448, None,
)
_BITRATES_V1_L2 = (
    None, 32, 48, 56, 64, 80, 96, 112,
    128, 160, 192, 224, 256, 320, 384, None,
)
_BITRATES_V1_L3 = (
    None, 32, 40, 48, 56, 64, 80, 96,
    112, 128, 160, 192, 224, 256, 320, None,
)
_BITRATES_V2_L1 = (
    None, 32, 48, 56, 64, 80, 96, 112,
    128, 144, 160, 176, 192, 224, 256, None,
)
_BITRATES_V2_L23 = (
    None, 8, 16, 24, 32, 40, 48, 56,
    64, 80, 96, 112, 128, 144, 160, None,
)

_BITRATE_TABLE = {
    (MPEG_V1, 1): _BITRATES_V1_L1,
    (MPEG_V1, 2): _BITRATES_V1_L2,
    (MPEG_V1, 3): _BITRATES_V1_L3,
    (MPEG_V2, 1): _BITRATES_V2_L1,
    (MPEG_V2, 2): _BITRATES_V2_L23,
    (MPEG_V2, 3): _BITRATES_V2_L23,
    (MPEG_V25, 1): _BITRATES_V2_L1,
    (MPEG_V25, 2): _BITRATES_V2_L23,
    (MPEG_V25, 3): _BITRATES_V2_L23,
}

CHANNEL_MODE_MONO = 0b11

# Layer III side information size in bytes, keyed by (version, is_mono).
# The Xing/Info tag lives immediately after this block.
_SIDE_INFO_SIZE = {
    (MPEG_V1, True): 17,
    (MPEG_V1, False): 32,
    (MPEG_V2, True): 9,
    (MPEG_V2, False): 17,
    (MPEG_V25, True): 9,
    (MPEG_V25, False): 17,
}


FrameHeader = namedtuple(
    "FrameHeader",
    [
        "raw",                # the 4 header bytes, reused when synthesising frames
        "version",            # "1" | "2" | "2.5"
        "layer",              # 1 | 2 | 3
        "bitrate",            # bits per second
        "sample_rate",        # Hz
        "channel_mode",       # 2-bit field; CHANNEL_MODE_MONO == mono
        "protected",          # True when a 16-bit CRC follows the header
        "padded",             # True when this frame carries a padding slot
        "frame_length",       # total frame size in bytes, padding included
        "samples_per_frame",
        "side_info_size",     # Layer III side info length, 0 for other layers
    ],
)


class Mp3FormatError(ValueError):
    """
    Raised when a byte range cannot be interpreted as MPEG audio.
    """


def parse_frame_header(data, offset=0):
    """
    Parse the MPEG frame header at `offset`.

    Returns a FrameHeader, or None when the bytes at `offset` are not a
    plausible frame header. Returning None rather than raising keeps the
    resync scan in find_first_frame() cheap.
    """
    if offset + 4 > len(data):
        return None

    b0, b1, b2, b3 = data[offset:offset + 4]

    # 11-bit frame sync.
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None

    version = _VERSION_TABLE.get((b1 >> 3) & 0b11)
    layer = _LAYER_TABLE.get((b1 >> 1) & 0b11)

    if version is MPEG_RESERVED or layer is None:
        return None

    protected = not (b1 & 0b1)

    bitrate_index = (b2 >> 4) & 0b1111
    sample_rate_index = (b2 >> 2) & 0b11
    padded = bool((b2 >> 1) & 0b1)

    if sample_rate_index == 0b11:
        return None

    bitrate_kbps = _BITRATE_TABLE[(version, layer)][bitrate_index]

    if bitrate_kbps is None:
        return None

    bitrate = bitrate_kbps * 1000
    sample_rate = _SAMPLE_RATES[version][sample_rate_index]

    channel_mode = (b3 >> 6) & 0b11
    is_mono = channel_mode == CHANNEL_MODE_MONO

    # Samples per frame, and the coefficient used for the frame length.
    #
    # Layer I is measured in 4-byte slots; layers II and III in single bytes.
    # Layer III on MPEG-2 / 2.5 uses half the samples of MPEG-1, which is why
    # 24 kHz mono MP3s have 576-sample frames rather than 1152.
    if layer == 1:
        samples_per_frame = 384
        frame_length = (12 * bitrate // sample_rate + (1 if padded else 0)) * 4
    else:
        if layer == 2:
            samples_per_frame = 1152
        elif version == MPEG_V1:
            samples_per_frame = 1152
        else:
            samples_per_frame = 576

        coefficient = samples_per_frame // 8
        frame_length = coefficient * bitrate // sample_rate + (1 if padded else 0)

    if frame_length <= 4:
        return None

    side_info_size = 0

    if layer == 3:
        side_info_size = _SIDE_INFO_SIZE[(version, is_mono)]

    return FrameHeader(
        raw=bytes(data[offset:offset + 4]),
        version=version,
        layer=layer,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channel_mode=channel_mode,
        protected=protected,
        padded=padded,
        frame_length=frame_length,
        samples_per_frame=samples_per_frame,
        side_info_size=side_info_size,
    )


def format_key(header):
    """
    The tuple of properties that must match across every input for a
    byte-level splice to be valid.

    Padding is deliberately excluded: a padded and an unpadded frame differ by
    one byte but are otherwise the same format and interleave freely.
    """
    return (
        header.version,
        header.layer,
        header.bitrate,
        header.sample_rate,
        header.channel_mode,
    )


def describe_format(header):
    """
    Human-readable one-line summary of a frame format, for logs and errors.
    """
    channels = "mono" if header.channel_mode == CHANNEL_MODE_MONO else "stereo"

    return (
        f"MPEG-{header.version} Layer {header.layer} "
        f"{header.sample_rate} Hz {channels} "
        f"{header.bitrate // 1000} kbps"
    )


def strip_tags(data):
    """
    Return (start, end) byte offsets of the MPEG audio inside `data`,
    excluding ID3 metadata.

    Leaving these tags in place is the main reason naive `copy /b` style
    concatenation misbehaves: an ID3 block from the second file onwards lands
    in the middle of the audio stream, where some decoders emit a click.

    Handles ID3v2 at the head and ID3v1 at the tail. Other trailing metadata
    (APEv2, Lyrics3) is not produced by this pipeline and is left alone; a
    stray trailing block is harmless because it sits after the final frame and
    is skipped by the frame walk anyway.
    """
    start = 0
    end = len(data)

    # ID3v2: "ID3", version (2 bytes), flags (1 byte), syncsafe size (4 bytes).
    while (
        end - start >= 10
        and data[start:start + 3] == b"ID3"
    ):
        size_bytes = data[start + 6:start + 10]

        # Syncsafe integer: 7 significant bits per byte.
        size = 0

        for byte in size_bytes:
            if byte & 0x80:
                # Not a valid syncsafe value; refuse to guess.
                return start, end

            size = (size << 7) | byte

        flags = data[start + 5]
        has_footer = bool(flags & 0x10)

        start += 10 + size + (10 if has_footer else 0)

        if start > end:
            return end, end

    # ID3v1: fixed 128-byte trailer beginning with "TAG".
    if end - start >= 128 and data[end - 128:end - 125] == b"TAG":
        end -= 128

    return start, end


def find_first_frame(data, start=0, end=None, max_scan=65536):
    """
    Locate the first plausible MPEG frame at or after `start`.

    Returns (offset, FrameHeader), or (None, None) when no frame is found
    within `max_scan` bytes.

    A single valid-looking header is not enough — random bytes hit the 11-bit
    sync pattern often enough to matter — so a candidate is only accepted when
    the frame that follows it also parses with the same format.
    """
    if end is None:
        end = len(data)

    limit = min(end, start + max_scan)
    offset = start

    while offset + 4 <= limit:
        header = parse_frame_header(data, offset)

        if header is not None:
            next_offset = offset + header.frame_length

            if next_offset + 4 > end:
                # Not enough room to corroborate, but the header itself is
                # sound and reaches the end of the data. Accept it.
                return offset, header

            follower = parse_frame_header(data, next_offset)

            if follower is not None and format_key(follower) == format_key(header):
                return offset, header

        offset += 1

    return None, None


def iter_frames(data, start=None, end=None):
    """
    Walk the MPEG frames in `data`, yielding (offset, FrameHeader).

    Stops at the first byte range that does not parse as a frame, which is the
    normal way a stream ends (trailing tags, padding, or truncation).
    """
    if start is None or end is None:
        tag_start, tag_end = strip_tags(data)

        if start is None:
            start = tag_start

        if end is None:
            end = tag_end

    offset, header = find_first_frame(data, start, end)

    if header is None:
        return

    while offset + 4 <= end:
        header = parse_frame_header(data, offset)

        if header is None:
            return

        if offset + header.frame_length > end:
            # Truncated final frame; not usable.
            return

        yield offset, header

        offset += header.frame_length


# ============================================================
# XING / INFO SEEK HEADER
# ============================================================

XING_FRAMES_FLAG = 0x0001
XING_BYTES_FLAG = 0x0002
XING_TOC_FLAG = 0x0004

XING_TOC_ENTRIES = 100


def find_xing_offset(data, offset, header):
    """
    Return the offset of the Xing/Info/VBRI magic inside the frame at
    `offset`, or None when the frame carries no seek header.

    Xing and Info live immediately after the header, optional CRC, and Layer
    III side information. VBRI (written by some Fraunhofer encoders) always
    sits 32 bytes past the header.
    """
    if header.layer != 3:
        return None

    frame_end = offset + header.frame_length

    vbri_at = offset + 4 + 32

    if vbri_at + 4 <= frame_end and data[vbri_at:vbri_at + 4] == b"VBRI":
        return vbri_at

    tag_at = offset + 4 + (2 if header.protected else 0) + header.side_info_size

    if tag_at + 4 <= frame_end and data[tag_at:tag_at + 4] in (b"Xing", b"Info"):
        return tag_at

    return None


def is_seek_header_frame(data, offset, header):
    """
    True when the frame at `offset` is a Xing/Info/VBRI metadata frame rather
    than real audio.

    These frames must be dropped from every input during a splice. They carry
    no audio (they decode as a single frame of silence) and their frame counts
    describe only the file they came from, so leaving one in place would give
    the joined stream a wrong duration.
    """
    return find_xing_offset(data, offset, header) is not None


def build_info_frame(header, frame_count, total_bytes, frame_offsets=None):
    """
    Build a CBR "Info" seek header frame matching `header`'s format.

    This is the pure-Python equivalent of FFmpeg's `-write_xing 1`: it gives
    players an authoritative frame count, stream size, and a 100-entry seek
    table so scrubbing lands in the right place.

    `frame_count` and `total_bytes` describe the audio that follows this
    frame, plus this frame itself in the byte count -- matching what LAME
    writes. `frame_offsets` is an optional list of byte offsets (relative to
    the start of the audio) used to build an accurate TOC; without it the TOC
    is computed on the CBR assumption.
    """
    frame = bytearray(header.frame_length)

    # Reuse the real header so version, bitrate, sample rate and channel mode
    # all match the stream exactly.
    frame[0:4] = header.raw

    # Clear the padding bit: this synthesised frame is never padded.
    frame[2] &= ~0b10

    cursor = 4 + (2 if header.protected else 0) + header.side_info_size

    # "Info" rather than "Xing" signals a constant-bitrate stream.
    frame[cursor:cursor + 4] = b"Info"
    cursor += 4

    flags = XING_FRAMES_FLAG | XING_BYTES_FLAG | XING_TOC_FLAG

    frame[cursor:cursor + 4] = flags.to_bytes(4, "big")
    cursor += 4

    frame[cursor:cursor + 4] = frame_count.to_bytes(4, "big")
    cursor += 4

    frame[cursor:cursor + 4] = total_bytes.to_bytes(4, "big")
    cursor += 4

    if cursor + XING_TOC_ENTRIES > header.frame_length:
        raise Mp3FormatError(
            f"Frame of {header.frame_length} bytes is too small to hold an "
            "Info header with a seek table."
        )

    for index in range(XING_TOC_ENTRIES):
        fraction = index / XING_TOC_ENTRIES

        if frame_offsets:
            position = frame_offsets[
                min(
                    int(fraction * len(frame_offsets)),
                    len(frame_offsets) - 1,
                )
            ]
            # Offsets are relative to the audio; the Info frame precedes it.
            byte_position = header.frame_length + position
        else:
            byte_position = int(fraction * total_bytes)

        entry = int(256.0 * byte_position / total_bytes) if total_bytes else 0

        frame[cursor + index] = min(255, max(0, entry))

    return bytes(frame)


def build_silence_frame(header):
    """
    Build a single frame of digital silence matching `header`'s format.

    A Layer III frame whose side information and main data are all zero
    decodes to silence: every scalefactor band has zero length, so no spectral
    coefficients are read. This lets the pure-Python backend produce filler
    audio without FFmpeg's `anullsrc`.
    """
    if header.layer != 3:
        raise Mp3FormatError(
            "Silence synthesis is only implemented for Layer III."
        )

    frame = bytearray(header.frame_length)

    frame[0:4] = header.raw
    frame[2] &= ~0b10  # never padded

    # Everything after the header stays zero: no CRC value is needed because a
    # zeroed side-info block is what we want decoded, and the CRC is only
    # checked by decoders when protection is signalled.
    return bytes(frame)


def reference_frame_header(sample_rate=None, bitrate=None, mono=True):
    """
    Synthesise a FrameHeader for the pipeline's canonical output format.

    Used when a filler must be created before any real audio exists to copy a
    format from. Defaults to the Sarvam TTS format: 24 kHz mono 128 kbps,
    which is MPEG-2 Layer III and therefore 384-byte, 576-sample frames.
    """
    sample_rate = sample_rate or SPEECH_SAMPLE_RATE
    bitrate = bitrate or FILLER_BITRATE

    for version, rates in _SAMPLE_RATES.items():
        if sample_rate not in rates:
            continue

        bitrates = _BITRATE_TABLE[(version, 3)]
        bitrate_kbps = bitrate // 1000

        if bitrate_kbps not in bitrates:
            continue

        version_bits = next(
            bits for bits, name in _VERSION_TABLE.items() if name == version
        )

        b1 = 0xE0 | (version_bits << 3) | (0b01 << 1) | 0b1
        b2 = (bitrates.index(bitrate_kbps) << 4) | (rates.index(sample_rate) << 2)
        b3 = (CHANNEL_MODE_MONO if mono else 0b00) << 6

        header = parse_frame_header(bytes([0xFF, b1, b2, b3]))

        if header is not None:
            return header

    raise Mp3FormatError(
        f"No MPEG Layer III format exists for {sample_rate} Hz at "
        f"{bitrate // 1000} kbps."
    )


# ============================================================
# VALIDATION
# ============================================================

def is_valid_audio(filename, verbose=True):
    """
    Check whether an audio file is actually decodable.

    Prefers FFmpeg's full decode pass, which catches files that exist but are
    corrupt or incomplete:

        Failed to find two consecutive MPEG audio frames.

    When FFmpeg is unavailable, MP3s fall back to a native frame scan so the
    stitch stage still validates its inputs with no external binary. Other
    formats can only be checked for existence in that case.
    """
    filename = Path(filename)

    if not filename.exists():
        return False

    if filename.stat().st_size == 0:
        return False

    if have_ffmpeg():
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(filename),
            "-f",
            "null",
            "-",
        ]

        result = audio_tools.run(command)

        if result.returncode != 0:
            if verbose:
                print()
                print(f"INVALID: {filename}")
                print(result.stderr.strip())
                print()

            return False

        return True

    if filename.suffix.lower() == ".mp3":
        return is_valid_mp3_native(filename, verbose=verbose)

    if verbose:
        print(
            f"WARNING: cannot deep-validate {filename.name} without ffmpeg; "
            "accepting on existence alone."
        )

    return True


def is_valid_mp3_native(filename, verbose=True, min_frames=2):
    """
    Validate an MP3 by walking its frame chain, with no external tools.

    A file passes when at least `min_frames` consecutive frames parse with a
    consistent format. This is the same condition FFmpeg reports on when it
    fails with "two consecutive MPEG audio frames".
    """
    filename = Path(filename)

    try:
        data = filename.read_bytes()

    except OSError as exc:
        if verbose:
            print(f"INVALID: {filename} ({exc})")

        return False

    seen = 0
    first_key = None

    for offset, header in iter_frames(data):
        if first_key is None:
            first_key = format_key(header)

        seen += 1

        if seen >= min_frames:
            return True

    if verbose:
        print()
        print(f"INVALID MP3: {filename}")
        print(
            f"Found {seen} usable MPEG frame(s); need at least {min_frames}."
        )
        print()

    return False


def get_audio_duration(filename):
    """
    Return audio duration in seconds.

    Uses ffprobe when available, otherwise computes the duration natively for
    MP3 by summing frame sample counts. Returns None when the duration cannot
    be determined.
    """
    filename = Path(filename)

    if have_ffprobe():
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(filename),
        ]

        result = audio_tools.run(command)

        if result.returncode == 0:
            try:
                return float(result.stdout.strip())
            except ValueError:
                pass

    if filename.suffix.lower() == ".mp3":
        return get_mp3_duration_native(filename)

    return None


def get_mp3_duration_native(filename):
    """
    Compute an MP3's duration by summing per-frame sample counts.

    Exact for the CBR streams this pipeline produces, and correct for VBR too
    since each frame's own header supplies its sample count.
    """
    try:
        data = Path(filename).read_bytes()

    except OSError:
        return None

    samples = 0
    sample_rate = None

    for offset, header in iter_frames(data):
        if is_seek_header_frame(data, offset, header):
            continue

        samples += header.samples_per_frame
        sample_rate = header.sample_rate

    if not samples or not sample_rate:
        return None

    return samples / sample_rate


# ============================================================
# CSV
# ============================================================

# Columns the stitch stage genuinely needs.
REQUIRED_COLUMNS = {
    "sequence_number",
    "character_name",
    "dialogue_text",
}

# The speaker column feeds Sarvam's `speaker` TTS parameter. Generation is
# currently disabled, so its absence is not an error. Either spelling is
# accepted; "speaker_name" wins when both are present.
SPEAKER_COLUMNS = ("speaker_name", "speaker")

# Optional column naming the audio to insert immediately BEFORE this row's
# dialogue. An empty cell means "use SHORT_FILLER"; see NO_FILLER_TOKENS for how
# to ask for no leading filler at all.
FILLER_COLUMN = "filler_before"

# Optional column naming the audio to insert immediately AFTER this row's
# dialogue.
#
# The empty-cell rule is deliberately NOT the same as FILLER_COLUMN's: an empty
# "filler_after" cell means no trailing audio whatsoever, not a default gap.
# Only an explicitly named file is ever appended. Rows therefore keep their
# leading gap by default and gain a trailing one only where the CSV asks.
FILLER_AFTER_COLUMN = "filler_after"

# Optional pair of columns overlaying a background track on the row's dialogue;
# see background_score.py and section 10 of docs/audio-concat-study.md. Both
# empty means no mix. Setting only one of the two is reported as a fault.
SCORE_COLUMN = "background_score"
SCORE_CUES_COLUMN = "score_cues"

# Cell values that mean "no filler here at all", as opposed to an empty cell.
#
# This only adds expressive power to "filler_before", where an empty cell
# already means SHORT_FILLER and there would otherwise be no way to butt one
# row's audio straight up against the previous row's. Accepted in
# "filler_after" too, where it is simply a louder way of writing an empty cell.
#
# Compared case-insensitively. No real filler can collide with these, since
# filler cells name files and a filename carries an extension.
NO_FILLER_TOKENS = {"n", "no", "none"}


def is_no_filler(value):
    """
    True when a filler cell explicitly asks for no filler rather than naming a
    file or being left blank.
    """
    return str(value or "").strip().lower() in NO_FILLER_TOKENS


def load_csv(csv_file=None, row_limit=None):
    """
    Load dialogue records from the dialogue CSV.

    Required columns:

        sequence_number
        character_name
        dialogue_text

    Optional columns:

        speaker_name  (or  speaker)
        filler_before
        filler_after
        background_score
        score_cues

    Returns a list of dicts with keys: sequence_number, character_name,
    dialogue_text, speaker, filler_before, filler_after, background_score,
    score_cues, row_number. `speaker`
    is None when the CSV has no speaker column, or when a row leaves it blank.
    `filler_before` is None when the cell is empty, meaning "use SHORT_FILLER".
    `filler_after` is None when the cell is empty, meaning "append nothing".
    Either may hold one of NO_FILLER_TOKENS ("N") to suppress that filler; the
    raw cell text is preserved here and interpreted by filler_for() /
    filler_after_for(). `background_score` and `score_cues` are None when
    empty; both empty means the row is not mixed.
    """
    csv_file = Path(csv_file) if csv_file else CSV_FILE

    if not csv_file.exists():
        raise FileNotFoundError(
            f"CSV file not found: {csv_file}"
        )

    records = []

    with open(
        csv_file,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise RuntimeError(
                f"{csv_file.name} has no header row."
            )

        fieldnames = set(reader.fieldnames)

        missing = REQUIRED_COLUMNS - fieldnames

        if missing:
            raise RuntimeError(
                f"{csv_file.name} is missing columns: "
                + ", ".join(sorted(missing))
            )

        speaker_column = next(
            (name for name in SPEAKER_COLUMNS if name in fieldnames),
            None,
        )

        if speaker_column is None:
            print(
                f"NOTE: {csv_file.name} has no "
                f"{' / '.join(SPEAKER_COLUMNS)} column. "
                "This is only needed for TTS generation, which is disabled."
            )

        for row_number, row in enumerate(reader, start=2):

            sequence_number = str(
                row.get("sequence_number") or ""
            ).strip()

            character_name = str(
                row.get("character_name") or ""
            ).strip()

            dialogue_text = str(
                row.get("dialogue_text") or ""
            ).strip()

            speaker = None

            if speaker_column:
                speaker = str(row.get(speaker_column) or "").strip() or None

            filler_before = (
                str(row.get(FILLER_COLUMN) or "").strip() or None
            )

            filler_after = (
                str(row.get(FILLER_AFTER_COLUMN) or "").strip() or None
            )

            background_score = (
                str(row.get(SCORE_COLUMN) or "").strip() or None
            )

            score_cues = (
                str(row.get(SCORE_CUES_COLUMN) or "").strip() or None
            )

            if not sequence_number:
                print(
                    f"WARNING: Row {row_number} has no "
                    "sequence_number. Skipping."
                )
                continue

            if not dialogue_text:
                print(
                    f"WARNING: Row {row_number} has empty "
                    "dialogue_text. Skipping."
                )
                continue

            records.append(
                {
                    "sequence_number": sequence_number,
                    "character_name": character_name,
                    "dialogue_text": dialogue_text,
                    "speaker": speaker,
                    "filler_before": filler_before,
                    "filler_after": filler_after,
                    "background_score": background_score,
                    "score_cues": score_cues,
                    "row_number": row_number,
                }
            )

    if row_limit is not None:
        records = records[:row_limit]

    return records


def filler_for(record, directories=None):
    """
    Resolve the audio to insert immediately before a record's dialogue.

    Returns (path, is_default):

        (SHORT_FILLER, True)    empty cell -- use the default gap
        (named_path,   False)   a named file, looked up across every search
                                directory in precedence order, so filler audio
                                fetched into human_gen/ is found as readily as
                                one placed in machine_gen/
        (None,         False)   the cell is one of NO_FILLER_TOKENS ("N") --
                                add no leading filler at all, so this row's
                                audio follows the previous row directly

    When a named file is nowhere to be found, its expected path in the primary
    directory is returned so the caller can report what was missing.
    """
    name = (record.get("filler_before") or "").strip()

    if not name:
        return SHORT_FILLER, True

    if is_no_filler(name):
        return None, False

    found = find_named_audio(name, directories)

    if found is not None:
        return found, False

    return OUT_DIRECTORY / name, False


def filler_after_for(record, directories=None):
    """
    Resolve the audio to insert immediately after a record's dialogue.

    Returns (path, found).

    An empty "filler_after" cell yields (None, True): nothing is appended, and
    that is not a problem to report. This is the deliberate asymmetry with
    filler_for() -- there is no default trailing filler, because a default one
    would put a gap after every line including the last. A cell holding one of
    NO_FILLER_TOKENS ("N") is treated the same as empty.

    A named file is looked up the same way as a leading filler. When it cannot
    be found, (expected_path, False) is returned so the caller can report the
    miss; nothing should be appended in that case, since the row asked for one
    specific sound and substituting silence would misrepresent the CSV.
    """
    name = (record.get("filler_after") or "").strip()

    if not name or is_no_filler(name):
        return None, True

    found = find_named_audio(name, directories)

    if found is not None:
        return found, True

    return OUT_DIRECTORY / name, False


def dialogue_path(record, out_directory=None):
    """
    The canonical MP3 path a dialogue record maps to.

    This naming is the contract the generation stage writes to:

        {sequence_number}-{safe character_name}.mp3

    The stitch stage does NOT require this exact spelling -- see
    find_dialogue_file(), which matches on the sequence number alone so that
    files delivered as e.g. "129_Krishna.m4a" are still picked up.
    """
    out_directory = Path(out_directory) if out_directory else OUT_DIRECTORY

    safe_character = safe_filename(record["character_name"])

    return out_directory / f"{record['sequence_number']}-{safe_character}.mp3"


# Leading run of digits in a filename stem, which is the sequence number.
_SEQUENCE_PATTERN = re.compile(r"^(\d+)")


def sequence_of(path):
    """
    Extract the leading sequence number from a filename, or None.

        1-DURYODHANA.mp3   -> 1
        10_YUDHISHTHIRA.m4a -> 10
        129_Krishna.m4a    -> 129
        samay-1.mp3        -> None
        short_filler.mp3   -> None
    """
    match = _SEQUENCE_PATTERN.match(Path(path).stem)

    return int(match.group(1)) if match else None


def normalize_character(text):
    """
    Reduce a character name to a comparison key: casefolded, with everything
    but letters and digits removed.

        VOICE OF TIME  -> voiceoftime
        Krishna        -> krishna
    """
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


def character_token(path):
    """
    The character name a dialogue filename carries, as a comparison key, or ""
    when it carries none.

        4-SHAKUNI.mp3        -> shakuni
        129_Krishna.m4a      -> krishna
        0007-VOICE OF TIME.mp3 -> voiceoftime
        4.mp3                -> ""

    Only the leading sequence number is stripped. The separator after it needs
    no special handling, because normalize_character() drops it along with
    every other non-alphanumeric.
    """
    stem = Path(path).stem
    match = _SEQUENCE_PATTERN.match(stem)

    if match:
        stem = stem[match.end():]

    return normalize_character(stem)


def reserved_names(out_directory=None):
    """
    Filenames in the output directory that are pipeline artifacts rather than
    dialogue audio, and must never be treated as inputs.

    Filler audio named by the CSV's "filler_before" or "filler_after" column is
    excluded from dialogue discovery by a different rule: those files carry no
    leading sequence number, and both discovery paths require one.
    """
    names = {
        MASTER_FILE.name,
        SHORT_FILLER.name,
        LONG_FILLER.name,
    }

    return names


def iter_candidate_files(out_directory=None, extensions=None):
    """
    Yield every plausible dialogue audio file in one directory.

    Skips reserved names, underscore-prefixed temporaries (which includes the
    _normalized cache directory), and unrecognised extensions.
    """
    out_directory = Path(out_directory) if out_directory else OUT_DIRECTORY
    extensions = extensions or INPUT_EXTENSIONS

    if not out_directory.exists():
        return

    reserved = reserved_names(out_directory)
    allowed = {ext.lower() for ext in extensions}

    for path in sorted(out_directory.iterdir()):
        if not path.is_file():
            continue

        if path.name.startswith("_"):
            continue

        if path.name in reserved:
            continue

        if path.suffix.lower() not in allowed:
            continue

        yield path


def find_dialogue_file(record, directories=None, extensions=None):
    """
    Locate the audio file for a dialogue record by sequence number.

    Directories are searched in order and the FIRST one holding a match wins
    outright -- a recording in human_gen/ is never overridden by TTS output in
    machine_gen/, whatever the timestamps say. Within a single directory, the
    most recently modified file wins.

    Matching is deliberately tolerant: the sequence number is the only thing
    that must agree. Separator (``-`` or ``_``), character-name spelling and
    case, and file extension may all differ from the canonical form, because
    files arrive from more than one tool.

    Returns (path or None, alternatives, source_directory or None, collisions).

    `alternatives` lists every other file that matched -- in the winning
    directory and in the ones searched after it. It is for reporting only: the
    lower-precedence matches are the designed human_gen -> machine_gen fallback,
    not a fault.

    `collisions` is the subset that matters: other files for the same take in
    the WINNING directory. Those make the choice genuinely ambiguous, and are
    what the take-id collision check aborts on.
    """
    try:
        wanted = int(str(record["sequence_number"]).strip())
    except (ValueError, TypeError):
        return None, [], None, []

    directories = directories or search_directories()

    chosen = None
    source = None
    alternatives = []
    collisions = []

    for directory in directories:
        matches = [
            path
            for path in iter_candidate_files(directory, extensions)
            if sequence_of(path) == wanted
        ]

        if not matches:
            continue

        # Newest first; a freshly delivered file supersedes an older one.
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if chosen is None:
            chosen = matches[0]
            source = Path(directory)
            alternatives.extend(matches[1:])
            collisions.extend(matches[1:])
        else:
            # Lower-precedence directory: everything here is an alternative.
            alternatives.extend(matches)

    return chosen, alternatives, source, collisions


def find_named_audio(name, directories=None):
    """
    Locate a file referenced by name -- filler audio from the CSV's
    "filler_before" or "filler_after" column -- across the search directories,
    in order.

    WORK_DIRECTORY is searched last so that a row may name a generated filler
    such as long_filler.mp3 and have it resolve, while a real recording of the
    same name in human_gen/ still takes precedence.

    Returns the path, or None when no directory holds it.
    """
    directories = directories or search_directories()

    for directory in (*directories, WORK_DIRECTORY):
        candidate = Path(directory) / name

        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def canonical_target_header():
    """
    The FrameHeader every segment must match before stitching.
    """
    return reference_frame_header(
        sample_rate=TARGET_SAMPLE_RATE,
        bitrate=TARGET_BITRATE,
        mono=TARGET_MONO,
    )


def needs_normalizing(path, target=None):
    """
    True when `path` must be transcoded before it can be spliced.

    Non-MP3 containers always need it -- AAC cannot be carried in an MP3
    stream. MP3s need it only when their frame format differs from the target.
    """
    path = Path(path)
    target = target or canonical_target_header()

    if path.suffix.lower() != ".mp3":
        return True

    try:
        data = path.read_bytes()
    except OSError:
        return True

    for offset, header in iter_frames(data):
        if is_seek_header_frame(data, offset, header):
            continue

        return format_key(header) != format_key(target)

    # No readable frames; let the transcoder try.
    return True



USAGE = """\
Usage:
    python stitch_mp3.py [ROWS] [strict=0|1] [dry_run=0|1] [loudness=0|1]

    ROWS        optional positive integer; process only the first ROWS
                dialogue rows. Omit to process everything.

    strict=1    abort if any expected dialogue MP3 is missing, corrupt, or
                in a mismatched audio format. Nothing is written. (default)

    strict=0    stitch anyway: skip the unusable files, drop format
                outliers, and report what was left out.

    dry_run=1   run every check, including normalization, report everything
                found, and stop before stitching. strict is ignored. Exits
                non-zero when a mandatory check fails. Requires FFmpeg, and
                does populate work/ (normalized cache, generated fillers);
                only the master is guaranteed untouched.

    loudness=0  leave every line at its recorded level for this run, e.g. to
                compare before and after. The default, 1, brings each line
                to loudness.target_lufs in dice-skit.json.

    Defaults for strict and loudness come from dice-skit.json; see
    python settings.py.

    Three checks abort regardless of strict: dialogues_new.csv missing, a
    file with no character name, and two files for one take in the same
    directory. A file whose character name differs from its CSV row aborts
    only under strict=1; strict=0 lists it and uses the file.

Examples:
    python stitch_mp3.py                  all rows, strict
    python stitch_mp3.py 100              first 100 rows, strict
    python stitch_mp3.py strict=0         all rows, best effort
    python stitch_mp3.py 10 strict=0      first 10 rows, best effort
    python stitch_mp3.py dry_run=1        validate everything, write no master\
"""

# Accepted spellings for the strict flag's value. The canonical form is 0/1;
# the rest are accepted because they cost nothing and are easy to reach for.
_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


Arguments = namedtuple(
    "Arguments",
    ["row_limit", "strict", "strict_source", "dry_run", "loudness"],
)


def _parse_flag(name, value):
    """
    Interpret a 0/1 flag value, exiting with USAGE on anything else.
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


def parse_arguments(argv, strict_default=None):
    """
    Parse the optional command-line arguments.

    Accepts, in any order:

        a positive integer     row limit
        strict=0 / strict=1    override STRICT_MISSING for this run
        dry_run=0 / dry_run=1  validate only; never stitch (also: dry=1)
        loudness=0 / loudness=1  override loudness.enabled for this run

    Leading dashes on the flag (--strict=0) are tolerated. When the flag is
    absent, `strict` falls back to `strict_default`, or to the STRICT_MISSING
    module setting when that is None.

    Returns an Arguments namedtuple. `strict_source` is "command line" or
    "audio_common.STRICT_MISSING", for use in log messages.

    Exits with status 1 and prints USAGE on any unrecognised argument.
    """
    row_limit = None
    strict = None
    dry_run = False
    level = None

    for argument in argv[1:]:
        token = argument.strip()

        if not token:
            continue

        if token.lower() in ("-h", "--help", "/?", "help"):
            print(USAGE)
            sys.exit(0)

        key, separator, value = token.lstrip("-/").partition("=")

        if separator and key.lower() in ("strict", "strict_missing"):
            strict = _parse_flag("strict", value)

            continue

        if separator and key.lower() in ("dry", "dry_run"):
            dry_run = _parse_flag("dry_run", value)

            continue

        if separator and key.lower() == "loudness":
            level = _parse_flag("loudness", value)

            continue

        # Anything else must be the row limit.
        try:
            candidate = int(token)

        except ValueError:
            print(f'ERROR: unrecognised argument "{argument}".')
            print()
            print(USAGE)
            sys.exit(1)

        if candidate <= 0:
            print(
                f"ERROR: row limit must be a positive integer, got "
                f"{candidate}."
            )
            print()
            print(USAGE)
            sys.exit(1)

        if row_limit is not None:
            print(
                f"ERROR: row limit given more than once "
                f"({row_limit} and {candidate})."
            )
            print()
            print(USAGE)
            sys.exit(1)

        row_limit = candidate

    if strict is None:
        strict = STRICT_MISSING if strict_default is None else strict_default
        strict_source = (
            SETTINGS_SOURCES["run"]["strict"]
            if strict_default is None
            else "caller default"
        )
    else:
        strict_source = "command line"

    return Arguments(
        row_limit=row_limit,
        strict=strict,
        strict_source=strict_source,
        dry_run=dry_run,
        loudness=DIALOGUE_LOUDNESS if level is None else level,
    )


def parse_row_limit(argv):
    """
    Backwards-compatible helper returning only the row limit.
    """
    return parse_arguments(argv).row_limit