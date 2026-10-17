DICE-SKIT AUDIO PIPELINE
========================

Builds one finished audio file, output\master\master_dialogues.mp3, from a
script of dialogue lines (input\dialogues_new.csv) and one recorded or
generated audio file per line.

The production it was built for is "The Dice of Destiny", a 20-minute
Mahabharata skit (The_Dice_of_Destiny_20min_v13.html).

Full technical detail, the design decisions and test results are in
docs\audio-concat-study.md, called "the study" below. References such as
"study sec. 10" point to a section of that document, not of this README,
whose own sections 1-9 are listed under CONTENTS.


CONTENTS
--------
  1. What the tool does
  2. Prerequisites and installation
  3. Using it with no configuration changes
  4. Command-line options
  5. Settings file (dice-skit.json)
  6. The script file (dialogues_new.csv)
  7. The Sarvam TTS API key
  8. What each file does
  9. Troubleshooting


------------------------------------------------------------------------------
1. WHAT THE TOOL DOES
------------------------------------------------------------------------------

  * Joins every line's audio into one master, in the order of the rows in
    dialogues_new.csv.

  * Finds each line's audio by the take number at the start of its filename,
    e.g. 129_Krishna.m4a is take 129. Two folders are searched:
      input\human_gen\      human recordings (always preferred)
      in-out\machine_gen\   TTS-generated lines (used until a recording
                            arrives)

  * Keeps inputs and outputs apart: input\ holds what you supply, in-out\
    what one stage writes for another, output\ everything the tool makes.
    "python stitch_mp3.py clean" empties the rebuildable outputs, keeping
    the logs. (study sec. 14)

  * Accepts .mp3, .m4a, .aac, .mp4, .wav, .flac, .ogg, .opus and .wma, and
    converts everything to one format (24 kHz mono 128 kbps MP3 by default).
    Conversions are cached in work\, so later runs are fast.

  * Evens out loudness: every line is brought to the same level (-19 LUFS by
    default), so quiet and loud voices sound alike. Peaks are limited so
    raised lines cannot distort. (study sec. 11)

  * Inserts gaps and sound effects between lines: a 2-second pause by
    default, or any named audio file (music, a dice sound, a longer pause)
    before or after a line. (study sec. 8)

  * Mixes background music under a line, with a cue string saying which
    track is loud when, e.g. b-2s-m-10s-b-5s. (study sec. 10)

  * Checks everything before writing anything. Missing, corrupt or
    unconvertible audio is reported in one list. Three mistakes always
    stop the build, because they would produce a master that sounds complete
    but is wrong (study sec. 9):
      - dialogues_new.csv is missing
      - a file has no character name in its name (e.g. 4.mp3)
      - two files in one folder claim the same take number
    A fourth, a file's character name not matching its CSV row, stops only
    the default strict build. With strict=0 it is listed and the file is
    used, since it is usually a spelling difference or a recording that
    covers several takes (e.g. 23_24_Shakuni.m4a).

  * Never damages the previous master: a failed run leaves it untouched.

  * Writes a full log of every run to log\, including which file was used
    for each line, every warning, and every setting in force.

  * Optional extras:
      fetch_drive.py    downloads recordings from a shared Google Drive folder
      generate_mp3.py   generates missing lines with Sarvam TTS
                        (switched off by default)
      modaud.exe        Windows console release; stitches a project folder
                        passed on the command line


------------------------------------------------------------------------------
2. PREREQUISITES AND INSTALLATION
------------------------------------------------------------------------------

The steps below are for Windows. Study sec. 0 covers
proxy problems and other details.

  What                     Needed for                       Required?
  -----------------------  -------------------------------  -------------------
  Python 3.10 or newer     everything                       yes
  FFmpeg (ffmpeg+ffprobe)  loudness leveling, converting    yes, in practice
                           .m4a/.aac, background music,
                           dry runs
  gdown, truststore        fetch_drive.py                   only to download
                                                            from Google Drive
  sarvamai, python-dotenv  generate_mp3.py                  only for TTS
  GNU Make, PyInstaller,   building modaud.exe              only to build release
  pytest, Black

Without FFmpeg the tool can still join MP3s that are already in the target
format, but it can't level loudness or convert other formats.

