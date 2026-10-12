"""
Pipeline settings, loaded from dice-skit.json.

Section 12 of docs/audio-concat-study.md specifies this. Every setting has a
built-in default in SPEC below. dice-skit.json, beside the scripts, overrides
any of them. A missing file, or a missing key, means "use the default", so the
pipeline runs with no JSON at all.

    built-in default  ->  dice-skit.json  ->  command line (strict=, dry_run=)

Keys starting with "_" are comments and are ignored. Any other unknown key, or
a value of the wrong type or out of range, raises SettingsError before any
audio is touched, so a typo can never be silently ignored.

SPEC is also the contract a future GUI reads: each entry carries its type,
limits or choices, and the help text shown in dice-skit.json.

Never store the Sarvam API key here. tts.api_key_source says where it lives.
"""

import json
import os
from pathlib import Path

BASE_DIRECTORY = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIRECTORY / "dice-skit.json"

FORMAT_VERSION = 1


class SettingsError(ValueError):
    """
    Raised for an unreadable settings file or an invalid value in it.
    """


class Setting:
    """
    One setting: its default, how it is checked, and its help text.

    kind is "bool", "int", "float", "str", "path", "path_list" or "url".
    `nullable` allows JSON null. `minimum` / `maximum` bound numbers, and
    `choices` restricts a value to a fixed set.
    """

    def __init__(
        self,
        default,
        kind,
        help_text,
        minimum=None,
        maximum=None,
        choices=None,
        nullable=False,
    ):
        self.default = default
        self.kind = kind
        self.help = help_text
        self.minimum = minimum
        self.maximum = maximum
        self.choices = choices
        self.nullable = nullable

    def check(self, where, value):
        """
        Return `value` if it is valid for this setting, else raise.
        """
        if value is None:
            if self.nullable:
                return None

            raise SettingsError(f"{where} may not be null")

        if self.kind == "bool":
            if not isinstance(value, bool):
                raise SettingsError(f"{where} must be true or false, got {value!r}")

        elif self.kind in ("int", "float"):
            # bool is a subclass of int in Python; reject it explicitly.
            numeric = (int, float) if self.kind == "float" else (int,)

            if isinstance(value, bool) or not isinstance(value, numeric):
                raise SettingsError(
                    f"{where} must be {'a number' if self.kind == 'float' else 'a whole number'}, "
                    f"got {value!r}"
                )

            if self.minimum is not None and value < self.minimum:
                raise SettingsError(f"{where} must be at least {self.minimum}, got {value}")

            if self.maximum is not None and value > self.maximum:
                raise SettingsError(f"{where} must be at most {self.maximum}, got {value}")

            if self.kind == "float":
                value = float(value)

        elif self.kind in ("str", "path", "url"):
            if not isinstance(value, str) or not value.strip():
                raise SettingsError(f"{where} must be a non-empty string, got {value!r}")

        elif self.kind == "path_list":
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                raise SettingsError(
                    f"{where} must be a non-empty list of folder names, got {value!r}"
                )

        if self.choices is not None:
            items = value if isinstance(value, list) else [value]

            for item in items:
                if item not in self.choices:
                    raise SettingsError(
                        f"{where} must be one of {', '.join(map(str, self.choices))}; "
                        f"got {item!r}"
                    )

            if isinstance(value, list) and len(set(value)) != len(value):
                raise SettingsError(f"{where} lists the same entry twice: {value!r}")

        return value


# ============================================================
# THE SETTINGS
# ============================================================
#
# Order here is the order dice-skit.json is written in. Help text is what the
# file's "_<key>" comments say; keep the two in step.

