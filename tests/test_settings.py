import json

import pytest

import audio_common
import settings


def test_missing_settings_file_uses_documented_defaults(tmp_path):
    values, sources = settings.load(tmp_path / "missing.json")

    assert values["paths"]["input"] == "input"
    assert values["paths"]["in_out"] == "in-out"
    assert values["paths"]["output"] == "output"
    assert sources["paths"]["output"] == "default"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"run": {"strict": "false"}}, "run.strict must be true or false"),
        ({"output": {"sample_rate": 12345}}, "output.sample_rate must be one of"),
        ({"paths": {"output": ""}}, "paths.output must be a non-empty string"),
        ({"paths": {"search_order": ["human_gen", "human_gen"]}}, "same entry twice"),
        ({"unknown": {}}, 'unknown section "unknown"'),
    ],
)
def test_invalid_settings_are_rejected(tmp_path, payload, message):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(settings.SettingsError, match=message):
        settings.load(path)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"run":{"strict":true,"strict":false}}')

    with pytest.raises(settings.SettingsError, match="appears twice"):
        settings.load(path)


def test_load_project_config_reads_csv_filename(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"csv_file": "dialogues.csv"}))

    assert settings.load_project_config(path) == "dialogues.csv"


def test_load_project_config_requires_file(tmp_path):
    with pytest.raises(settings.SettingsError, match="config.json is required"):
        settings.load_project_config(tmp_path / "config.json")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, '"csv_file" must be a non-empty filename'),
        ({"csv_file": "../dialogues.csv"}, "without a folder path"),
        ({"csv_file": "dialogues.txt"}, "must end in .csv"),
    ],
)
def test_invalid_project_config_is_rejected(tmp_path, payload, message):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(settings.SettingsError, match=message):
        settings.load_project_config(path)


def test_configure_resolves_relative_and_absolute_paths(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "input").mkdir()
    (project / "output").mkdir()
    stage = tmp_path / "stage"
    (project / "config.json").write_text(
        json.dumps(
            {
                "csv_file": "dialogues.csv",
                "run": {"strict": False},
                "paths": {
                    "input": "source",
                    "in_out": str(stage),
                    "output": "build",
                },
            }
        )
    )

    for name in audio_common._CONFIGURED:
        monkeypatch.setattr(audio_common, name, getattr(audio_common, name))

    audio_common.configure(project)

    assert audio_common.INPUT_DIRECTORY == project / "source"
    assert audio_common.IN_OUT_DIRECTORY == stage
    assert audio_common.OUTPUT_DIRECTORY == project / "build"
    assert audio_common.CSV_FILE == project / "source" / "dialogues.csv"
    assert audio_common.SETTINGS_SOURCES["paths"]["csv"] == "config.json"
    assert audio_common.SETTINGS["run"]["strict"] is False
    assert audio_common.SETTINGS["paths"]["csv"] == "dialogues.csv"


def test_configure_rejects_deprecated_dice_skit_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "dice-skit.json").write_text("{}")
    (project / "dialogues_new.csv").write_text("legacy data")
    (project / "human_gen").mkdir()

    with pytest.raises(settings.SettingsError, match="is deprecated"):
        audio_common.configure(project)

    assert (project / "dialogues_new.csv").is_file()
    assert not (project / "input").exists()


def test_write_preserves_csv_selection_and_settings(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "csv_file": "dialogues.csv",
                "run": {"strict": False},
            }
        )
    )
    values, _ = settings.load(path)

    settings.write(path, values)

    saved = json.loads(path.read_text())
    assert saved["csv_file"] == "dialogues.csv"
    assert saved["run"]["strict"] is False
    assert "csv" not in saved["paths"]


@pytest.mark.parametrize(
    ("input_dir", "in_out_dir", "output_dir"),
    [
        ("input", "in-out", "input"),
        ("input", "in-out", "input/generated"),
        ("input", "in-out", "."),
        ("input", "in-out", "in-out/output"),
    ],
)
def test_output_path_must_not_overlap_source_paths(
    tmp_path, input_dir, in_out_dir, output_dir
):
    with pytest.raises(settings.SettingsError, match="must not be the same as"):
        audio_common._check_layout(
            tmp_path / input_dir,
            tmp_path / in_out_dir,
            tmp_path / output_dir,
        )


def test_separate_output_path_is_allowed(tmp_path):
    audio_common._check_layout(
        tmp_path / "input",
        tmp_path / "in-out",
        tmp_path / "output",
    )
