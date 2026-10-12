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
    path = tmp_path / "dice-skit.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(settings.SettingsError, match=message):
        settings.load(path)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "dice-skit.json"
    path.write_text('{"run":{"strict":true,"strict":false}}')

    with pytest.raises(settings.SettingsError, match="appears twice"):
        settings.load(path)


def test_configure_resolves_relative_and_absolute_paths(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "input").mkdir()
    (project / "output").mkdir()
    stage = tmp_path / "stage"
    (project / "dice-skit.json").write_text(
        json.dumps(
            {
                "paths": {
                    "input": "source",
                    "in_out": str(stage),
                    "output": "build",
                }
            }
        )
    )

    for name in audio_common._CONFIGURED:
        monkeypatch.setattr(audio_common, name, getattr(audio_common, name))

    audio_common.configure(project)

    assert audio_common.INPUT_DIRECTORY == project / "source"
    assert audio_common.IN_OUT_DIRECTORY == stage
    assert audio_common.OUTPUT_DIRECTORY == project / "build"


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
