import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IGNORED_DIRECTORIES = {
    ".git",
    "__pycache__",
    "dialogues-mp3",
    "log",
    "machine_gen",
    "tmp",
    "work",
}
TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".csv",
    ".css",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".pyi",
    ".rst",
    ".scss",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {".gitattributes", ".gitignore", "license", "makefile"}
NBSP = b"\xc2\xa0"


def main():
    changed_files = 0

    for directory, subdirectories, filenames in os.walk(PROJECT_ROOT):
        subdirectories[:] = [
            name for name in subdirectories if name not in IGNORED_DIRECTORIES
        ]

        for filename in filenames:
            path = Path(directory, filename)
            if (
                path.suffix.lower() not in TEXT_SUFFIXES
                and path.name.lower() not in TEXT_FILENAMES
            ):
                continue

            original = path.read_bytes()
            normalized = original.replace(NBSP, b" ")
            if normalized != original:
                path.write_bytes(normalized)
                print(f"Normalized: {path.relative_to(PROJECT_ROOT)}")
                changed_files += 1

    if changed_files:
        print(f"Replaced non-breaking spaces in {changed_files} file(s).")
    else:
        print("No non-breaking spaces found in project text files.")


if __name__ == "__main__":
    main()
