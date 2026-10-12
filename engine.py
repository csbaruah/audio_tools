"""
Engine API: the pipeline as functions, for the desktop app and any other
front end. Section 13.3 of docs/audio-concat-study.md specifies it.

    open_project(folder)          -> Project summary
    list_lines()                  -> one LineStatus per CSV row
    check(on_event)               -> Result  (the dry run)
    build(options, on_event)      -> Result  (writes the master)

The command line and this API run the SAME code: build() and check() call
stitch_mp3.main(), so the pipeline's behaviour, checks and report are
identical whichever way it is started. The difference is only in delivery:
here every report line, warning and stage change is passed to `on_event`
as it happens, instead of only being printed.

Every call works on the project opened last. One build runs at a time; a
second call while one is running raises EngineBusy.
"""

import io
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import audio_common as common
import settings


class EngineBusy(RuntimeError):
    """
    Raised when a check or build is requested while one is already running.
    """


class ProjectError(ValueError):
    """
    Raised when a folder cannot be opened as a project.
    """


@dataclass
class Project:
    """
    What the Project screen shows.
    """

    folder: Path
    has_csv: bool
    has_settings_file: bool
    lines: int = 0
    recorded: int = 0
    generated: int = 0
    missing: int = 0
    master: Path = None
    master_exists: bool = False


@dataclass
class LineStatus:
    """
    One CSV row and the audio it resolved to. The Lines screen shows these.
    """

    row: int
    take: str
    character: str
    text: str
    file: Path = None
    source: str = ""
    status: str = "ok"          # ok | missing | mismatch | no-character | collision | mixed
    detail: str = ""


@dataclass
class Event:
    """
    One thing that happened during a check or build.

    kind is "stage" (a new report section began), "line" (a line of the
    report) or "done".
    """

    kind: str
    text: str = ""
    stage: str = ""
    progress: float = 0.0


@dataclass
class Result:
    """
    The outcome of a check or build.
    """

    ok: bool
    exit_code: int
    report: list = field(default_factory=list)   # every report line, in order
    sections: dict = field(default_factory=dict) # section title -> its lines
    warnings: list = field(default_factory=list)
    master: Path = None
    duration: str = ""
    log: Path = None


# Report sections in the order a full build prints them. The share of the run
# each one marks the start of drives the progress bar.
STAGES = [
    "RESOLVING INPUTS",
    "PRE-FLIGHT",
    "MANDATORY CHECKS",
    "DEFAULT FILLER",
    "ROW FILLERS",
    "LOUDNESS",
    "BACKGROUND SCORE",
    "NORMALIZING",
    "BACKEND",
    "STITCHING",
    "FINALIZING",
    "ALL DONE",
]

_ENDINGS = {"ABORTED", "ABORTED - MANDATORY CHECKS FAILED", "DRY RUN COMPLETE", "ERROR"}

_lock = threading.Lock()
_project = None


# ============================================================
# PROJECT
# ============================================================

def open_project(folder):
    """
    Make `folder` the active project and summarise it.

    Raises ProjectError when the folder does not exist, and
    settings.SettingsError when its dice-skit.json is invalid.
    """
    global _project

    folder = Path(folder).expanduser().resolve()

    if not folder.is_dir():
        raise ProjectError(f"{folder} is not a folder")

    with _lock:
        common.configure(folder)
        _project = folder

    return summary()


def summary():
    """
    Counts for the Project screen, for the active project.
    """
    folder = _require_project()

    project = Project(
        folder=folder,
        has_csv=common.CSV_FILE.exists(),
        has_settings_file=common.SETTINGS_FILE.exists(),
        master=common.MASTER_FILE,
        master_exists=common.MASTER_FILE.exists(),
    )

    if not project.has_csv:
        return project

    for line in list_lines():
        project.lines += 1

        if line.file is None:
            project.missing += 1
        elif line.source == common.HUMAN_GEN_DIRECTORY.name:
            project.recorded += 1
        else:
            project.generated += 1

    return project


def project_folder():
    """
    The active project folder, or None.
    """
    return _project


def _require_project():
    if _project is None:
        raise ProjectError("no project is open")

    return _project


# ============================================================
# LINES
# ============================================================

