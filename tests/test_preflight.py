import csv

import pytest

import audio_common
import stitch_mp3


def test_load_csv_requires_required_columns(tmp_path):
    manifest = tmp_path / "dialogues.csv"
    manifest.write_text("sequence_number,character_name\n1,SAMAY\n")

    with pytest.raises(RuntimeError, match="missing columns: dialogue_text"):
        audio_common.load_csv(manifest)


def test_load_csv_skips_rows_without_sequence_or_dialogue(tmp_path):
    manifest = tmp_path / "dialogues.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("sequence_number", "character_name", "dialogue_text"),
        )
        writer.writeheader()
        writer.writerow(
            {"sequence_number": "", "character_name": "SAMAY", "dialogue_text": "Hello"}
        )
        writer.writerow(
            {"sequence_number": "2", "character_name": "KRISHNA", "dialogue_text": ""}
        )
        writer.writerow(
            {
                "sequence_number": "3",
                "character_name": "ARJUNA",
                "dialogue_text": "Hello",
            }
        )

    records = audio_common.load_csv(manifest)

    assert [record["sequence_number"] for record in records] == ["3"]
    assert records[0]["row_number"] == 4


def test_preflight_reports_missing_and_corrupt_inputs(tmp_path, monkeypatch):
    corrupt = tmp_path / "2-KRISHNA.mp3"
    corrupt.write_bytes(b"not audio")
    monkeypatch.setattr(audio_common, "search_directories", lambda: ())
    monkeypatch.setattr(
        audio_common,
        "is_valid_audio",
        lambda path, verbose=False: verbose and path.exists(),
    )
    ignore_call = lambda *args, **kwargs: (args, kwargs)
    monkeypatch.setattr(audio_common, "warn", ignore_call)
    monkeypatch.setattr(stitch_mp3, "log_row_sources", ignore_call)

    resolved = [
        {"path": None, "label": "row 1 / 1-SAMAY.mp3"},
        {"path": corrupt, "label": "row 2 / 2-KRISHNA.mp3"},
    ]

    usable, missing, corrupt_entries = stitch_mp3.preflight(resolved)

    assert usable == []
    assert missing == [resolved[0]]
    assert corrupt_entries == [resolved[1]]


def test_mandatory_missing_manifest_is_fatal_even_when_not_strict(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(audio_common, "CSV_FILE", tmp_path / "missing.csv")
    monkeypatch.setattr(audio_common, "warn", lambda *args, **kwargs: (args, kwargs))

    fatal, waived = stitch_mp3.mandatory_checks([], strict=False)

    assert fatal == [("manifest missing", 1)]
    assert waived == []


@pytest.mark.parametrize(
    ("strict", "fatal_expected", "waived_expected"),
    [(True, True, False), (False, False, True)],
)
def test_character_mismatch_is_fatal_only_in_strict_mode(
    tmp_path, monkeypatch, strict, fatal_expected, waived_expected
):
    manifest = tmp_path / "dialogues.csv"
    manifest.write_text("present")
    monkeypatch.setattr(audio_common, "CSV_FILE", manifest)
    monkeypatch.setattr(audio_common, "warn", lambda *args, **kwargs: (args, kwargs))
    audio = tmp_path / "4-SHAKUNI.mp3"
    audio.write_bytes(b"fixture")
    entry = {
        "path": audio,
        "source": tmp_path / "human_gen",
        "record": {"character_name": "ARJUNA", "row_number": 2},
        "label": "row 2 / 4-SHAKUNI.mp3",
    }

    fatal, waived = stitch_mp3.mandatory_checks([entry], strict=strict)

    assert bool(fatal) is fatal_expected
    assert bool(waived) is waived_expected