Step 1 - Install Python
  Download from https://www.python.org/downloads/windows/ and tick
  "Add python.exe to PATH" in the installer. In a NEW terminal, check:

      python --version
      python -m pip --version

  If "python" opens the Microsoft Store, turn off the python.exe entries in
  Settings > Apps > Advanced app settings > App execution aliases.

Step 2 - Install FFmpeg

      winget install --id Gyan.FFmpeg --scope user

  Then RESTART the terminal (restart VS Code completely if you use it), and
  check both programs:

      ffmpeg -version
      ffprobe -version

  If winget is blocked, download ffmpeg-release-essentials.zip from
  https://www.gyan.dev/ffmpeg/builds/, unzip it outside OneDrive, and add its
  bin folder to your PATH:

      setx PATH "%PATH%;C:\Users\<you>\tools\ffmpeg\bin"

Step 3 - Python packages for Google Drive downloads (optional)

      python -m pip install --user gdown truststore

  truststore is needed on corporate networks that inspect HTTPS. Never work
  around a certificate error by turning verification off.

Step 4 - Python packages for TTS generation (optional)

      python -m pip install --user sarvamai python-dotenv

  Also see section 7 for the API key.

Step 5 - Tests and formatting checks (contributors)

      python -m pip install -r requirements-dev.txt
      make test

  The terminal shows each test name and its result, then checks formatting
  with Black. The full output is also saved in
  output\log\tests-<date-time>.log. To run without Make:

      python scripts\run_tests.py

  GitHub Actions installs GNU Make and runs the same "make test" target on
  pull requests to main and pushes to main. To block merging when it fails,
  configure "Tests / tests" as a required status check in repository rules.
  The tests use temporary fixtures; they do not need the audio library.

Step 6 - Build the Windows console release (optional)

  Install GNU Make and the release-build and test packages:

      python -m pip install -r requirements-dev.txt -r requirements-build.txt

  Make sure both ffmpeg.exe and ffprobe.exe are on PATH, then run. This target
  first runs make test (pytest and Black), and builds only if both checks pass:

      make release-windows

  If GNU Make is not installed, the same builder can be run directly:

      python make_release.py

  Other Make targets:

      make test             run tests and formatting checks; save full log
      make clean            remove only output\release\
      make android          not configured until an Android framework is chosen

  "make release" is an alias for "make release-windows". The Android target
  intentionally reports that no build is configured yet; it does not install
  tools or create an APK.

  The build creates output\release\modaud.exe, bundles FFmpeg and FFprobe,
  and stages a three-line sample project. The Makefile route needs GNU Make;
  running the released executable does not require Python, Make or FFmpeg on
  PATH. See docs\audio-concat-study.md, section 16.


------------------------------------------------------------------------------
3. USING IT WITH NO CONFIGURATION CHANGES
------------------------------------------------------------------------------

Every setting has a sensible default, so nothing needs editing. Open a
terminal in the dice-skit folder:

  1. (Optional) Fetch recordings from Google Drive:

         python fetch_drive.py "<drive-folder-link>" dry=1    (preview)
         python fetch_drive.py "<drive-folder-link>"          (download)

     Or copy recordings into input\human_gen\ by hand. Name each file
     <take>-<CHARACTER>.<ext>, e.g. 17-YUDHISHTHIRA.m4a.

  2. Check everything without building:

         python stitch_mp3.py dry_run=1

  3. Build the master:

         python stitch_mp3.py              (stops if any line is missing)
         python stitch_mp3.py strict=0     (builds what exists, reports gaps)

  4. Listen to output\master\master_dialogues.mp3. The console summary and
     output\log\run<date-time>.log say what was used and what was skipped.

After building the Windows release, try its bundled sample:

      output\release\modaud.exe output\release\sample_project dry_run=1
      output\release\modaud.exe output\release\sample_project

For another project, pass that project's folder as the first argument.
Quote paths containing spaces. Remaining arguments are the usual
stitch_mp3.py options, such as strict=0 or dry_run=1.

A project from before 2026-10-08, with human_gen\, machine_gen\ and the
other folders at the top, is moved into the input / in-out / output layout
automatically on its first run. Files are moved, never copied or deleted.
If a file is in use (e.g. dialogues_new.csv open in Excel), nothing moves;
close it and run again.

While recordings are still arriving, strict=0 is the normal way to run it.
Use the default strict mode for the final build.


------------------------------------------------------------------------------
4. COMMAND-LINE OPTIONS
------------------------------------------------------------------------------