SPEC = {
    "run": {
        "_comment": "How a build behaves.",
        "strict": Setting(
            True, "bool",
            [
                "What to do when a line's audio is missing, corrupt or unconvertible.",
                "true: abort and leave the old master untouched. Use for the final build.",
                "false: skip those lines and build the rest. Use while recordings are arriving.",
                "The four mandatory checks abort either way and cannot be switched off.",
            ],
        ),
        "backend": Setting(
            "auto", "str",
            [
                "How audio files are joined.",
                "auto: FFmpeg if installed, else the built-in joiner (recommended).",
                "ffmpeg: always FFmpeg; fails if it is not installed.",
                "python: built-in joiner; no FFmpeg needed, MP3 only.",
            ],
            choices=("auto", "ffmpeg", "python"),
        ),
        "batch_size": Setting(
            None, "int",
            "null = one pass (recommended). A number, e.g. 40, only if one pass runs out of memory.",
            minimum=1, nullable=True,
        ),
    },
    "paths": {
        "_comment": [
            "Where files are read from and written to (doc section 14).",
            "input, in_out and output are the three top-level folders; each may be relative to the project folder or absolute, e.g. D:/dice-skit-output.",
            "The other folders are relative to their parent: csv and human_gen to input, machine_gen to in_out, master_dir, work and log to output.",
            "output must not be inside input or in_out, nor contain them: 'stitch_mp3.py clean' deletes inside output.",
        ],
        "input": Setting("input", "path", "Top-level input folder. Never written by the pipeline except by fetch_drive.py, which downloads into human_gen."),
        "in_out": Setting("in-out", "path", "Top-level folder for material one stage writes and another reads: machine_gen."),
        "output": Setting("output", "path", "Top-level output folder: master, work and log. Everything in it can be rebuilt."),
        "csv": Setting("dialogues_new.csv", "path", "The script, inside input: lines, order, fillers, background scores."),
        "human_gen": Setting("human_gen", "path", "Recorded takes and named sound files, inside input."),
        "machine_gen": Setting("machine_gen", "path", "TTS-generated takes, inside in_out. generate_mp3.py writes here; stitch_mp3.py reads it."),
        "master_dir": Setting("master", "path", "Folder for the finished master, inside output."),
        "work": Setting("work", "path", "Caches and generated silence, inside output. Safe to delete; rebuilt on the next run."),
        "log": Setting("log", "path", "One log file per run, inside output. 'clean' never deletes it."),
        "master": Setting("master_dialogues.mp3", "str", "Master filename, inside master_dir."),
        "search_order": Setting(
            ["human_gen", "machine_gen"], "path_list",
            "When both folders hold a file for the same take, the first listed wins.",
            choices=("human_gen", "machine_gen"),
        ),
    },
    "output": {
        "_comment": "Format of the master and of every piece in it.",
        "format": Setting("mp3", "str", "Only mp3 is supported today.", choices=("mp3",)),
        "sample_rate": Setting(
            24000, "int",
            [
                "Hz. 24000 matches the TTS output, so those files need no conversion (recommended for speech).",
                "44100 or 48000 suit music-heavy productions, but every TTS line must then be converted.",
                "Also allowed: 16000, 22050, 32000.",
            ],
            choices=(16000, 22050, 24000, 32000, 44100, 48000),
        ),
        "bitrate": Setting(
            128000, "int",
            [
                "Bits per second. Higher means better quality and a bigger file.",
                "At 16000-24000 Hz: 8000-160000; 128000 is clear speech.",
                "At 32000-48000 Hz: 32000-320000; 192000 or more suits music.",
            ],
            minimum=8000, maximum=320000,
        ),
        "mono": Setting(
            True, "bool",
            "true: right for dialogue. false: stereo, only if stereo music width matters; about twice the size.",
        ),
    },
    "fillers": {
        "_comment": "Generated silence between lines, always in the output format above.",
        "short_seconds": Setting(
            2.0, "float",
            [
                "Gap before each line whose filler_before cell is empty.",
                "1-1.5 brisk, 2 natural (default), 3+ slow and dramatic.",
                "Applies to almost every line: +1 s on 154 lines adds about 2.5 minutes.",
            ],
            minimum=0.1, maximum=60,
        ),
        "long_seconds": Setting(
            10.0, "float",
            "Length of long_filler.mp3, for rows that name it, e.g. a scene change.",
            minimum=0.1, maximum=600,
        ),
    },
    "background_score": {
        "_comment": "Music under a line (doc section 10). Only affects rows with background_score and score_cues filled in.",
        "duck_db": Setting(
            -14.0, "float",
            "How far the faded track is turned down, below 0. -6 subtle, -14 faint but audible (default), -20 almost silent.",
            minimum=-40, maximum=-1,
        ),
        "ramp_seconds": Setting(
            0.3, "float",
            "Crossfade length. 0.1 quick, 0.3 smooth (default), 1.0 slow. Keep it shorter than your shortest cue segment.",
            minimum=0.01, maximum=5,
        ),
        "match_loudness": Setting(
            True, "bool",
            "true: bring the music to the line's loudness before mixing (recommended). false: only if the music is pre-mastered to sit under dialogue.",
        ),
        "limit": Setting(
            0.95, "float",
            "Peak ceiling for the mix, 0.5-1.0 of full scale. Lower only if mixes sound distorted.",
            minimum=0.5, maximum=1.0,
        ),
    },
    "loudness": {
        "_comment": "Even out volume across lines (doc section 11). Dialogue only; fillers, sound effects and music keep their levels.",
        "enabled": Setting(
            True, "bool",
            "false leaves each line at its recorded level; useful for comparing. Same as loudness=0 on the command line.",
        ),
        "target_lufs": Setting(
            -19.0, "float",
            [
                "Closer to 0 is louder.",
                "-16 loud: phone speakers, noisy places; more lines get limited.",
                "-19 balanced for speech (default). -23 broadcast standard; quieter, very little limiting.",
            ],
            minimum=-31, maximum=-10,
        ),
        "true_peak_dbfs": Setting(
            -1.0, "float",
            "Highest allowed peak. -1 default; -2 if the master will be re-encoded later, e.g. by an upload platform.",
            minimum=-9, maximum=0,
        ),
    },
    "tts": {
        "_comment": "Generating lines with Sarvam (generate_mp3.py). The API key is never stored in this file.",
        "enabled": Setting(False, "bool", "true generates missing lines and uses API credit."),
        "api_key_source": Setting(
            "env", "str",
            "env: SARVAM_API_KEY from .env (today). keyring: Windows Credential Manager (recommended once a GUI exists).",
            choices=("env", "keyring"),
        ),
        "language": Setting(
            "en-IN", "str",
            "Language and accent, e.g. en-IN (Indian English), hi-IN (Hindi). See Sarvam's documentation.",
        ),
        "model": Setting("bulbul:v3", "str", "Sarvam voice model."),
        "pace": Setting(
            1.0, "float",
            "Speaking speed. 1.0 normal; lower is slower. See Sarvam's documentation for the range.",
            minimum=0.3, maximum=3.0,
        ),
        "sample_rate": Setting(
            24000, "int",
            "Rate requested from TTS. Keep it equal to output.sample_rate so generated lines need no conversion.",
            choices=(8000, 16000, 22050, 24000, 44100, 48000),
        ),
        "overwrite_existing": Setting(
            False, "bool",
            "true regenerates every line, e.g. after changing voice or pace. Uses API credit.",
        ),
    },
    "drive": {
        "_comment": "Downloading recordings (fetch_drive.py).",
        "folder_url": Setting(
            None, "url",
            "Shared Google Drive folder link. null means pass it on the command line.",
            nullable=True,
        ),
        "use_cookies": Setting(
            False, "bool",
            "true only if the folder is not link-shared and needs your browser login.",
        ),
    },
}

