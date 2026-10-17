# dice-skit

## Keep README.txt and Changelog.txt up to date

Whenever a functionality is added, or an existing one changes, update both
`README.txt` and `Changelog.txt` in the same piece of work.

**Only after verification.** Update them once the change has been run and
shown to work: a test build, a dry run, or measured output. Don't record
anything that is specified but not built, or built but not tested. If
verification fails or is skipped, leave both files alone and say so.

**What counts:**
- New or changed behaviour of any script
- New, renamed or removed command-line options
- New, renamed or removed settings in `config.json`, or changed defaults
- New or changed CSV columns
- New, renamed or deleted source files
- Changed prerequisites or installation steps

Pure refactors with no visible effect, and changes to `docs/` only, don't
need an entry.

### Changelog.txt

- One entry per change, at the top: the newest goes first.
- Heading line: `YYYY-MM-DD  Short title`, with the section number from
  `docs/audio-concat-study.md` if there is one, e.g. `(sec. 11)`.
- Body: at most 50 words, written for a user, not a programmer.
- Say what changed and what it means for the user; include a measured result
  if there is one.
- Never rewrite older entries, except to correct a factual error.

### README.txt

Update every section the change affects. Usually that means:
- Section 1, for what the tool does
- Section 2, for prerequisites and installation
- Section 4, for command-line options
- Section 5, for settings in `config.json`
- Section 6, for CSV columns
- Section 8, for what each file does
- Section 9, for new messages and how to fix them

Keep it readable by a non-programmer, with lines under 80 characters where
possible. Before finishing, check that every setting name, default value and
command it quotes matches the code.