Options may be given in any order. A command-line option overrides
dice-skit.json for that one run.

stitch_mp3.py  (builds the master)

  N             Only the first N rows, e.g.  python stitch_mp3.py 20
  strict=1      Stop if any line's audio is missing, corrupt or unconvertible.
                Nothing is written. (default)
  strict=0      Build anyway, skipping unusable lines, and report them.
                Files whose character name differs from the CSV are listed
                and used as they are.
  dry_run=1     Run every check and conversion, then stop before building.
                Exits with an error only if one of the three always-fatal
                checks fails. Writes to output\work\ (caches) but never
                to the master.
  loudness=0    Keep each line at its recorded level (e.g. to compare).
  loudness=1    Level every line (default).
  help          Show the usage text.
  clean         Empty output\master\ and output\work\, after asking.
                Keeps output\log\, input\ and in-out\. Accepts only
                yes=1, which skips the question.

  Validate, then build only if nothing fundamental is wrong (PowerShell):

      python stitch_mp3.py dry_run=1
      if ($LASTEXITCODE -eq 0) { python stitch_mp3.py strict=0 }

fetch_drive.py  (downloads recordings into input\human_gen\)

  <link or ID>  The shared Google Drive folder. Optional if drive.folder_url
                is set in dice-skit.json.
  dry=1         List what would be downloaded; download nothing.
  overwrite=1   Download even files already present.
  limit=N       Stop after N downloads (for a trial).
  cookies=1     Use your browser login, for folders that are not link-shared.

settings.py  (the settings file)

  python settings.py          List every setting, its value, and where it
                              came from (built-in default or dice-skit.json).
  python settings.py write    Create or refresh dice-skit.json with full
                              comments, keeping your current values and any
                              comments you added.

generate_mp3.py  (TTS; off by default)

  python generate_mp3.py      Prints how to enable it; see section 7.

modaud.exe  (Windows console release; built by "make release")

  modaud.exe <project-folder> [stitch options]

  Example:
      output\release\modaud.exe "C:\Projects\my audio" strict=0

  The project folder contains input\dialogues_new.csv and optionally
  dice-skit.json. The executable uses its sibling ffmpeg\ folder, writes
  output to the project's configured folders, and accepts stitch_mp3.py
  options. TTS generation and the GUI are not part of this prototype.


------------------------------------------------------------------------------
5. SETTINGS FILE (dice-skit.json)
------------------------------------------------------------------------------

dice-skit.json, beside the scripts, holds every adjustable setting. EACH
SETTING IS EXPLAINED INSIDE THE FILE ITSELF: the key "_name" right after
"name" says what it does, the range of values allowed, and how to choose one.
Read the file for detail; this is only an overview.

Rules:
  * Keys starting with _ are comments and are ignored.
  * Delete a setting, or the whole file, to use the built-in default.
  * A typo, a wrong type or an out-of-range value stops the run with a
    message naming the setting, before any audio is touched.
  * Never put the Sarvam API key in this file (see section 7).

  Section            Main settings
  -----------------  ---------------------------------------------------------
  run                strict (default true), backend (auto / ffmpeg / python),
                     batch_size
  paths              input, in_out, output (the three top folders; may be
                     absolute, e.g. D:/dice-skit-output), then csv,
                     human_gen, machine_gen, master_dir, work, log, the
                     master filename, search_order (which folder wins)
  output             format (mp3), sample_rate (24000), bitrate (128000),
                     mono (true)
  fillers            short_seconds (2, the default gap), long_seconds (10)
  background_score   duck_db (-14, how far the faded track drops),
                     ramp_seconds (0.3), match_loudness (true), limit (0.95)
  loudness           enabled (true), target_lufs (-19), true_peak_dbfs (-1)
  tts                enabled (false), api_key_source (env / keyring),
                     language (en-IN), model, pace, sample_rate,
                     overwrite_existing
  drive              folder_url, use_cookies

Common changes:
  * Brisker pacing:      "fillers": { "short_seconds": 1.5 }
  * Quieter, less limiting:  "loudness": { "target_lufs": -21 }
  * Music more audible under speech:
                         "background_score": { "duck_db": -8 }
  * Always build what exists:  "run": { "strict": false }

After editing, "python settings.py" shows the values in force. Study
sec. 12 covers the design.


