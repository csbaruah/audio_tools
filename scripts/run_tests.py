"""Run the project's test and formatting checks, teeing output to output/log."""

import argparse
from datetime import datetime
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIRECTORY = PROJECT_ROOT / "output" / "log"


def _log_path(log_directory):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return Path(log_directory) / f"tests-{timestamp}.log"


def run_checks(python, project_root=PROJECT_ROOT, log_directory=LOG_DIRECTORY):
    """Stream each check to stdout and the same timestamped log file."""
    project_root = Path(project_root).resolve()
    log_directory = Path(log_directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    log_file = _log_path(log_directory)

    commands = (
        (python, "-m", "pytest", "-v", "-ra", "--color=no", "tests"),
        (
            python,
            "-m",
            "black",
            "--check",
            "tests",
            "modaud.py",
            "make_release.py",
            "scripts/run_tests.py",
        ),
    )

    with log_file.open("w", encoding="utf-8", newline="") as log:
        print(f"Test run started: {datetime.now().astimezone().isoformat()}", file=log)
        print(f"Project: {project_root}", file=log)
        print(f"Log: {log_file}", file=log)
        log.flush()

        for command in commands:
            label = " ".join(command)
            print(f"\n> {label}")
            print(f"\n> {label}", file=log)
            log.flush()

            try:
                process = subprocess.Popen(
                    command,
                    cwd=project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except OSError as exc:
                message = f"Could not start check: {exc}"
                print(message)
                print(message, file=log)
                return 1, log_file

            if process.stdout is not None:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    print(line, end="", file=log, flush=True)

            return_code = process.wait()
            print(f"Check exit code: {return_code}")
            print(f"Check exit code: {return_code}", file=log)
            log.flush()

            if return_code:
                print(f"Test log saved: {log_file}")
                return return_code, log_file

        print("\nAll checks passed.")
        print("\nAll checks passed.", file=log)

    print(f"Test log saved: {log_file}")
    return 0, log_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use for pytest and Black",
    )
    arguments = parser.parse_args(argv)
    return run_checks(arguments.python)[0]


if __name__ == "__main__":
    sys.exit(main())