def list_lines():
    """
    Every CSV row with the file it resolved to and its status.

    Uses the same matching and the same four mandatory-check rules as a build
    (section 9.6), so a fault shown here is exactly what a build would report.
    A "mismatch" stops only a strict build; the other faults stop any build.
    Reads files only; nothing is converted or written.
    """
    _require_project()

    lines = []
    claimed = {}

    for record in common.load_csv():
        path, _, source, collisions = common.find_dialogue_file(record)

        line = LineStatus(
            row=record["row_number"],
            take=record["sequence_number"],
            character=record["character_name"],
            text=record["dialogue_text"],
            file=path,
            source=source.name if source else "",
        )

        if path is None:
            line.status = "missing"
            line.detail = "no file for this take in " + ", ".join(
                d.name for d in common.search_directories()
            )

        else:
            token = common.character_token(path)

            if not token:
                line.status = "no-character"
                line.detail = f"{path.name} has no character name; rename it to {record['sequence_number']}-{record['character_name']}{path.suffix}"

            elif token != common.normalize_character(record["character_name"]):
                line.status = "mismatch"
                line.detail = (
                    f"{path.name} names a different character than {record['character_name']}. "
                    "A strict build stops here; with strict off the file is used as it is."
                )

            elif collisions:
                line.status = "collision"
                line.detail = "more than one file for this take: " + ", ".join(
                    p.name for p in [path, *collisions]
                )

            elif record.get("background_score"):
                line.status = "mixed"
                line.detail = f"background {record['background_score']} [{record.get('score_cues') or 'no cues'}]"

        lines.append(line)

    return lines


# ============================================================
# CHECK AND BUILD
# ============================================================

def check(on_event=None):
    """
    Run every check without writing the master: the dry run (section 9.8).
    """
    return _run(["dry_run=1"], on_event)


def build(strict=None, loudness=None, rows=None, on_event=None):
    """
    Build the master.

    strict / loudness override dice-skit.json for this run when not None.
    rows limits the build to the first N rows.
    """
    args = []

    if rows:
        args.append(str(int(rows)))

    if strict is not None:
        args.append(f"strict={int(bool(strict))}")

    if loudness is not None:
        args.append(f"loudness={int(bool(loudness))}")

    return _run(args, on_event)


def _run(args, on_event):
    """
    Run stitch_mp3.main() with `args`, streaming its report to on_event.
    """
    import stitch_mp3

    _require_project()

    if not _lock.acquire(blocking=False):
        raise EngineBusy("a check or build is already running")

    sink = _EventSink(on_event)
    saved_argv = sys.argv
    saved_stdout, saved_stderr = sys.stdout, sys.stderr

    try:
        sys.argv = ["stitch_mp3.py", *args]
        sys.stdout = sys.stderr = sink

        try:
            code = stitch_mp3.main()

        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1

        except Exception as exc:
            # The command line prints these under an ERROR banner; so do we.
            print()
            print("=" * 70)
            print("ERROR")
            print("=" * 70)
            print(exc)
            code = 1

        finally:
            log = common.close_run_log()

    finally:
        sys.argv = saved_argv
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
        sink.flush()
        _lock.release()

    result = Result(
        ok=code == 0,
        exit_code=code,
        report=sink.lines,
        sections=sink.sections,
        warnings=common.logged_warnings(),
        log=log,
    )

    if code == 0 and "ALL DONE" in sink.sections:
        result.master = common.MASTER_FILE

        for line in sink.sections["ALL DONE"]:
            if line.startswith("Duration"):
                result.duration = line.split(":", 1)[1].strip()

    sink.emit(Event("done", progress=1.0))

    return result


class _EventSink(io.TextIOBase):
    """
    Stands in for stdout during a run: splits the report into lines, groups
    them by section, and turns each into an Event.
    """

    _RULE = re.compile(r"^=+$")

    def __init__(self, on_event):
        self.on_event = on_event
        self.lines = []
        self.sections = {}
        self._buffer = ""
        self._section = "START"
        self._pending_rule = False

    def writable(self):
        return True

    def write(self, text):
        self._buffer += text

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._line(line)

        return len(text)

    def flush(self):
        if self._buffer:
            self._line(self._buffer)
            self._buffer = ""

    def _line(self, line):
        line = line.rstrip("\r")

        # A section heading is a line between two rules of "=".
        if self._RULE.match(line):
            self._pending_rule = True
            return

        if self._pending_rule and line and not self._RULE.match(line):
            self._pending_rule = False

            if line in STAGES or line in _ENDINGS:
                self._section = line
                self.sections.setdefault(line, [])

                share = STAGES.index(line) / len(STAGES) if line in STAGES else 1.0
                self.emit(Event("stage", stage=line, progress=share))

                return

        self._pending_rule = False
        self.lines.append(line)
        self.sections.setdefault(self._section, []).append(line)
        self.emit(Event("line", text=line, stage=self._section))

    def emit(self, event):
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                # A broken display must never stop a build.
                pass


# ============================================================
# SETTINGS
# ============================================================

def load_settings():
    """
    (values, sources) for the active project's dice-skit.json.
    """
    _require_project()

    return settings.load(common.SETTINGS_FILE)


def save_settings(values):
    """
    Check and save settings for the active project, keeping the file's
    comments, then apply them.

    Raises settings.SettingsError, without saving, when a value is invalid.
    """
    folder = _require_project()

    for section, fields in values.items():
        for key, value in fields.items():
            settings.SPEC[section][key].check(f"{section}.{key}", value)

    settings._cross_check(values, common.SETTINGS_FILE.name)
    settings.write(common.SETTINGS_FILE, values)

    with _lock:
        common.configure(folder)