------------------------------------------------------------------------------
6. THE SCRIPT FILE (input\dialogues_new.csv)
------------------------------------------------------------------------------

One row per line, played in row order. Columns:

  sequence_number   The take number. A whole number that must never change
                    once a line is recorded; see "Revising the script" below.
  character_name    Must match the name in the audio's filename (ignoring
                    case, spaces and punctuation).
  dialogue_text     The line. Rows with empty text are skipped.
  speaker           The TTS voice. Only used by generate_mp3.py.
  filler_before     Audio before this line. Empty = 2-second gap.
                    N = no gap. A filename = that file (e.g. dice.mp3).
  filler_after      Audio after this line. Empty = nothing.
  background_score  Music file to mix under this line. Empty = no mix.
  score_cues        Which track is loud when (study sec. 10.4), e.g.
                      b-2s-m-10s-b-5s   music alone 2 s, then the line
                                        enters and leads for 10 s, ...
                      t-m-6s-b-15s      both start together; line leads 6 s
                      t-m               both start together; the line leads
                                        to the end
                      t-m-loop          both start together; the mix lasts
                                        exactly as long as the line: a
                                        shorter background repeats, a longer
                                        one fades out and is cut at the end
                                        of the line
                    m = the line, b = the background; letters alternate; the
                    last segment runs to the end, and its duration may be left
                    off. -loop may only come last.

Revising the script (study sec. 9.4): never renumber recorded lines. To move
a line, move its row. Give a new line the next unused number, even if that
puts it out of numeric order; the order comes from the rows, not the numbers.
To cut a line, delete its row and leave its file in place.

dialogues_new_xcel.xlsx is an earlier spreadsheet version of the script. The
tool reads only the CSV.


------------------------------------------------------------------------------
7. THE SARVAM TTS API KEY
------------------------------------------------------------------------------

The key is needed ONLY to generate lines with generate_mp3.py, which is off
by default. Building the master never needs it.

The key is never stored in dice-skit.json, because that file gets copied and
shared. tts.api_key_source in dice-skit.json says where the key is:

  "env" (default) - an environment variable called SARVAM_API_KEY.
    Either create a file named .env in the dice-skit folder containing:

        SARVAM_API_KEY=your_actual_api_key

    or set it for your Windows user (open a NEW terminal afterwards):

        setx SARVAM_API_KEY "your_actual_api_key"

    or for the current PowerShell window only:

        $env:SARVAM_API_KEY = "your_actual_api_key"

    A .env file is plain text: never share, email or upload it.

  "keyring" - Windows Credential Manager, the safer option. Install the
    package and store the key once:

        python -m pip install --user keyring
        python -c "import keyring; keyring.set_password('dice-skit', 'SARVAM_API_KEY', 'your_actual_api_key')"

    Then set "tts": { "api_key_source": "keyring" } in dice-skit.json. You can
    view or delete the key in Control Panel > Credential Manager.

To turn generation on:
  1. python -m pip install --user sarvamai python-dotenv
  2. Provide the key as above.
  3. Uncomment the four blocks marked "--- UNCOMMENT TO ENABLE ---" in
     generate_mp3.py.
  4. Set "tts": { "enabled": true } in dice-skit.json.
  5. python generate_mp3.py
Generation uses API credit. tts.overwrite_existing (false by default) keeps
lines already generated.


------------------------------------------------------------------------------
8. WHAT EACH FILE DOES
------------------------------------------------------------------------------

Programs (run these)
  stitch_mp3.py        Builds the master. Reads the CSV, finds each line's
                       audio, runs every check, levels loudness, mixes
                       background music, converts formats, inserts fillers,
                       and joins everything. Writes the run report and log.
  fetch_drive.py       Downloads recordings from a shared Google Drive folder
                       into input\human_gen\, skipping files already
                       present.
  generate_mp3.py      Generates missing lines with Sarvam TTS into
                       in-out\machine_gen\. Switched off by default.
  settings.py          Loads and checks dice-skit.json. Holds every setting's
                       default, allowed range and help text. Run it to list
                       the settings, or "write" to refresh the file.
  modaud.py            Console entry point packaged as
                       output\release\modaud.exe. Takes a project folder,
                       then stitch_mp3.py options.
  make_release.py      Builds the Windows executable, bundles FFmpeg tools,
                       and stages the sample project.
  scripts\run_tests.py Runs pytest verbosely and Black; displays and saves
                       results to output\log\tests-<date-time>.log.
  Makefile             test, release-windows (also release), android and clean
                       targets.
  requirements-build.txt
                       PyInstaller required only on the release build machine.

