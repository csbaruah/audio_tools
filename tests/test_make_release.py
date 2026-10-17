import csv
import json

import pytest

import make_release


def test_copy_ffmpeg_tools_copies_programs_dlls_and_notices(tmp_path, monkeypatch):
    source = tmp_path / "ffmpeg-bin"
    source.mkdir()
    ffmpeg = source / "ffmpeg.exe"
    ffprobe = source / "ffprobe.exe"
    dll = source / "avcodec-test.dll"
    notice = source / "LICENSE.txt"
    for file in (ffmpeg, ffprobe, dll, notice):
        file.write_bytes(file.name.encode())

    monkeypatch.setattr(
        make_release, "find_tool", lambda name, path=None: source / f"{name}.exe"
    )
    monkeypatch.setattr(make_release, "verify_tool", lambda path: None)

    destination = make_release.copy_ffmpeg_tools(tmp_path / "release")

    assert (destination / "ffmpeg.exe").read_bytes() == ffmpeg.read_bytes()
    assert (destination / "ffprobe.exe").read_bytes() == ffprobe.read_bytes()
    assert (destination / dll.name).read_bytes() == dll.read_bytes()
    assert (destination / notice.name).read_bytes() == notice.read_bytes()


def test_find_tool_requires_executable_on_path(monkeypatch):
    monkeypatch.setattr(make_release.shutil, "which", lambda name, path=None: None)

    with pytest.raises(make_release.ReleaseError, match="ffmpeg.exe was not found"):
        make_release.find_tool("ffmpeg")


def test_release_readme_includes_requested_main_readme_sections(tmp_path):
    (tmp_path / "README.txt").write_text(
        "Project intro\n"
        "4. COMMAND-LINE OPTIONS\ncommand options\n"
        "5. SETTINGS FILE (config.json)\nsettings details\n"
        "6. THE SCRIPT FILE (input\\<csv_file>)\nCSV details\n"
        "7. OTHER SECTION\nnot requested\n"
        "9. TROUBLESHOOTING\ntroubleshooting details\n",
        encoding="utf-8",
    )

    readme = make_release.release_readme(tmp_path)

    for text in (
        "command options",
        "settings details",
        "CSV details",
        "troubleshooting details",
    ):
        assert text in readme
    assert "not requested" not in readme


def test_release_readme_fails_if_main_section_is_missing(tmp_path):
    (tmp_path / "README.txt").write_text("4. COMMAND-LINE OPTIONS\n")

    with pytest.raises(make_release.ReleaseError, match="missing section"):
        make_release.release_readme(tmp_path)


def test_sample_project_has_three_rows_and_all_referenced_assets(tmp_path):
    project_root = tmp_path / "source"
    source_media = project_root / "input" / "human_gen"
    source_media.mkdir(parents=True)
    stale_csv = tmp_path / "release" / "sample_project" / "input" / "dialogues_new.csv"
    stale_csv.parent.mkdir(parents=True)
    stale_csv.write_text("stale")
    for filename in make_release.SAMPLE_AUDIO + make_release.SAMPLE_ASSETS:
        (source_media / filename).write_bytes(filename.encode())
    (project_root / "config.json").write_text(
        json.dumps(
            {
                "csv_file": "dialogues_new.csv",
                "run": {"strict": False},
                "paths": {"input": "input"},
            }
        )
    )

    sample = make_release.write_sample_project(project_root, tmp_path / "release")
    csv_path = sample / "input" / "dialogues.csv"
    config = json.loads((sample / "config.json").read_text(encoding="utf-8"))

    assert config["csv_file"] == "dialogues.csv"
    assert config["run"]["strict"] is False
    assert config["paths"]["input"] == "input"
    assert "csv" not in config["paths"]
    assert not (sample / "dice-skit.json").exists()
    assert not stale_csv.exists()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 3
    assert len({row["score_cues"] for row in rows}) == 3
    assert all(row["filler_before"] and row["filler_after"] for row in rows)
    assert all(row["background_score"] and row["score_cues"] for row in rows)
    assert all(
        (sample / "input" / "human_gen" / filename).is_file()
        for filename in make_release.SAMPLE_AUDIO + make_release.SAMPLE_ASSETS
    )


def test_sample_project_fails_for_missing_media(tmp_path):
    project_root = tmp_path / "source"
    (project_root / "input" / "human_gen").mkdir(parents=True)
    (project_root / "config.json").write_text('{"csv_file": "dialogues_new.csv"}')

    with pytest.raises(make_release.ReleaseError, match="sample asset is missing"):
        make_release.write_sample_project(project_root, tmp_path / "release")


def test_verify_tool_reports_nonzero_exit(tmp_path, monkeypatch):
    executable = tmp_path / "ffmpeg.exe"
    executable.touch()

    class Result:
        returncode = 1
        stdout = ""
        stderr = "bad binary"

    monkeypatch.setattr(
        make_release.subprocess, "run", lambda *args, **kwargs: Result()
    )

    with pytest.raises(
        make_release.ReleaseError, match="did not pass its version check"
    ):
        make_release.verify_tool(executable)


def test_clean_release_removes_only_release_artifacts(tmp_path, monkeypatch):
    output = tmp_path / "output"
    release = output / "release"
    preserved = output / "master" / "master.mp3"
    (release / "sample_project").mkdir(parents=True)
    preserved.parent.mkdir()
    preserved.write_bytes(b"keep")
    monkeypatch.setattr(make_release, "PROJECT_ROOT", tmp_path)

    assert make_release.clean_release(release) == release

    assert not release.exists()
    assert preserved.read_bytes() == b"keep"


def test_clean_release_refuses_path_outside_release_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(make_release, "PROJECT_ROOT", tmp_path)
    outside = tmp_path / "important"
    outside.mkdir()

    with pytest.raises(make_release.ReleaseError, match="refusing to clean outside"):
        make_release.clean_release(outside)


def test_clean_release_refuses_release_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(make_release, "PROJECT_ROOT", tmp_path)
    release = tmp_path / "output" / "release"
    release.parent.mkdir()
    try:
        release.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not permitted")

    with pytest.raises(make_release.ReleaseError, match="release-folder link"):
        make_release.clean_release(release)

    assert tmp_path.is_dir()


def test_release_builder_cli_dispatches_clean(tmp_path, monkeypatch, capsys):
    cleaned = tmp_path / "output" / "release"
    monkeypatch.setattr(make_release, "clean_release", lambda: cleaned)

    assert make_release.main(["clean"]) == 0
    assert str(cleaned) in capsys.readouterr().out

    assert make_release.main(["unexpected"]) == 2