FILE_COMMENT = [
    "Settings for the dice-skit audio pipeline.",
    "Keys starting with _ are comments and are ignored.",
    "Delete any setting to use its built-in default.",
    "Command-line flags (strict=0, dry_run=1, loudness=0) override this file for one run.",
    "Paths are relative to this file's folder.",
]


# ============================================================
# LOADING
# ============================================================

def _unique_keys(pairs):
    """
    json object hook that rejects a repeated key, which json.loads would
    otherwise resolve silently by keeping the last one.
    """
    seen = {}

    for key, value in pairs:
        if key in seen:
            raise SettingsError(f'key "{key}" appears twice in the same section')

        seen[key] = value

    return seen


def load(path=None):
    """
    Read and check the settings file.

    Returns (values, sources). `values[section][key]` is the value to use and
    `sources[section][key]` is "default" or "dice-skit.json".

    A missing file is not an error: every value is then the default.
    """
    path = Path(path) if path else SETTINGS_FILE

    values = {
        section: {k: s.default for k, s in fields.items() if not k.startswith("_")}
        for section, fields in SPEC.items()
    }
    sources = {
        section: {k: "default" for k in fields}
        for section, fields in values.items()
    }

    if not path.exists():
        return values, sources

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)

    except SettingsError as exc:
        raise SettingsError(f"{path.name}: {exc}")

    except (OSError, ValueError) as exc:
        raise SettingsError(f"{path.name} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        raise SettingsError(f"{path.name} must hold a JSON object at the top level")

    version = data.get("version", FORMAT_VERSION)

    if version != FORMAT_VERSION:
        raise SettingsError(
            f"{path.name} is format version {version!r}; this code reads version "
            f"{FORMAT_VERSION}"
        )

    for section, body in data.items():
        if section.startswith("_") or section == "version":
            continue

        if section not in SPEC:
            raise SettingsError(
                f'{path.name}: unknown section "{section}". Known sections: '
                + ", ".join(SPEC)
            )

        if not isinstance(body, dict):
            raise SettingsError(f'{path.name}: "{section}" must be an object')

        for key, value in body.items():
            if key.startswith("_"):
                continue

            spec = SPEC[section].get(key)

            if not isinstance(spec, Setting):
                known = [k for k in SPEC[section] if not k.startswith("_")]

                raise SettingsError(
                    f'{path.name}: unknown setting "{section}.{key}". Known in '
                    f'"{section}": ' + ", ".join(known)
                )

            try:
                values[section][key] = spec.check(f"{section}.{key}", value)
            except SettingsError as exc:
                raise SettingsError(f"{path.name}: {exc}")
            sources[section][key] = path.name

    _cross_check(values, path.name)

    return values, sources


def _cross_check(values, where):
    """
    Rules that involve more than one setting.
    """
    output = values["output"]

    # MPEG Layer III allows a narrower bitrate range at the MPEG-2 rates.
    if output["sample_rate"] <= 24000 and output["bitrate"] > 160000:
        raise SettingsError(
            f"{where}: output.bitrate {output['bitrate']} is too high for "
            f"{output['sample_rate']} Hz; the maximum at 16000-24000 Hz is 160000"
        )

    if output["sample_rate"] >= 32000 and output["bitrate"] < 32000:
        raise SettingsError(
            f"{where}: output.bitrate {output['bitrate']} is too low for "
            f"{output['sample_rate']} Hz; the minimum at 32000-48000 Hz is 32000"
        )


# ============================================================
# WRITING
# ============================================================

def document(values=None, existing=None):
    """
    The settings as a commented JSON object: every value plus its "_" help
    text, in SPEC order. This is what write() saves and what a GUI would
    show beside each field.

    `existing` is the file's current content, if any. Its "_" comments win
    over the built-in help text, so a comment a user wrote is never lost.
    """
    values = values or load()[0]
    existing = existing if isinstance(existing, dict) else {}

    def comment(body, key, default):
        return body.get(key, default) if isinstance(body, dict) else default

    data = {
        "_comment": comment(existing, "_comment", FILE_COMMENT),
        "version": FORMAT_VERSION,
        "_version": comment(existing, "_version", "File format version. Do not edit."),
    }

    for section, fields in SPEC.items():
        old = existing.get(section)
        body = {}

        for key, spec in fields.items():
            if key == "_comment":
                body["_comment"] = comment(old, "_comment", spec)
                continue

            body[key] = values[section][key]
            body[f"_{key}"] = comment(old, f"_{key}", spec.help)

        data[section] = body

    return data


def write(path=None, values=None):
    """
    Save a fully commented settings file, keeping any "_" comments the
    current file already has.

    Written to a temporary file first and then renamed into place, so a crash
    mid-save can never leave a half-written file.
    """
    path = Path(path) if path else SETTINGS_FILE

    existing = None

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None

    text = json.dumps(document(values, existing), indent=2, ensure_ascii=False) + "\n"

    temp = path.with_name(f"_{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)

    return path


def describe(values, sources):
    """
    Lines for the run log: every setting, its value and where it came from.
    """
    lines = []

    for section, fields in values.items():
        for key, value in fields.items():
            lines.append(
                f"  {section + '.' + key:<34} {json.dumps(value):<28} {sources[section][key]}"
            )

    return lines


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "write":
        values, _ = load()
        print(f"Wrote {write(values=values)}")
    else:
        values, sources = load()

        print(f"Settings file: {SETTINGS_FILE} "
              f"({'found' if SETTINGS_FILE.exists() else 'not found; all defaults'})")

        for line in describe(values, sources):
            print(line)

        print()
        print("python settings.py write   creates or refreshes a fully commented "
              "dice-skit.json, keeping your current values.")