Modules (used by the programs)
  audio_common.py      Shared foundation: settings values, the CSV loader,
                       file matching by take number, audio validation, MP3
                       frame reading, command-line parsing and the run log.
  concat_backends.py   Joins audio files, either with FFmpeg or with a
                       built-in MP3 frame joiner that needs no FFmpeg.
                       Also converts inputs to the target format.
  loudness.py          Measures each line and brings it to the target
                       loudness, with peak limiting. (study sec. 11)
  layout.py            The input / in-out / output folder layout: moves an
                       old project into it, and runs "clean". (study
                       sec. 14)
  tests\               Fixture-based tests for layout migration, settings,
                       CSV loading and pre-flight checks.
  requirements-dev.txt pytest and Black for running the test suite locally.
  .github\workflows\tests.yml
                       Runs the tests and formatting check on GitHub.
  background_score.py  Reads cue strings and mixes background music under a
                       line. (study sec. 10)

Configuration and data
  dice-skit.json       All adjustable settings, with a comment on each.
  input\dialogues_new.csv
                       The script: lines, order, fillers, background music.
  dialogues_new_xcel.xlsx       Earlier spreadsheet form of the script.
  The_Dice_of_Destiny_20min_v13.html  The skit's script, for reading.
  .env                 (Not shipped; you create it.) The Sarvam key, if you
                       use the "env" option.

Folders (study sec. 14)
  input\               What you supply. Never deleted by the tool.
    human_gen\           Recorded lines, plus named sound files (music,
                         dice).
  in-out\              Written by one stage, read by another.
    machine_gen\         TTS-generated lines.
  output\              Everything the tool makes; all rebuildable.
    master\              The finished master.
    work\                Caches and generated silence.
    log\                 One log file per run. Kept by "clean".
    release\             Windows executable release and sample project.
  docs\                audio-concat-study.md: setup guide, design, decisions
                       and test results.
  backup\              Numbered snapshots (v1, v2, ...) of every source file,
                       taken automatically before each change made with
                       Claude Code.
  .claude\             Claude Code project settings: the hook that takes
                       those snapshots (hooks\snapshot.py). Not needed to run
                       the tool.
  CLAUDE.md            Project rules for Claude Code, e.g. keeping this
                       README and Changelog.txt up to date. Not needed to run
                       the tool.


------------------------------------------------------------------------------
9. TROUBLESHOOTING
------------------------------------------------------------------------------

  Message                       What to do
  ----------------------------  ----------------------------------------------
  ERROR in settings: ...        dice-skit.json has a mistake; the message names
                                the setting. Fix it, or delete that key.
  MISSING (...)                 Normal while recordings are incomplete. Fetch
                                them, or run with strict=0.
  CHARACTER MISMATCH            A file's name gives a different character from
                                its CSV row. Stops a strict build; with
                                strict=0 it is listed and the file is used.
                                Often harmless (a spelling difference, or
                                23_24_Shakuni.m4a covering two takes), but if
                                a line was renumbered, fix the file, not the
                                CSV number. Read this list before the final
                                strict build.
  TAKE-ID COLLISION             Two files in one folder have the same take
                                number. Move the unwanted one to
                                input\human_gen\retired\.
  NO CHARACTER IN FILENAME      Rename e.g. 4.mp3 to 4-CHARACTER.mp3.
  CONVERSION FAILED ...         FFmpeg is missing or not on PATH (step 2).
  PEAK-LIMITED                  Normal: quiet lines were raised to the target.
                                Lower loudness.target_lufs to reduce it.
  ALREADY CLIPPED               That recording is distorted; re-record it.
  BACKGROUND SCORE FAILED       The music file is missing or score_cues is
                                malformed; the block says which.
  ffmpeg not found after        Restart the terminal (or VS Code completely).
  installing
  RELEASE ERROR: ... was not    Add the FFmpeg bin folder to PATH, including
  found on PATH                 both ffmpeg.exe and ffprobe.exe, then rerun
                                make release.
  SSLError: certificate         Corporate network:
  verify failed                 python -m pip install --user truststore

More in study sec. 0.11.