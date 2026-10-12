import json
import os
from pathlib import Path

import pytest

import layout


def _old_project(project):
    (project / "dialogues_new.csv").write_text("sequence_number\n1\n")
    (project / "human_gen").mkdir()
    (project / "human_gen" / "1-SAMAY.mp3").write_bytes(b"recording")
    (project / "machine_gen").mkdir()
    (project / "machine_gen" / "2-KRISHNA.mp3").write_bytes(b"tts")
    (project / "dialogues-mp3").mkdir()
    (project / "dialogues-mp3" / "old.mp3").write_bytes(b"master")
    (project / "work").mkdir()
    (project / "work" / "cache.tmp").write_bytes(b"cache")
    (project / "log").mkdir()
    (project / "log" / "run.log").write_text("log")
    (project / "dice-skit.json").write_text(
        json.dumps(
            {
                "paths": {
                    "csv": "dialogues_new.csv",
                    "human_gen": "human_gen",
                    "machine_gen": "machine_gen",
                    "work": "work",
                    "log": "log",
                    "output": "dialogues-mp3",
                },
                "run": {"strict": False},
            }
        )
    )


def test_migrate_moves_old_layout_and_preserves_custom_settings(tmp_path):
    _old_project(tmp_path)

    moves = layout.migrate(tmp_path, say=lambda *_: None)

    assert len(moves) == 6
    assert (tmp_path / "input" / "dialogues_new.csv").is_file()
    assert (
        tmp_path / "input" / "human_gen" / "1-SAMAY.mp3"
    ).read_bytes() == b"recording"
    assert (
        tmp_path / "in-out" / "machine_gen" / "2-KRISHNA.mp3"
    ).read_bytes() == b"tts"
    assert (tmp_path / "output" / "master" / "old.mp3").read_bytes() == b"master"
    assert (tmp_path / "output" / "work" / "cache.tmp").read_bytes() == b"cache"
    assert (tmp_path / "output" / "log" / "run.log").read_text() == "log"
    assert not (tmp_path / "human_gen").exists()

    rewritten = json.loads((tmp_path / "dice-skit.json").read_text())
    assert rewritten["run"]["strict"] is False
    assert rewritten["paths"]["input"] == "input"
    assert rewritten["paths"]["in_out"] == "in-out"
    assert rewritten["paths"]["output"] == "output"

    assert layout.migrate(tmp_path, say=lambda *_: None) == []


def test_migrate_refuses_destination_collision_without_moving_anything(tmp_path):
    _old_project(tmp_path)
    destination = tmp_path / "in-out" / "machine_gen"
    destination.mkdir(parents=True)
    (destination / "keep.mp3").write_bytes(b"keep")

    with pytest.raises(layout.LayoutError, match="Nothing was moved"):
        layout.migrate(tmp_path, say=lambda *_: None)

    assert (tmp_path / "dialogues_new.csv").is_file()
    assert (tmp_path / "human_gen" / "1-SAMAY.mp3").is_file()
    assert (tmp_path / "machine_gen" / "2-KRISHNA.mp3").is_file()
    assert (destination / "keep.mp3").read_bytes() == b"keep"
    assert not (tmp_path / "input").exists()


def test_migrate_rolls_back_prior_moves_after_os_error(tmp_path, monkeypatch):
    _old_project(tmp_path)
    real_replace = os.replace
    failed = False

    def fail_moving_human_gen(source, target):
        nonlocal failed
        if Path(source) == tmp_path / "human_gen" and not failed:
            failed = True
            raise OSError("simulated file-in-use error")
        return real_replace(source, target)

    monkeypatch.setattr(layout.os, "replace", fail_moving_human_gen)

    with pytest.raises(
        layout.LayoutError, match="Everything already moved was put back"
    ):
        layout.migrate(tmp_path, say=lambda *_: None)

    assert failed
    assert (tmp_path / "dialogues_new.csv").is_file()
    assert (tmp_path / "human_gen" / "1-SAMAY.mp3").is_file()
    assert (tmp_path / "machine_gen" / "2-KRISHNA.mp3").is_file()
    assert not (tmp_path / "input").exists()
