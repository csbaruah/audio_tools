import os
from pathlib import Path

import audio_tools
import modaud


def test_project_path_is_required(capsys):
    assert modaud.run([]) == 2
    assert "Usage:" in capsys.readouterr().out


def test_missing_project_is_reported(tmp_path, capsys):
    missing = tmp_path / "does not exist"

    assert modaud.run([str(missing)]) == 2
    assert "project folder does not exist" in capsys.readouterr().out


def test_project_argument_configures_pipeline_and_forwards_options(
    tmp_path, monkeypatch
):
    project = tmp_path / "a project"
    project.mkdir()
    configured = []
    calls = []

    monkeypatch.setattr(
        modaud.common, "configure", lambda path: configured.append(path)
    )
    monkeypatch.setattr(modaud, "bundled_ffmpeg_directory", lambda: None)
    monkeypatch.setattr(
        modaud.stitch_mp3, "main", lambda: calls.append(list(modaud.sys.argv)) or 7
    )
    monkeypatch.setattr(modaud.common, "close_run_log", lambda: None)
    original_argv = modaud.sys.argv

    assert modaud.run([str(project), "strict=0", "dry_run=1"]) == 7

    assert configured == [project.resolve()]
    assert calls == [["stitch_mp3.py", "strict=0", "dry_run=1"]]
    assert modaud.sys.argv == original_argv


def test_stitch_system_exit_becomes_process_exit_code(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(modaud.common, "configure", lambda path: None)
    monkeypatch.setattr(modaud, "bundled_ffmpeg_directory", lambda: None)
    monkeypatch.setattr(modaud.common, "close_run_log", lambda: None)

    def exit_with_error():
        raise SystemExit(1)

    monkeypatch.setattr(modaud.stitch_mp3, "main", exit_with_error)

    assert modaud.run([str(project)]) == 1


def test_bundled_ffmpeg_is_selected_for_frozen_executable(tmp_path):
    ffmpeg_dir = tmp_path / "ffmpeg"
    ffmpeg_dir.mkdir()
    executable = tmp_path / "modaud.exe"

    assert (
        modaud.bundled_ffmpeg_directory(frozen=True, executable=executable)
        == ffmpeg_dir
    )


def test_bundled_ffmpeg_path_is_set_before_stitching(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    bundled = tmp_path / "release" / "ffmpeg"
    bundled.mkdir(parents=True)
    observed = []

    monkeypatch.setattr(modaud, "bundled_ffmpeg_directory", lambda: bundled)
    monkeypatch.setattr(modaud.common, "configure", lambda path: None)
    monkeypatch.setattr(modaud.common, "close_run_log", lambda: None)
    monkeypatch.setattr(
        modaud.stitch_mp3,
        "main",
        lambda: observed.append(os.environ.get("DICE_SKIT_FFMPEG_DIR")) or 0,
    )
    monkeypatch.setattr(audio_tools, "reset", lambda: None)

    assert modaud.run([str(project)]) == 0
    assert observed == [str(bundled)]
