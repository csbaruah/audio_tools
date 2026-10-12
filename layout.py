"""
Project folder layout: input, in-out and output (section 14 of
docs/audio-concat-study.md), plus the one-time move from the old layout and
the "clean" command.

    dice-skit/
      input/          dialogues_new.csv, human_gen/            (never deleted)
      in-out/         machine_gen/                             (never deleted)
      output/         master/, work/, log/                     (rebuildable)

The old layout kept everything at the top of the project:

      dialogues_new.csv, human_gen/, machine_gen/, dialogues-mp3/, work/, log/

migrate() moves an old-layout project into the new one. It runs on the first
run after this change, because the user asked for it to be automatic. It is
deliberately conservative, since it moves the user's recordings:

  * It moves, never copies or deletes, so nothing is duplicated or lost.
  * It never overwrites. If any destination already exists, it moves NOTHING
    and reports why, so a project is never left half-moved.
  * It rewrites dice-skit.json's paths only after every move succeeded.
  * Every move is listed on the console and in a migrate log.

clean() deletes the contents of the master and work folders, and nothing
else, after asking for confirmation.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path


class LayoutError(RuntimeError):
    """
    Raised when the folders cannot be laid out or cleaned safely.
    """


# Old top-level names, and where each goes in the new layout, relative to the
# project. The old master folder's contents go into output/master.
_OLD_TO_NEW = [
    ("dialogues_new.csv", "input/dialogues_new.csv"),
    ("human_gen", "input/human_gen"),
    ("machine_gen", "in-out/machine_gen"),
    ("dialogues-mp3", "output/master"),
    ("work", "output/work"),
    ("log", "output/log"),
]

# Old dice-skit.json path keys, with the values they had by default. A file
# still holding these (or a subset) is an old-layout file.
_OLD_PATH_DEFAULTS = {
    "csv": "dialogues_new.csv",
    "human_gen": "human_gen",
    "machine_gen": "machine_gen",
    "work": "work",
    "log": "log",
    "output": "dialogues-mp3",
}

_NEW_ONLY_KEYS = {"input", "in_out", "master_dir"}


# ============================================================
# DETECTION
# ============================================================

def _settings_paths(project):
    """
    The "paths" object in the project's dice-skit.json, or None.
    """
    path = Path(project) / "dice-skit.json"

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    paths = data.get("paths")

    return paths if isinstance(paths, dict) else None


def settings_are_old(project):
    """
    True when dice-skit.json still describes the old layout: it has a paths
    section with none of the new top-level keys, and its output value is the
    old master folder.
    """
    paths = _settings_paths(project)

    if not paths:
        return False

    values = {k: v for k, v in paths.items() if not k.startswith("_")}

    if _NEW_ONLY_KEYS & values.keys():
        return False

    return values.get("output", "dialogues-mp3") == "dialogues-mp3"


def old_items(project):
    """
    (old, new) absolute paths for every old-layout item present in `project`.
    """
    project = Path(project)

    return [
        (project / old, project / new)
        for old, new in _OLD_TO_NEW
        if (project / old).exists()
    ]


def needs_migration(project):
    """
    True when the project uses the old layout and should be moved.
    """
    project = Path(project)

    if (project / "input").exists() or (project / "output").exists():
        # Already (partly) in the new layout; never guess, never move.
        return False

    return bool(old_items(project)) or settings_are_old(project)


# ============================================================
# MIGRATION
# ============================================================

def migrate(project, say=print):
    """
    Move an old-layout project into the new layout.

    Returns the list of (old, new) moves made, empty when there was nothing to
    do. Raises LayoutError, having moved nothing, when a destination already
    exists or the settings file cannot be rewritten.
    """
    project = Path(project).resolve()

    if not needs_migration(project):
        return []

    moves = old_items(project)

    # Check everything before touching anything.
    clashes = [new for _, new in moves if new.exists()]

    if clashes:
        raise LayoutError(
            "cannot move to the new folder layout, because these already "
            "exist: " + ", ".join(str(p.relative_to(project)) for p in clashes)
            + ". Nothing was moved."
        )

    new_settings = _rewritten_settings(project)

    say()
    say("=" * 70)
    say("MOVING TO THE NEW FOLDER LAYOUT (one time)")
    say("=" * 70)
    say("Inputs go to input\\, TTS takes to in-out\\, everything rebuildable to")
    say("output\\. Files are moved, not copied; nothing is deleted.")
    say()

    done = []

    try:
        for old, new in moves:
            new.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, new)
            done.append((old, new))
            say(f"    {old.relative_to(project)}  ->  {new.relative_to(project)}")

    except OSError as exc:
        # Put back what was already moved, so the project is never half-moved.
        for moved_old, moved_new in reversed(done):
            try:
                os.replace(moved_new, moved_old)
            except OSError:
                pass

        # Remove any destination folders this attempt created, if empty, so
        # the next run sees the old layout and tries again.
        for folder in ("input", "in-out", "output"):
            _remove_empty_tree(project / folder)

        raise LayoutError(
            f"could not move {old.name}: {exc}. Everything already moved was "
            "put back. Close any program using these files (e.g. "
            "dialogues_new.csv open in Excel) and run again."
        )

    if new_settings is not None:
        path, text = new_settings
        temp = path.with_name(f"_{path.name}.tmp")
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
        say("    dice-skit.json: paths updated to the new layout")

    for folder in ("input", "in-out", "output"):
        (project / folder).mkdir(exist_ok=True)

    _write_migration_log(project, done, new_settings is not None)

    say()
    say(f"Moved {len(done)} item(s). The project now uses the new layout.")

    return done


def _remove_empty_tree(folder):
    """
    Delete `folder` and its subfolders if they hold no files at all.
    """
    if not folder.is_dir():
        return

    if any(p.is_file() for p in folder.rglob("*")):
        return

    for sub in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        sub.rmdir()

    folder.rmdir()


def _rewritten_settings(project):
    """
    (path, new text) for dice-skit.json with its paths section converted to
    the new layout, or None when no change is needed.

    Old values that were the old defaults become the new defaults (the key is
    simply dropped, so the built-in default applies). A value the user had
    changed is kept, so their customisation is not lost.
    """
    import settings

    path = Path(project) / "dice-skit.json"

    if not settings_are_old(project):
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LayoutError(f"dice-skit.json could not be read to update its paths: {exc}")

    old = data.get("paths", {})
    kept = {}

    for key, value in old.items():
        if key.startswith("_"):
            continue

        if key == "output":
            # The old "output" was the master folder; nothing to keep unless
            # it had been changed, in which case it becomes master_dir.
            if value != "dialogues-mp3":
                kept["master_dir"] = value
            continue

        if _OLD_PATH_DEFAULTS.get(key) == value:
            continue  # was the old default; the new default replaces it

        kept[key] = value

    values, _ = settings.load(Path("__no_such_file__"))
    values["paths"].update(kept)

    # Keep every other section exactly as the user had it.
    for section, body in data.items():
        if section in values and section != "paths" and isinstance(body, dict):
            for key, value in body.items():
                if not key.startswith("_") and key in values[section]:
                    values[section][key] = value

    # Drop the old paths comments so the new built-in help is written.
    data["paths"] = {}
    document = settings.document(values, existing=data)

    return path, json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _write_migration_log(project, done, settings_changed):
    log_dir = Path(project) / "output" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = [
        f"migrate log - {datetime.now().isoformat(timespec='seconds')}",
        "moved to the input / in-out / output layout (doc section 14)",
        "",
        *[f"  {old}  ->  {new}" for old, new in done],
        "",
        f"dice-skit.json paths updated: {'yes' if settings_changed else 'no'}",
    ]

    (log_dir / f"migrate{stamp}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# CLEAN
# ============================================================

def _inside(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def clean_targets(output, master_dir, work, log):
    """
    The folders clean() empties, after checking each is safely inside output
    and is not the log folder.
    """
    targets = []

    for folder in (master_dir, work):
        folder = Path(folder).resolve()

        if not _inside(folder, output) or folder == Path(output).resolve():
            raise LayoutError(
                f"refusing to clean {folder}: it is not inside the output "
                f"folder {output}"
            )

        if _inside(log, folder):
            raise LayoutError(
                f"refusing to clean {folder}: the log folder is inside it"
            )

        targets.append(folder)

    return targets


def summarise(folders):
    """
    (files, bytes) per folder, for the confirmation prompt.
    """
    result = []

    for folder in folders:
        files = [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []
        result.append((folder, len(files), sum(p.stat().st_size for p in files)))

    return result


def clean(folders):
    """
    Delete the contents of each folder, keeping the folder itself. Returns the
    number of files removed.
    """
    removed = 0

    for folder in folders:
        if not folder.exists():
            continue

        for child in folder.iterdir():
            if child.is_dir():
                removed += sum(1 for p in child.rglob("*") if p.is_file())
                shutil.rmtree(child)
            else:
                child.unlink()
                removed += 1

    return removed