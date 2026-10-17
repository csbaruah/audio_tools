from io import StringIO

from scripts import run_tests


class FakeProcess:
    def __init__(self, output, return_code):
        self.stdout = StringIO(output)
        self.return_code = return_code

    def wait(self):
        return self.return_code


def test_run_checks_streams_and_saves_each_test_result(tmp_path, monkeypatch, capsys):
    outputs = iter(
        [
            "tests/test_example.py::test_first PASSED\n"
            "tests/test_example.py::test_second FAILED\n",
            "would reformat: test_example.py\n",
        ]
    )
    return_codes = iter([1, 0])
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess(next(outputs), next(return_codes))

    monkeypatch.setattr(run_tests.subprocess, "Popen", fake_popen)

    result, log_path = run_tests.run_checks(
        "python", project_root=tmp_path, log_directory=tmp_path / "logs"
    )
    terminal = capsys.readouterr().out
    logged = log_path.read_text(encoding="utf-8")

    assert result == 1
    assert len(calls) == 1
    assert calls[0][0][2:4] == ("pytest", "-v")
    assert "test_first PASSED" in terminal
    assert "test_second FAILED" in terminal
    assert "test_first PASSED" in logged
    assert "test_second FAILED" in logged
    assert "Test log saved:" in terminal


def test_run_checks_records_format_check_on_success(tmp_path, monkeypatch, capsys):
    outputs = iter(
        [
            "tests/test_example.py::test_first PASSED\n",
            "All done! 1 file left unchanged.\n",
        ]
    )
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        return FakeProcess(next(outputs), 0)

    monkeypatch.setattr(run_tests.subprocess, "Popen", fake_popen)

    result, log_path = run_tests.run_checks(
        "python", project_root=tmp_path, log_directory=tmp_path / "logs"
    )

    assert result == 0
    assert len(calls) == 2
    assert calls[1][2:5] == ("black", "--check", "tests")
    assert "test_first PASSED" in capsys.readouterr().out
    assert "All done!" in log_path.read_text(encoding="utf-8")


def test_run_checks_reports_failure_to_start(tmp_path, monkeypatch, capsys):
    def fail_to_start(*args, **kwargs):
        raise OSError("interpreter missing")

    monkeypatch.setattr(run_tests.subprocess, "Popen", fail_to_start)

    result, log_path = run_tests.run_checks(
        "missing-python", project_root=tmp_path, log_directory=tmp_path / "logs"
    )

    assert result == 1
    assert "Could not start check: interpreter missing" in capsys.readouterr().out
    assert "Could not start check: interpreter missing" in log_path.read_text(
        encoding="utf-8"
    )
