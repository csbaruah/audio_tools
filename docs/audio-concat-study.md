# Audio concatenation: can this be done without FFmpeg?

A study of how to concatenate the dice-skit dialogue audio, written to answer two
questions:

1. Can MP3 concatenation be done with native Windows or native Python APIs
   instead of shelling out to FFmpeg?
2. Will whatever we choose still work when the pipeline extends beyond `.mp3` to
   other formats such as `.m4a`?

**Answers, in short: yes for MP3, and no — not with the same technique — for
`.m4a`.** MP3 can be spliced at the byte level with pure Python and no
dependencies. `.m4a` fundamentally cannot, and needs a real muxer. That
asymmetry is why `concat_backends.py` has a backend interface with two
implementations rather than one hard-coded path.

Section 0 below is a practical setup guide for running the pipeline on a new
machine; the rest of the document is the study itself. Section 9 covers the
take-ID rules and the mandatory pre-flight checks that keep recordings attached
to the right lines when the script is revised. Section 10 covers mixing a
background score under a line, section 11 evening out loudness across lines,
and section 12 the settings file, dice-skit.json. Section 13 proposes, but
does not yet implement, a graphical interface for Windows, Android, iOS and
the web. Section 14 describes the input / in-out / output folder layout and
the clean command. Section 15 records the longer-term hybrid product
direction. Section 16 proposes the first Windows executable as a local
processing prototype. Section 0.13 covers the optional MSYS2 Windows
development toolchain.

---

## 0. Setting up on a new PC

Everything here assumes Windows. The pipeline is plain Python and does not need
to be installed or built.

### 0.1 What actually needs installing

Worth knowing before you start, because it is less than it looks:

| Component | Needs | Why |
|---|---|---|
| `stitch_mp3.py` | **Python only** — zero third-party packages | Verified: `audio_common.py`, `concat_backends.py`, `stitch_mp3.py` and `generate_mp3.py` import nothing outside the standard library |
| `stitch_mp3.py` with `.m4a` / `.aac` / `.mp4` inputs, or inputs not already 24 kHz mono 128 kbps MP3 | **+ FFmpeg** | AAC cannot be carried in an MP3 stream and resampling needs a decoder — see §7 |
| `stitch_mp3.py dry_run=1` | **+ FFmpeg** whenever any input needs normalizing | It normalizes at full depth, so it needs FFmpeg wherever a real run would — see §9.8 |
| `fetch_drive.py` | **+ `gdown`, `truststore`** | Drive crawling; `truststore` for corporate TLS — see §0.5 |
| `generate_mp3.py` | `sarvamai`, `python-dotenv`, plus a `SARVAM_API_KEY` | Only if you re-enable TTS generation, which is off by default |

So a machine that only ever stitches already-conforming MP3s needs nothing but
Python. FFmpeg becomes necessary the moment a non-MP3 container or an off-format
file appears, which in practice is immediately.

### 0.2 Install Python

Python **3.10 or newer**. That floor comes from `gdown` and `truststore`, which
both declare `Requires-Python >= 3.10`; the pipeline code itself would run on
older versions, but there is no reason to pick one.

Get it from [python.org](https://www.python.org/downloads/windows/) and **tick
"Add python.exe to PATH"** in the installer. Then confirm in a new terminal:

```powershell
python --version
python -m pip --version
```

If `python` opens the Microsoft Store instead of running, the Store alias is
shadowing the real install: Settings → Apps → Advanced app settings → App
execution aliases, and turn off the `python.exe` entries.

### 0.3 Install FFmpeg

```powershell
winget install --id Gyan.FFmpeg --scope user
```

`--scope user` needs no administrator rights. The download is ~240 MB, so allow
time on a slow link.

**Then restart your terminal** — winget writes the PATH entry to the registry,
and processes already running do not see it. If you are in VS Code, restart VS
Code itself, not just the integrated terminal, because the terminal inherits its
environment from the extension host.

Verify **both** binaries, not just `ffmpeg`:

```powershell
ffmpeg -version
ffprobe -version
```

The pipeline calls `ffprobe` separately for durations. `Gyan.FFmpeg` is a full
static build that bundles both; some other winget FFmpeg packages alias only
`ffmpeg`.

If `winget` is blocked by policy, download `ffmpeg-release-essentials.zip` from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/), extract it somewhere you own,
and add its `bin` folder to your user PATH:

```powershell
setx PATH "%PATH%;C:\Users\<you>\tools\ffmpeg\bin"
```

`setx` writes user-scope PATH and needs no admin. Do not extract into a
OneDrive-synced folder — a few hundred MB of binaries will sync pointlessly and
OneDrive occasionally locks files mid-transfer.

### 0.4 Install the Python packages

Only needed for `fetch_drive.py`:

```powershell
python -m pip install --user gdown truststore
```

`--user` avoids needing admin rights. This pulls in `beautifulsoup4`,
`filelock`, `requests`, `tqdm` and `typing-extensions` as gdown's dependencies.

### 0.5 If the new PC is behind a corporate TLS proxy

Skip this on a home machine. On a corporate network it is not optional.

Symptom, when running `fetch_drive.py`:

```
SSLError: certificate verify failed: self-signed certificate in certificate chain
```

The cause is specific and worth understanding, because the obvious "fix" is the
wrong one. A TLS-inspecting proxy re-signs HTTPS with an internal certificate
authority. Python's own default SSL context consults the **Windows certificate
store**, which has that CA, so `urllib` works fine. But `gdown` uses `requests`,
which ships **certifi's fixed CA bundle** — the corporate CA is not in it, so
verification fails.

`truststore` redirects verification back at the OS store, and
`fetch_drive.py` calls `truststore.inject_into_ssl()` before importing gdown. So
the fix is simply to have it installed:

```powershell
python -m pip install --user truststore
```

Do **not** work around this by disabling certificate verification. gdown exposes
a `verify=False` option, and it would appear to work, but it turns off
certificate checking on exactly the network where inspection is happening.

### 0.6 Copy the project

Copy the whole `dice-skit` folder, or at minimum:

```
dice-skit/
  *.py                    the scripts and modules
  dice-skit.json          settings
  input/
    dialogues_new.csv     the running order, fillers and background scores
  docs/
    audio-concat-study.md this document
```

The folders follow the layout in §14:

| Directory | Contents | Copy it? |
|---|---|---|
| `input/human_gen/` | Human recordings from Drive, plus named filler audio (sound effects, opening music) | Only the fillers — re-fetch the rest |
| `in-out/machine_gen/` | Sarvam TTS output from `generate_mp3.py` | Only if you have TTS output worth keeping |
| `output/work/` | Generated fillers and the transcode, loudness and mix caches | No — rebuildable |
| `output/master/` | **Output only**: `master_dialogues.mp3` | No |
| `output/log/` | One `run<timestamp>.log` per invocation | No |

Everything under `output/` can be deleted and rebuilt at any time without
touching source audio; `python stitch_mp3.py clean` does that safely (§14.5).
A project copied from an older machine with the old top-level folders is moved
into this layout automatically on its first run (§14.4).

`stitch_aud.py`, the original single-file script, was deleted on 2026-10-05
(§5.1). Do not copy it from an older machine.

All paths are anchored to the script directory rather than the working
directory, so it does not matter where you invoke from.

### 0.6a Versioned backups (automatic)

The folder is not under version control. Instead, `backup/` holds numbered
snapshots of every source file:

| Folder | Contents |
|---|---|
| `backup/v1/` | `audio_common.py`, `stitch_mp3.py` and `docs/audio-concat-study.md` as they were before the §9 checks were added (2026-10-04) |
| `backup/v2/` … | Every source file, in the project's layout, taken automatically |

A Claude Code hook in `.claude/settings.json` takes them. Each request arms one
snapshot. The first edit after it copies all sources into the next `vN`
**before** the edit lands. Sources are the top-level `.py .csv .xlsx .html .md
.json .txt` files plus `docs/`. Audio, `work/`, `log/` and `backup/` itself are
never copied. A snapshot identical to the latest version is skipped.

To restore, copy files back out of the version you want. To take a snapshot by
hand:

```powershell
python .claude\hooks\snapshot.py now
```

### 0.7 Fetch the audio

```powershell
cd "C:\path\to\dice-skit"
python fetch_drive.py "https://drive.google.com/drive/folders/<id>" dry=1
python fetch_drive.py "https://drive.google.com/drive/folders/<id>"
```

Run `dry=1` first: it lists exactly what would be downloaded and writes nothing.

**Quote the URL.** Drive links usually carry a query string, and PowerShell
treats `&` as a command separator, so an unquoted link can be silently
truncated.

The folder must be shared as "Anyone with the link" — `fetch_drive.py` uses
anonymous access and never touches your Google credentials. To check, open the
link in a private browser window while signed out.

Downloads land in **`input/human_gen/`** as one flat directory, whatever Drive's
subfolder structure was — the Drive folder holds human-recorded takes. Files
already present in *either* `input/human_gen/` or `in-out/machine_gen/` are skipped, matched
by leading sequence number across extensions, so an existing
`16_YUDHISHTHIRA.m4a` satisfies a remote `16-YUDHISHTHIRA.mp3`. Use
`overwrite=1` to force a re-fetch, `limit=N` for a trial run.

### 0.8 Build the master

```powershell
python stitch_mp3.py
```

Strict by default: if any row's audio is missing, corrupt, or in a format that
cannot be converted, it prints one consolidated report and **exits 1 without
writing or modifying the master**. To build from a partial set instead:

```powershell
python stitch_mp3.py strict=0        # skip what is unusable, stitch the rest
python stitch_mp3.py 20              # only the first 20 rows
python stitch_mp3.py 20 strict=0     # both, in either order
python stitch_mp3.py dry_run=1       # every check, no master; see §9.8
```

Output is `dialogues-mp3/master_dialogues.mp3`.

What each mode does with each kind of fault:

| Mode | Missing / corrupt / off-format audio, missing named filler | Mandatory checks (§9.6) | Master | Exit |
|---|---|---|---|---|
| `strict=1` (default) | abort | abort | untouched on abort | 1 on any fault |
| `strict=0` | skip and stitch the rest | **abort**, except check 2, which is listed and allowed | written if no fatal mandatory fault | 1 on fatal mandatory fault, else 0 |
| `dry_run=1` | report | report; check 2 counts as allowed | **never** written | 1 on fatal mandatory fault, else 0 |

The mandatory checks:

1. `dialogues_new.csv` is missing.
2. A file's character name differs from its CSV row's (`4-DURYODHANA.mp3` on a
   SHAKUNI row). **Aborts only under `strict=1`.** Under `strict=0` it is listed
   in full under `CHARACTER MISMATCH (allowed, strict is off)`, counted as an
   ordinary problem, and the file is used as it is (§9.6).
3. A file has no character name (`4.mp3`).
4. Two files in one directory map to the same take (`4_A.mp3` and `0004_B.mp3`).

Checks 1, 3 and 4 abort a real run **whatever `strict` says, and there is no
flag to override them**.

Each guards against a master that sounds complete but is wrong. See §9 for why.

**Validate before building.** `dry_run=1` runs every check, including the
FFmpeg normalization pass, and stops before stitching. `strict` is ignored under
`dry_run`. Two things are easy to miss:

- It **does write to `output/work/`**: the normalized cache, and any generated filler
  the CSV names. Only the master is guaranteed untouched. This is not wasted
  work, because the real run then reuses the cache.
- It exits non-zero **only** for a mandatory fault, so it chains straight into a
  build while audio is still incomplete:

```powershell
python stitch_mp3.py dry_run=1
if ($LASTEXITCODE -eq 0) { python stitch_mp3.py strict=0 }
```

### 0.9 Verify it worked

```powershell
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 dialogues-mp3\master_dialogues.mp3
start dialogues-mp3\master_dialogues.mp3
```

`start` opens it in your default player. Scrub across a join to confirm the
audio is continuous and the reported duration is right.

A healthy run prints something like:

```
Usable   : 14
  from human_gen       : 4
  from machine_gen     : 10
Already in target format : 10
Reused from cache        : 6
Passed   : manifest present, character names agree, one file per take
Format   : MPEG-2 Layer 3 24000 Hz mono 128 kbps (consistent across 16 file(s))
Selected     : ffmpeg
Warnings    : 140
Run log     : ...\log\run20260911-194904.log
```

The second run should report the transcodes as `Reused from cache`, doing no
FFmpeg work at all.

The `Passed` line comes from the `MANDATORY CHECKS` block, which is printed
straight after `PRE-FLIGHT` on every run. If that block prints anything other
than `Passed`, the run will abort; see §9.6.

A dry run ends with this block instead of `ALL DONE`:

```
DRY RUN COMPLETE
======================================================================
Mandatory check failures : 0
Other problems           : 139
Warnings                 : 139

The master was NOT written or modified. work/ may have been updated
(normalized cache, generated fillers); a real run reuses it.

A real run would abort under strict=1, or build an INCOMPLETE master under strict=0.
```

Every run also writes `log/run<timestamp>.log` — see §0.10.

### 0.10 Reading the run log

Each invocation of `stitch_mp3.py` writes a timestamped log under `output/log/`,
containing the full console transcript, a per-row source manifest, and every
warning. It is written even when the run aborts, so a failed run still leaves a
complete record.

**Which file was used for each row** is recorded in a `ROW SOURCES` table. This
is log-only: at 154 rows it would swamp the console, but it is exactly what you
want when auditing which take ended up in the master.

```
ROW SOURCES
======================================================================
  seq  source        file                                status
----------------------------------------------------------------------
    0  machine_gen   0-SAMAY.mp3                         ok
    1  machine_gen   1-DURYODHANA.mp3                    ok
   10  human_gen     10_YUDHISHTHIRA.m4a                 ok
   11  -             11-DURYODHANA.mp3                   MISSING
   16  human_gen     16_YUDHISHTHIRA.m4a                 ok
----------------------------------------------------------------------
  not found      140
  human_gen      4
  machine_gen    10
```

The rest of the log looks like:

```
run log - 2026-09-11T19:49:04
command: stitch_mp3.py strict=0
======================================================================
... full transcript ...
WARNING: no audio for row 13 / 11-DURYODHANA.mp3 - searched human_gen, machine_gen
...
======================================================================
WARNING SUMMARY (140)
======================================================================
   1. no audio for row 13 / 11-DURYODHANA.mp3 - searched human_gen, machine_gen
   2. ...
```

Warnings are written to the log rather than echoed to the console, because the
console already carries consolidated `MISSING` / `CORRUPT` lists and 140
individual lines would bury the summary. To find them:

```powershell
Select-String -Path log\run*.log -Pattern '^WARNING:'
Select-String -Path log\run*.log -Pattern 'human_gen|machine_gen' -Context 0,0
Get-Content (Get-ChildItem log\*.log | Sort LastWriteTime -Desc)[0].FullName -Tail 40
```

Seven kinds of warning are recorded. The first three are ordinary problems:

- **`no audio for row N / <file> - searched human_gen, machine_gen`** — the
  sequence number resolved in neither directory. This is the expected state
  while audio is still incomplete.
- **`filler_before "<name>" not found ... used short_filler.mp3`** — a named
  leading filler is absent, so the row fell back to plain silence. The
  `filler_after` equivalent reads **`... appended nothing`** instead, since
  there is no default trailing gap to fall back to.
- **`could not convert <file> to the target format`** — usually FFmpeg missing.

The other four come from the mandatory checks (§9.6). Any one of them means the
run aborted, or under `dry_run` that a real run would abort:

- **`dialogues_new.csv not found; refusing to stitch in numeric take-ID order`**
- **`character mismatch: row N is <CSV character> but resolved to <file>`**
- **`no character name in filename: <file>`**
- **`take N has K files in <directory>: <names>`**

```powershell
Select-String -Path log\run*.log -Pattern '^WARNING: (character mismatch|no character|take \d+ has|dialogues_new)'
```

### 0.11 Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ERROR in settings: dice-skit.json: ...` | The settings file has a typo, a wrong type or an out-of-range value; the message names the key. Fix it, or delete the key to use its default. Nothing was built. See §12. |
| `PEAK-LIMITED` | Quiet lines were raised to the loudness target and their loudest peaks were held at the ceiling. Normal for speech. To limit less, lower `loudness.target_lufs`, e.g. to −21 (§11.4). |
| `ALREADY CLIPPED` | The source file is distorted. Leveling cannot repair it; re-record or regenerate that line. |
| `ffmpeg: command not found` after installing | Terminal predates the PATH change. Restart the terminal, or VS Code entirely. |
| `ffprobe` missing but `ffmpeg` works | An FFmpeg package that aliases only `ffmpeg`. Install `Gyan.FFmpeg`. |
| `CONVERSION FAILED ... ffmpeg was not found on PATH` | A non-MP3 container or off-format input needs transcoding. Install FFmpeg (§0.3). |
| `SSLError: certificate verify failed` | Corporate TLS proxy. `pip install truststore` (§0.5). |
| `Failed to retrieve folder contents ... (status code 404)` | Drive folder is not link-shared, or the link points at a file. |
| `ERROR: gdown is not installed` | `pip install --user gdown truststore`. |
| Aborts with a long `MISSING (...)` list | Expected when audio is incomplete. Fetch the rest, or use `strict=0`. |
| `ABORTED - MANDATORY CHECKS FAILED` | One of the four checks in §9.6 failed. `strict=0` does not bypass it, and nothing does. Fix what the blocks above it name. |
| `CHARACTER MISMATCH` | The file for that take ID belongs to a different character, which is the usual sign of renumbering. Do not rename the CSV row to match. Find the right take, or give the line a new ID (§9.4). |
| `NO CHARACTER IN FILENAME` | A bare `4.mp3`. Rename it to `4-CHARACTER.mp3`. |
| `TAKE-ID COLLISION` | Two files in one directory map to the same take. `4_A.mp3` and `0004_B.mp3` collide because padding is ignored. Move the unwanted one to `human_gen/retired/`. |
| `MANIFEST MISSING` | `dialogues_new.csv` is absent. The run always aborts, because numeric order is not script order (§9.3). |
| A row picks the wrong take | Both directories hold that sequence number. `human_gen/` always wins — delete or rename the copy you do not want. The `MULTIPLE FILES PER SEQUENCE` block names both, and `ROW SOURCES` in the log shows what every row used. |
| Downloaded files are not being used | They must start with the sequence number. `17_Anything.m4a` works; `Yudhishthira17.m4a` does not. |
| Files placed in `output\master\` are ignored | By design — it is output only. Put source audio in `input\human_gen\` or `in-out\machine_gen\`. |
| `MOVING TO THE NEW FOLDER LAYOUT (one time)` | Normal, once, on a project from before 2026-10-08. Folders are moved, not copied (§14.4). |
| `cannot move to the new folder layout ...` | A destination already exists, or a file was in use. Nothing was moved. Close the file (often `dialogues_new.csv` in Excel) or resolve the named folder, and run again. |
| `FORMAT MISMATCH` after normalization | A transcode did not land on the target. Check `output.sample_rate` / `output.bitrate` in `dice-skit.json`. |
| `NAMED FILLER NOT FOUND` | A `filler_before` file is absent from both directories. It falls back to silence under `strict=0`. |
| `BACKGROUND SCORE FAILED` | A row's score was not applied, so its plain dialogue was used. The block names the cause: background not found, malformed `score_cues`, only one of the two columns set, or FFmpeg missing. An ordinary problem: `strict=1` aborts, `strict=0` continues. See §10.7. |
| `CUE STRING LONGER THAN THE AUDIO` | The mix was made, but a cue outlasted the audio. The other track came back to full volume (§10.7). Shorten that row's durations. |
| `NAMED TRAILING FILLER NOT FOUND` | A `filler_after` file is absent. Nothing is appended — there is no default trailing gap. Check the spelling, or use `"N"` / an empty cell if no gap was intended. |
| A row has no gap before it and you expected one | Its `filler_before` cell holds `N`, `no` or `none` (case-insensitive), which suppresses the leading filler. An empty cell gives the 2-second default instead. |
| Python opens the Microsoft Store | Disable the `python.exe` App execution aliases (§0.2). |

### 0.12 Quick reference

```powershell
# one-time setup
winget install --id Gyan.FFmpeg --scope user     # then RESTART the terminal
python -m pip install --user gdown truststore

# every time
cd "C:\path\to\dice-skit"
python fetch_drive.py "<drive-folder-url>" dry=1   # preview  -> machine_gen/
python fetch_drive.py "<drive-folder-url>"         # download -> machine_gen/
python stitch_mp3.py dry_run=1                     # validate; exit 1 = mandatory fault
python stitch_mp3.py                               # strict; or strict=0
python stitch_mp3.py help                          # every flag
Get-Content (Get-ChildItem log\*.log | Sort LastWriteTime -Desc)[0].FullName -Tail 40
```

### 0.13 Install the Windows development toolchain with MSYS2 (optional)

This installs native Windows versions of GNU Make, GCC, G++, and GDB. It is
useful for Windows C/C++ work and for running the `make release` command in
§16. It is separate from the Python/audio prerequisites above.

1. Install MSYS2 from [msys2.org](https://www.msys2.org/), or from an
   elevated PowerShell window:

   ```powershell
   winget install --id MSYS2.MSYS2
   ```

2. Launch **MSYS2 UCRT64** from the Start menu. Use this terminal for the
   following `pacman` commands.

3. Update the MSYS2 package database and base packages:

   ```bash
   pacman -Syu
   ```

   If the update asks you to close the terminal, close it, launch MSYS2 UCRT64
   again, and run `pacman -Syu` once more.

4. Install the build tools, UCRT64 compiler toolchain, and debugger:

   ```bash
   pacman -S --needed base-devel mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-gdb
   ```

   Accept the default package selection when prompted.

5. Verify the tools in MSYS2 UCRT64:

   ```bash
   make --version
   gcc --version
   g++ --version
   gdb --version
   ```

From the repository, `make test` runs the tests and Black check,
`make release-windows` runs those checks before building the Windows console
executable, and `make clean` removes only `output\release\`. `make release` is
retained as an alias for `make release-windows`. `make android` reports that no
Android build has been configured; choose the app framework and its build
command before wiring that target to a real build.

For this project's Windows release build, the same terminal must also resolve
the **Windows** Python interpreter, `ffmpeg.exe` and `ffprobe.exe`:

```bash
which python
which ffmpeg
which ffprobe
```

If they are not found, add their Windows installation folders to the Windows
user `PATH` (for example, `C:\Program Files\python` and
`C:\installable\ffmpeg\bin` on this machine), then open a new MSYS2 UCRT64
terminal. Confirm `python --version`, `ffmpeg -version` and `ffprobe -version`
before running `make release` from the project folder. The release is a
Windows executable, so do not use WSL's Linux Python to build it.

MSYS2 provides native Windows development tools; it is not WSL. WSL2 is a
separate Linux environment and is useful for Linux-targeted work. This GCC/G++
installation is a general-purpose Windows compiler, not an embedded-board
cross compiler. Android native builds use the Android NDK's Clang toolchain;
install and configure the NDK with the selected Android framework.

---

## 1. Why MP3 can be spliced and `.m4a` cannot

This is the single fact the whole design rests on.

**An MP3 file is not a container.** It is a bare, flat sequence of
self-contained MPEG audio frames, optionally bracketed by ID3 metadata. Each
frame carries its own 4-byte header declaring its own bitrate, sample rate and
channel mode, followed by its own audio data. There is no index, no table of
contents, and no file-level structure that describes the stream as a whole.
Delete a frame from the middle and the file is still valid — just shorter. Glue
two files' frames together and you have a valid, longer file.

**An `.m4a` file is a container** — ISO Base Media File Format, the same family
as `.mp4`. The audio payload lives in an `mdat` box, but the information needed
to *interpret* it lives in a separate `moov` box: `stts` (frame durations),
`stsz` (frame sizes), and `stco`/`co64` (absolute byte offsets of every frame
into the file). Concatenating two `.m4a` files byte-wise gives you a file with
two `moov` boxes and two `mdat` boxes, where the first `moov`'s offset table
describes only the first `mdat` and every offset in the second `moov` is now
wrong. Players read the first `moov`, play the first file, and stop.

There is no byte-level trick that fixes this. The sample tables must be
recomputed and a single new `moov` written — which is, by definition, muxing.

---

## 2. What this pipeline actually produces

Measured from the real source files, not assumed:

| File | Format | Frame size | Samples/frame | Notes |
|---|---|---|---|---|
| `1-DURYODHANA.mp3` (and all dialogue) | MPEG-2 Layer III, 24 000 Hz, mono, 128 kbps | 384 bytes | 576 (24.0 ms) | No ID3, no Xing header |
| `short_filler.mp3` | MPEG-2 Layer III, 24 000 Hz, mono, 128 kbps | 384 bytes | 576 (24.0 ms) | 44-byte ID3v2 head, 1 Xing frame |
| `samay-1.mp3` | MPEG-2 Layer III, **22 050 Hz**, mono, 128 kbps | 417 bytes | 576 (26.1 ms) | **Format outlier — see §6** |

Two things follow.

**24 kHz means MPEG-2, not MPEG-1.** MPEG-1 Layer III only defines 32 000,
44 100 and 48 000 Hz. The 16/22.05/24 kHz rates belong to MPEG-2 (formally the
"lower sampling frequency" extension), which halves the frame size to 576
samples. So:

```
frame length = 72 × bitrate / sample_rate + padding
             = 72 × 128000 / 24000
             = 384 bytes
```

Every dialogue frame is exactly 384 bytes and exactly 24 ms. This is true CBR,
uniform across every input. It is close to the ideal case for byte-level
splicing.

**Layer III side information is 9 bytes here.** Side info size depends on
version and channel count — 17/32 bytes for MPEG-1 mono/stereo, 9/17 for
MPEG-2. That number matters because it is where the Xing/Info tag lives:

```
offset of Xing/Info magic = 4 (header) + 0 (no CRC) + 9 (side info) = 13
```

---

## 3. What a correct pure-Python splice must handle

Naive byte concatenation — `copy /b a.mp3+b.mp3 out.mp3`, or
`shutil.copyfileobj` in a loop — mostly plays. "Mostly" is the problem. Three
things need explicit handling.

### 3.1 ID3 tags must be stripped

ID3v2 sits at the *head* of a file: the magic `ID3`, two version bytes, a flags
byte, then a four-byte **syncsafe** size (7 significant bits per byte, so the
high bit never sets and the length can never imitate a frame sync). ID3v1 is a
fixed 128-byte block at the *tail* beginning with `TAG`.

Concatenate naively and every input from the second onwards contributes an ID3
block *in the middle of the audio stream*. Decoders are supposed to resync past
it. Many do, silently. Some emit an audible click. None benefit.

`audio_common.strip_tags()` returns the `(start, end)` offsets of the real audio
and the splicer only ever copies bytes in that range. It handles chained ID3v2
headers and the optional footer flag (`0x10`, which adds 10 bytes).

### 3.2 Xing/Info seek headers must be dropped and rewritten

An MP3's first frame is often not audio at all. It is a metadata frame whose
audio payload is zeroed and whose side-info area instead holds:

| Field | Size | Meaning |
|---|---|---|
| magic | 4 | `Xing` (VBR) or `Info` (CBR); `VBRI` is a Fraunhofer variant at a fixed offset of 36 |
| flags | 4 | bit 0 frame count, bit 1 byte count, bit 2 TOC, bit 3 quality |
| frames | 4 | number of audio frames, excluding this one |
| bytes | 4 | total stream size in bytes, including this frame |
| TOC | 100 | seek table; entry *i* = `256 × byte_offset(i/100 of duration) / total_bytes` |

This is what lets a player show a duration instantly and scrub accurately
without scanning the whole file.

Leaving these in place during a splice is doubly wrong: the frame decodes as
~24 ms of silence in the middle of the stream, and the *first* file's frame
count becomes the whole output's claimed duration. A 90-minute master would
report as 7 seconds.

So the splicer drops every input's seek frame and writes exactly one fresh
`Info` header describing the joined stream. This is the pure-Python equivalent
of FFmpeg's `-write_xing 1`, and it is why `RawMp3Backend.finalize()` is a
no-op — the work already happened inline.

Verified on a real 10-dialogue master: 9 input seek frames dropped, one written,
frame count 3492 matching the actual 3492 audio frames, byte count 1 341 312
matching the file size exactly, TOC monotonic from 0 to 253.

### 3.3 The bit reservoir — a caveat, not a problem here

MP3 frames are not quite as independent as §1 implies. Layer III lets a frame
borrow unused space from *preceding* frames via the `main_data_begin` field: a
backward byte pointer into what is called the bit reservoir. Its width is 9 bits
on MPEG-1 (up to 511 bytes) and 8 bits on MPEG-2/2.5 (up to 255 bytes, which is
this pipeline's case).

At a splice point, the first frame of the appended file may carry a nonzero
`main_data_begin` pointing at a reservoir that, in the joined stream, belongs to
a different recording. The result is one frame — 24 ms — of possible garbage.

Why it is ignorable here: **every join in this pipeline is adjacent to a silence
filler.** The sequence is `dialogue, silence, dialogue, silence, …`, so any
artefact lands inside, or immediately next to, two seconds of digital silence.
There is no audible content to corrupt. Encoders also typically reset
`main_data_begin` to 0 at the start of a file, making the worst case rare in
practice.

A fully gapless splice would additionally require reading LAME's encoder
delay/padding values from the Info tag and trimming samples at each boundary.
That matters for splitting a continuous album into tracks. It does not matter
for a skit with deliberate two-second pauses between lines.

### 3.4 Bonus: silence needs no encoder

A Layer III frame whose side information and main data are all zero decodes to
**digital silence** — every scalefactor band has zero length, so no spectral
coefficients are read at all. So the filler can be synthesised directly:

```
2 seconds ÷ 24 ms per frame ≈ 83 frames × 384 zeroed bytes
```

This removes the last reason the stitch stage needed FFmpeg. `anullsrc` was the
only remaining call.

---

## 4. Options evaluated

### Rejected: `copy /b` (native cmd)

```cmd
copy /b file1.mp3+file2.mp3+file3.mp3 out.mp3
```

The classic answer, and genuinely native. Rejected on two counts. It does
nothing about §3.1 or §3.2 — tags and stale seek headers all end up inline. And
the command line is capped near 8191 characters: 153 files at these path lengths
overflows it comfortably. Chunking around the limit is possible but at that
point you are writing a worse version of the Python backend.

### Rejected: PowerShell stream copy

```powershell
$out = [System.IO.File]::Create("master.mp3")
Get-ChildItem *.mp3 | ForEach-Object {
    $in = [System.IO.File]::OpenRead($_.FullName); $in.CopyTo($out); $in.Close()
}
$out.Close()
```

Avoids the command-line limit, but is otherwise byte-for-byte the same operation
as `shutil.copyfileobj` with the same tag and header problems, in a language the
rest of the pipeline is not written in. Strictly worse than doing it in Python.

### Rejected: `soundfile` / `librosa`

`soundfile` ≥ 0.11 (bundling libsndfile ≥ 1.1.0) can read *and write* MP3, so a
decode-concatenate-re-encode pipeline is real and pip-installable. Rejected
because it **re-encodes**: a generation of lossy quality lost for an operation
that does not require touching the audio at all, plus the CPU cost of decoding
and encoding ~90 minutes of speech. libsndfile also has no AAC, so it fails the
`.m4a` requirement anyway.

### Rejected: `pydub`

The obvious high-level answer (`AudioSegment.from_mp3(...) + other`), and it
does support `.m4a`. But pydub handles only WAV natively and shells out to
FFmpeg for everything else — so it is an FFmpeg dependency *plus* a re-encode
*plus* an extra package. Worst of every option.

### Rejected: WinRT `MediaComposition`

This looked like the right native answer and is worth documenting as a dead end,
because it is where anyone researching "native Windows audio editing" will land
first. `Windows.Media.Editing.MediaComposition` has a `Clips` collection and
`RenderToFileAsync`, `MediaEncodingProfile.CreateMp3()` and `CreateM4a()` both
exist, and it is reachable from Python via PyWinRT
(`pip install winrt-Windows.Media.Editing`).

It does not work for audio-only input:

- `MediaClip.CreateFromFileAsync` is documented as "Creates a video clip from a
  **video** file". `MediaClip` exposes `GetVideoEncodingProperties()` and has no
  audio equivalent. Its other constructors are `CreateFromColor`,
  `CreateFromImageFileAsync`, `CreateFromSurface` — the class is video-centric
  throughout.
- Audio-only files belong in `BackgroundAudioTracks` instead. But background
  tracks are **mixed**, not sequenced: they all play concurrently from the
  composition's start. There is no append semantic. Staggering them with `Delay`
  to fake concatenation would still leave the composition with zero duration,
  because duration comes from `Clips`.

It is the wrong primitive for this job, and no amount of coaxing changes that.

### Documented, not built: Media Foundation directly

The genuine native answer is `IMFSourceReader` reading compressed samples from
each input and `IMFSinkWriter` writing them out sequentially with rebased
timestamps. Windows desktop ships the codecs to make this work in either
direction — the supported-codecs table lists **MP3 as `D/E`** (both MPEG-4 and
MPEG-3 containers) and **AAC-LC as `D/E`** in MPEG-4, so MP3 *and* `.m4a` output
are both natively available without any third-party encoder. `IMFSinkWriter`
also supports passthrough mode when input and output subtypes match, so this can
be a true remux with no re-encode.

Not built because reaching it from Python means hand-rolling COM interop through
`comtypes` — several hundred lines of `IMF*` interface plumbing, careful
`CoInitializeEx`/`MFStartup` lifecycle handling, and manual reference counting —
to replicate what FFmpeg does in one command. It is the correct choice only if a
hard "no external binaries, must support `.m4a`" constraint ever appears. §7
sketches what that would take.

### Kept as default: FFmpeg concat demuxer

```
ffmpeg -y -f concat -safe 0 -i list.txt -c copy out.mp3
```

`-c copy` means no decode and no re-encode; FFmpeg demuxes each input and remuxes
the packets. It is format-agnostic, already proven in this project, and the only
evaluated option that extends to `.m4a` without new code.

Its one hard requirement, stated in the FFmpeg documentation: *"All files must
have the same streams (same codecs, same time base, etc.)"* — exactly the
constraint the pure-Python backend enforces explicitly in §6.

One useful detail: FFmpeg's MP3 *demuxer* recognises and consumes Xing/Info
headers rather than emitting them as packets. So concatenating already-stitched
intermediate files does not accumulate stray seek frames mid-stream. The
original script's batch-and-append design was therefore not corrupting its
output — its only real cost was rewriting the entire master once per batch,
which makes total bytes written grow with the square of the input count.

---

## 5. Decision

| | FFmpeg backend | Pure-Python backend |
|---|---|---|
| External dependency | `ffmpeg` on PATH | none |
| Re-encodes | no | no |
| MP3 | yes | yes |
| `.m4a`, `.wav`, others | yes | **no** |
| Mixed input formats | tolerated (badly — see §6) | refused with a clear error |
| Speed | fast | faster (no process spawn, no parse of unused metadata) |
| Silence generation | `anullsrc` | synthesised zero frames |
| Seek metadata | `-write_xing 1` remux pass | written inline during splice |

Both are implemented behind `ConcatBackend`. `CONCAT_BACKEND = "auto"` prefers
FFmpeg when present — it is the proven, format-agnostic path — and falls back to
the pure-Python splicer when FFmpeg is absent and every input is MP3.

### Measured parity

Both backends were run over the same 10-dialogue input (19 segments) with
FFmpeg 9.0.1, and the outputs compared at three levels:

| Measure | FFmpeg backend | Pure-Python backend |
|---|---|---|
| Audio frames | 3492 | 3492 |
| **Audio frame payload** | **1 340 928 B, SHA-256 `800e45b6…`** | **1 340 928 B, SHA-256 `800e45b6…`** |
| Container size | 1 341 356 B | 1 341 312 B |
| Metadata frame | Xing + LAME (delay/padding set) | `Info` (no delay/padding) |
| ID3v2 header | 44 B (`-id3v2_version 3`) | none |
| Decoded PCM | 83.762 s | 83.808 s |

**The audio is byte-identical** — same frame count, same payload bytes, same
hash. The two files differ only in metadata: FFmpeg writes a 44-byte ID3v2
header and a LAME tag carrying encoder delay/padding, which instructs decoders
to trim ~46 ms from the stream ends. The Python backend writes a plain `Info`
header with no delay/padding, so nothing is trimmed. That accounts for the
entire 46 ms PCM difference; it is a metadata instruction, not a difference in
audio content.

Worth noting for anyone comparing outputs: *decoded PCM equality is the wrong
parity test for MP3.* Two files can hold identical audio frames and still decode
to different sample counts purely because of gapless-playback metadata. Compare
frame payloads instead.

A related measurement that makes §3.3 concrete: the dialogue MP3s carry no Xing
or LAME tag at all, so their frame duration and decoded duration agree exactly
(287 frames = 6888.0 ms = 6888.0 ms of PCM). `short_filler.mp3`, produced by
FFmpeg's `libmp3lame`, holds 86 frames = 2064.0 ms but decodes to 2000.0 ms —
**64 ms trimmed**, being LAME's standard 576-sample encoder delay plus 960
samples of padding. Nine such fillers account for the 576 ms by which the
untrimmed Python splice exceeds the sum of FFmpeg-decoded parts.

### 5.1 The original script, `stitch_aud.py`, deleted (2026-10-05)

`stitch_aud.py` was the original 1494-line script that did both jobs in one
pass: Sarvam TTS generation and FFmpeg stitching. It was split into
`generate_mp3.py` and `stitch_mp3.py` on 2026-09-11 and kept only for reference.
It was deleted on 2026-10-05, for these reasons:

| Reason | Detail |
|---|---|
| Unused | Nothing imported or called it. The only references were two mentions in this document. |
| Broken | It did not compile: a stray non-breaking space (U+00A0) on line 59 raised `SyntaxError`. It had not run since the split. |
| Superseded | Every part of it now lives elsewhere: generation in `generate_mp3.py`, joining in `concat_backends.py`, and every check, filler, mix and loudness feature added since in `stitch_mp3.py` and its modules. |
| Misleading | It kept its own copies of settings (`LANGUAGE_CODE`, `MODEL`, `PACE`, `SPEECH_SAMPLE_RATE`, `OVERWRITE_EXISTING`). They no longer matched `dice-skit.json` (§12), so they could mislead anyone who read it. |
| Known faulty | It joined the 22 050 Hz intro onto 24 000 Hz dialogue by stream copy, producing the mis-timed masters described in §6. |

**Recovery.** It is still available: every snapshot from `backup/v2/` to
`backup/v9/` holds an identical copy. The pre-split behaviour is also recorded
in this document. §6 covers the sample-rate fault, and the original plan for
the split is in the Claude plan file `cosmic-moseying-starfish.md`.

---

## 6. The sample-rate mismatch this study uncovered

`samay-1.mp3` is **22 050 Hz**. Every dialogue file is **24 000 Hz**.

The original `stitch_aud.py` (deleted, §5.1) prepended the intro unconditionally and joined it
with `-c copy`, which means the masters it produced contained a sample-rate
change partway through. This is not strictly illegal — sample rate is a per-frame
property, so the bytes are well-formed — but it is poorly supported in practice:

- Total duration is computed by many players from the first frame's rate and the
  file size, which is wrong for the whole remainder.
- Xing TOC seeking assumes a uniform frame duration; scrubbing lands in the
  wrong place.
- Some hardware decoders and browser `<audio>` implementations do not
  re-initialise on a mid-stream rate change and play the remainder at the wrong
  pitch, or stop at the boundary.

FFmpeg accepts it silently. The pure-Python backend refuses it, because
`format_key()` — `(version, layer, bitrate, sample_rate, channel_mode)` — must
match across all inputs. Padding is deliberately excluded from that key: a
padded and an unpadded frame differ by one byte but interleave freely.

`stitch_mp3.py` now surveys input formats during pre-flight and reports a
mismatch before touching the master, with two concrete remedies:

```
ffmpeg -i samay-1.mp3 -ar 24000 -ac 1 -b:a 128k samay-1-24k.mp3
```

or `INTRO_FILE = None` in `audio_common.py` to drop the intro. Under
`STRICT_MISSING = False` the outliers are dropped automatically and the run
continues.

---

## 7. Mixing `.m4a`, `.aac` and `.mp4` with `.mp3` (implemented)

The pipeline now receives some dialogue as `.m4a`, and `.aac` since. Per §1 none
of the byte-level work transfers, and the constraint is sharper than "m4a needs a
muxer": the output is an **MP3**, and AAC cannot be carried inside an MP3
stream at all. There is no stream-copy path from `.m4a` to `.mp3`. The audio
must be decoded and re-encoded.

So the pipeline gained a **normalization stage** (`concat_backends.normalize_input`)
that runs before stitching:

```
input already in target format?  -> pass through untouched (stream copy)
otherwise                        -> transcode once into _normalized/, cache it
```

The target is a single canonical format, `TARGET_SAMPLE_RATE` / `TARGET_BITRATE`
in `audio_common.py`, set to **24 kHz mono 128 kbps** — the Sarvam TTS format,
which is the bulk of the material. Choosing the majority format means most files
need no transcode and keep the lossless path; only the outliers are re-encoded.
Targeting the `.m4a` files' 44.1 kHz instead would have forced a re-encode of
every MP3, upsampling 24 kHz to 44.1 kHz for no information gain while adding a
fresh generation of lossy loss to all of them.

Measured on the input set as it stood when this section was written — 4
transcodes out of 14 files:

| Input | Source format | Action |
|---|---|---|
| 9 dialogue MP3s | MPEG-2 L3 24 kHz mono 128 k | pass-through, stream copy |
| `10_YUDHISHTHIRA.m4a` | AAC-LC 44.1 kHz mono | transcoded |
| `129_Krishna.m4a` | AAC-LC 44.1 kHz mono | transcoded |
| `131_Krishna.m4a` | AAC-LC 44.1 kHz mono | transcoded |
| `samay-1.mp3` (intro) | MPEG-2 L3 **22.05 kHz** mono | transcoded |

The set has since grown to 18 files including `.aac`; see *The normalization
contract* below for the current measurement and the full rule.

This also dissolved the §6 problem as a side effect. The 22.05 kHz intro is no
longer a special case to be reported and worked around — it is simply another
input that does not match the target, handled by the same code path as the
`.m4a` files. Transcoding preserves duration exactly (77.322 s in, 77.322 s
out), and the stitched master's duration matches the summed segments to 0.0 ms.

Caching is keyed on the source stem with staleness detected by modification
time, so a re-run reuses all four transcodes and does no FFmpeg work at all.

**Normalization requires FFmpeg.** The pure-Python backend can splice but
cannot decode, so an `.m4a` input with no FFmpeg on PATH is reported as a
conversion failure. Under `strict=0` those inputs are skipped and the
conforming MP3s are still stitched natively; under `strict=1` the run aborts.

### The normalization contract

Everything that reaches the splice is in one identical format: **MPEG-2 Layer
III, 24 000 Hz, mono, 128 kbps**. How a file gets there depends on its
container, and the rule divides cleanly in two.

**Non-MP3 containers are always transcoded.** `needs_normalizing()`
short-circuits on the extension before it inspects the file at all:

```python
if path.suffix.lower() != ".mp3":
    return True
```

So `.aac`, `.m4a`, `.mp4`, `.wav`, `.flac`, `.ogg`, `.opus` and `.wma` — every
non-MP3 entry in `INPUT_EXTENSIONS` — is decoded and re-encoded, with no path by
which one reaches the splice untouched. That is not a conservatism: per §1 the
output is an MP3 stream, and AAC cannot be carried inside one.

**MP3s are transcoded only when they do not already match.** The comparison is
`format_key()` — `(version, layer, bitrate, sample_rate, channel_mode)`, which
for the target is `('2', 3, 128000, 24000, 3)` — read from the first real audio
frame, with Xing/Info seek frames skipped (§3.2). Frame padding is excluded
deliberately: padded and unpadded frames differ by one byte but interleave
freely (see `format_key`'s docstring). A conforming MP3 passes through
byte-for-byte on the stream-copy path.

The transcode itself takes only the first audio stream and discards everything
else, which is what makes a video-bearing `.mp4` safe:

```
ffmpeg -y -i SOURCE -map 0:a:0 -vn -map_metadata -1 \
       -ar 24000 -ac 1 -c:a libmp3lame -b:a 128k -write_xing 1 TEMP
```

Measured across the current 18-file input set — 9 transcoded, 9 pass-through:

| Extension | Transcoded | Why |
|---|---|---|
| `.aac` | 1 / 1 | container, always |
| `.m4a` | 5 / 5 | container, always |
| `.mp3` | **3 / 12** | only the three that differ from target |

The split inside `.mp3` is the point: the extension does not decide, the frame
format does.

| File | As delivered | Action |
|---|---|---|
| `1-` … `9-*.mp3` (Sarvam TTS) | MPEG-2 L3 24 kHz mono 128 k | pass-through, stream copy |
| `0-SAMAY.mp3` | 48 kHz, 107 kbps | transcoded |
| `dice.mp3` | 44.1 kHz, 256 kbps | transcoded |
| `samay-1.mp3` | 22.05 kHz | transcoded |
| `11-DURYODHANA.aac` | AAC-LC 44.1 kHz mono | transcoded |
| `10_YUDHISHTHIRA.m4a`, `16_`, `129_`, `131_` | AAC-LC 44.1 kHz mono | transcoded |
| `new-mahabharata-instrumental.m4a` | AAC-LC 44.1 kHz **stereo** | transcoded, downmixed |

After normalization every one of them measures identically:

```
0-SAMAY.mp3                       mp3,24000,1,128000
10_YUDHISHTHIRA.mp3               mp3,24000,1,128000
11-DURYODHANA.mp3   (from .aac)   mp3,24000,1,128000
new-mahabharata-instrumental.mp3  mp3,24000,1,128000
dice.mp3                          mp3,24000,1,128000
```

Three independent mechanisms enforce this rather than one:

1. **Post-transcode assertion.** `transcode_to_target()` re-runs
   `needs_normalizing()` on its own output and raises `NormalizationError` if
   the result still misses the target, so a transcode cannot silently fail to
   land.
2. **Pre-splice survey.** `survey_formats()` re-reads the frame headers of
   everything about to be joined, independently of the normalization
   bookkeeping — `Format : MPEG-2 Layer 3 24000 Hz mono 128 kbps (consistent
   across 18 file(s))`. Under `strict=1` a mismatch aborts before the master is
   written; under `strict=0` the minority formats are dropped.
3. **Filler revalidation.** `ensure_filler()` deletes and regenerates any
   filler that is invalid *or* in the wrong format, so a `short_filler.mp3`
   left over from a run with a different target cannot corrupt a later splice.

Two limits worth recording:

- **Transcoding is lossy, once.** Non-MP3 sources and off-format MP3s carry one
  extra generation of MP3 loss that the pass-through TTS files do not. The
  `work/normalized/` cache is keyed on source stem with staleness by
  modification time, so the cost is paid once per file, not once per run.
- **VBR MP3s are judged on their first frame.** `format_key()` reads a single
  frame header. A variable-bitrate MP3 whose first frame happens to be 24 kHz
  mono 128 kbps is classed as conforming and passed through, even though later
  frames differ. Nothing in the current set is VBR and the FFmpeg backend
  tolerates it, but this is the one gap in the guarantee — and the first place
  to look if a newly added file produces an odd master duration.

### Filename matching

The `.m4a` files arrived as `10_YUDHISHTHIRA.m4a` and `129_Krishna.m4a` —
underscore separator and mixed case, against the MP3 convention's
`10-YUDHISHTHIRA.mp3` and the CSV's uppercase `KRISHNA`. Rather than require
renaming, `find_dialogue_file()` matches on the **leading sequence number
alone**, tolerating separator, case and extension. Deviations are reported so
drift stays visible:

```
MATCHED BY SEQUENCE NUMBER (4) - filename differs from the CSV convention:
    129-KRISHNA.mp3              <- 129_Krishna.m4a
```

Matching on the number alone is how the file is **found**. It is not the whole
test of whether the file is **accepted**. Since 2026-10-04 the mandatory checks
(§9.6) also require that:

- the character name in the filename matches the row's `character_name`, after
  both are casefolded and stripped to letters and digits. This tolerates
  `_`/`-`, case and spaces: `129_Krishna.m4a` matches `KRISHNA`, and
  `0153_Voice_of_Time.m4a` matches `VOICE OF TIME`. `4-DURYODHANA.mp3` on a
  SHAKUNI row does not match, and the run aborts.
- the filename carries a character name at all. A bare `4.mp3` aborts.

`MATCHED BY SEQUENCE NUMBER` above is purely informational. On the current data
it lists 5 rows whose names differ only in separator, case or extension. None of
them trip the character check.

**Two files with the same take number in one directory now abort the run**
(`TAKE-ID COLLISION`). Before 2026-10-04 the newest file silently won. Padding
does not make numbers distinct: `4_A.mp3`, `04_B.mp3` and `0004_C.mp3` are all
take 4. One file per take in each directory is the rule. Retire unwanted takes
to `human_gen/retired/`, which discovery never looks inside.

Sequence numbers start at **0**, which is the opening `SAMAY` line. Nothing
special-cases it — `0-SAMAY.mp3` is found by the same numeric match as any
other row.

### Two source directories

Audio arrives from two places, and the master folder is **not** one of them —
it holds only the finished master, nothing else. Folder paths below are those
of the layout adopted on 2026-10-08 (§14); before that all five sat at the top
of the project, and the master folder was `dialogues-mp3/`.

| Directory | Role | Written by |
|---|---|---|
| `input/human_gen/` | Source, precedence 1. Human-recorded takes, plus named filler audio | `fetch_drive.py` |
| `in-out/machine_gen/` | Source, precedence 2. Sarvam TTS output | `generate_mp3.py` |
| `output/work/` | Derived: generated fillers, and the transcode, loudness and mix caches | the pipeline |
| `output/master/` | **Output only** — `master_dialogues.mp3` and nothing else | `stitch_mp3.py` |

Keeping the output directory free of inputs means it can be deleted and rebuilt
without losing source material, and there is never any doubt about which files
are products and which are ingredients.

**Directory precedence beats modification time.** `human_gen/` wins outright: a
real recording is never displaced by synthesised speech, however new the
synthesised file is. Modification time only breaks ties *within* one directory.
Both rules are reported:

```
MULTIPLE FILES PER SEQUENCE (1) - directory precedence, then newest:
    using 1-REC.mp3  (human_gen)
      ignoring 1-DURYODHANA.mp3  (machine_gen)
```

The same take in **both** directories is the designed fallback, not a
collision. It is reported in this block but never aborts. Only two files for one
take in the **same** directory abort the run (§9.6, check 4). The tie-break by
"newest" in the block's heading therefore applies only to runs that are about to
abort anyway.

The ordering encodes an editorial judgement, not a technical one: a real voice
beats a synthesised one. Swap the tuple returned by
`audio_common.search_directories()` to change it.

Named `filler_before` audio is looked up across both the same way, so a sound
effect delivered into `human_gen/` is found without being moved.

Rows that fell through to the lower-precedence source are called out, because
those are the lines still waiting on a real recording:

```
FELL BACK TO MACHINE_GEN (10) - not in human_gen:
    0-SAMAY.mp3
    1-DURYODHANA.mp3
    ...
```

A sequence number present in neither directory produces a warning in
`log/run<timestamp>.log` rather than a silent gap — see §0.10.

## 8. Per-row fillers

The gap around each line is driven by two CSV columns — **`filler_before`** and
**`filler_after`** — rather than by a single global filler. Each row contributes
its leading filler, its dialogue, and optionally a trailing filler:

```
filler_before(row 0)   new-mahabharata-instrumental.m4a   21.096 s
dialogue row 0         0-SAMAY.mp3                        61.512 s
filler_after(row 0)    long_filler.mp3                    10.008 s
filler_before(row 1)   empty -> short_filler.mp3           2.064 s
dialogue row 1         1-DURYODHANA.mp3                    6.888 s
                       row 1 names no filler_after -> nothing
...
```

**The opening music is just row 0's `filler_before`** — the earlier dedicated
intro setting (`INTRO_FILE`) was removed rather than kept alongside it.

### The two columns have deliberately opposite empty-cell rules

| Cell | `filler_before` | `filler_after` |
|---|---|---|
| empty | `short_filler.mp3` (2 s default) | **nothing appended** |
| `"N"` | **nothing** — no leading gap | nothing (same as empty) |
| a filename | that file | that file |
| a filename that is absent | falls back to `short_filler.mp3`, reported | **nothing appended**, reported |

The asymmetry is the whole design. A default *leading* gap is what separates
lines, so an empty cell should supply one. A default *trailing* gap would put
silence after every line including the last, so an empty cell must supply
nothing — which is also why a named-but-missing `filler_after` appends nothing
rather than degrading to silence: the row asked for one specific sound, and
substituting a gap would invent timing the CSV never called for.

`"N"` exists because an empty `filler_before` cannot mean "no gap" — that
spelling is already taken by the 2-second default. It is compared
case-insensitively against `NO_FILLER_TOKENS` (`n`, `no`, `none`); no real
filler can collide, since filler cells name files and a filename carries an
extension. Its effect is to butt a row's audio directly against whatever
precedes it:

```
Default (short_filler.mp3) : 5 row(s)
Named in filler_before      : 1 row(s)
Suppressed with "N"         : 9 row(s)
Named in filler_after       : 1 row(s)
No trailing filler          : 14 row(s)
```

Either column may name a **generated** filler (`short_filler.mp3`,
`long_filler.mp3`) and it will be synthesised on demand — `GENERATED_FILLERS`
maps the filename to its duration, and the stitch stage creates whichever ones a
run actually references before resolving row fillers. `find_named_audio()`
therefore searches `work/` last, after the real source directories, so a
recording of the same name still wins.

Three consequences worth noting:

- **Filler audio is normalized like anything else.** The opening instrumental
  is 44.1 kHz *stereo* AAC, so it is decoded, downmixed to mono and resampled
  to 24 kHz by the same path as the `.m4a` dialogue. `0-SAMAY.mp3` itself is
  48 kHz and gets resampled too.
- **Each distinct source is converted once.** `short_filler.mp3` is referenced
  by most rows but appears once in the transcode map, so a 150-row run does not
  re-convert it 150 times.
- **A named leading filler that is absent degrades to the default gap** and is
  reported, rather than failing the run. Under `strict=1` it still counts as a
  problem and aborts; under `strict=0` the run continues with silence in its
  place:

```
NAMED FILLER NOT FOUND (1) - falling back to short_filler.mp3:
    row 19 / 17-YUDHISHTHIRA.mp3       wanted dice.mp3
```

Filler audio lives in the same directory as the dialogue but carries no leading
sequence number, which is what keeps it out of the dialogue set: both discovery
paths require a number, so `dice.mp3` and
`new-mahabharata-instrumental.m4a` are never mistaken for lines.

Measured on the current 15-row set: 31 segments (15 dialogue + 15 leading
fillers + 1 trailing), and the master's duration matches the summed segments to
**0.0 ms**. Suppressing 9 rows' leading fillers with `"N"` drops it to 22
segments and 194.323 s, against 212.899 s — the 18.58 s difference being the 9
removed 2-second gaps.

### If the output format ever needs to be `.m4a` too

| Option | Dependency | Re-encodes | Assessment |
|---|---|---|---|
| **FFmpeg concat demuxer** | `ffmpeg` | no | `FfmpegBackend.supports()` already returns True for any format; set `OUTPUT_FORMAT = "m4a"`. Requires identical codec parameters across inputs. **Recommended.** |
| **MP4Box `-cat`** | GPAC | no | Purpose-built for MP4 concatenation, tolerates some mismatches FFmpeg refuses. A second external binary for a narrow gain. |
| **Media Foundation** | none (ships with Windows) | optional | `IMFSourceReader` → `IMFSinkWriter` with `MFCreateMPEG4MediaSink`; AAC-LC encoder is native (`D/E`). Costs significant `comtypes` COM plumbing. |
| **Pure Python MP4 muxer** | none | no | No mature library rewrites sample tables across concatenated inputs. Not recommended. |

---

## 9. Surviving script revisions (implemented)

> **Specified 2026-09-12, implemented 2026-10-04.** The checks in §9.6–§9.9 are
> live in `stitch_mp3.py` (`mandatory_checks()`, `report_dry_run()`) and
> `audio_common.py` (`character_token()`, the `collisions` return of
> `find_dialogue_file()`, the `dry_run=` flag). §9.11 records how each was
> verified. The four numbering rules in §9.4 remain process: the code enforces
> their detectable symptoms, not the rules themselves.

### 9.1 The production problem

In a live production the order is: script written and reviewed, dialogues
numbered, actors record their lines separately, recordings land in `human_gen/`
named `<sequence_number>-<CHARACTER>.<ext>`.

Then the script is revised. Lines are inserted, lines are cut. The recordings
already made must not be lost, and must not be silently attached to the wrong
dialogue.

### 9.2 Root cause: one field doing two jobs

`sequence_number` is simultaneously the **identity** of a recorded take and its
**position** in the running order. A revision legitimately changes position.
Renumbering to reflect that re-points every existing recording at different text.

The pipeline cannot notice. Matching is by number alone — `find_dialogue_file()`
never consults `character_name`. Measured:

```
CSV row says character : SHAKUNI
expected filename      : 4-SHAKUNI.mp3
file actually on disk  : 4-DURYODHANA.mp3
RESOLVED TO            : 4-DURYODHANA.mp3     <- accepted silently
```

This is worse than a missing file. A missing file warns; this produces a
complete, plausible-sounding master with the wrong actor on the wrong line.

### 9.3 What the current code already guarantees (measured)

Three of the four rules in §9.4 need no code change, because the pipeline already
decouples order from the number:

| Property | Status | Evidence |
|---|---|---|
| Running order is **CSV row order**, not numeric | already true | rows numbered 7, 3, 9, 1 stitched in that order |
| Gaps and non-monotonic numbers tolerated | already true | same run; `sequence_number` is only a matching key |
| Zero-padding is transparent | already true | `0007-SHAKUNI.mp3` resolves to take 7 — the matcher int-converts leading digits |
| An archive subfolder is invisible to discovery | already true | `iter_candidate_files` skips non-files; `_`-prefixed names are skipped too |
| Renumbering is prevented or detected | detected since 2026-10-04 | the character-name check in §9.6 aborts on it; see §9.11 |

Every `sort`/`sorted` in `stitch_mp3.py` and `audio_common.py` was audited: they
cover the duplicate-sequence report, directory tallies, format-survey groups,
filler names, the directory listing, and mtime tie-breaking among files sharing
one number. **None affects row ordering.**

**One caveat — this holds in CSV-driven mode only.** When `dialogues_new.csv` is
absent the pipeline falls back to `resolve_from_glob()` and reports
`Order : numeric by leading sequence number`. Under numeric ordering, gapped
append-only IDs would play in ID order rather than script order. The immutable-ID
scheme therefore *requires* the CSV, which is why its absence becomes a mandatory
abort (§9.6).

### 9.4 The number becomes an immutable take ID (process)

Four rules, process rather than code. The code does not enforce these rules
directly. It enforces their detectable symptoms (§9.6):

1. **A number, once assigned, is never changed and never reused.** It identifies
   a take for the life of the production.
2. **Running order is row order.** Reorder by moving CSV rows; never by
   renumbering.
3. **New dialogue gets the next number above the high-water mark**, regardless of
   where its row sits. A line inserted between takes 40 and 41 becomes take 178
   in row 41. Looks odd, behaves correctly.
4. **Removed dialogue: delete the CSV row, leave the recording on disk.**
   Discovery is CSV-driven, so an unreferenced file is never used. Re-inserting
   the line later with its original number resurrects the take.

**High-water mark without extra bookkeeping:** `max` of the numbers in the CSV and
the numbers on filenames in `human_gen/` and the archive, plus one. Retired takes
keep their numbers reserved because their files still exist.

**Archiving retired takes:** move them to `human_gen/retired/`. Verified
invisible to discovery. Zero-padding to four digits is also worth adopting — it
sorts naturally in File Explorer and, as §9.3 records, changes nothing about
matching.

**Revising the script, step by step:**

1. Find the high-water mark. This one-liner checks the CSV, `input/human_gen/`
   (including `retired/`) and `in-out/machine_gen/` (folder layout, §14):

   ```powershell
   python -c "import csv,re,pathlib as p; n=[int(r['sequence_number']) for r in csv.DictReader(open('input/dialogues_new.csv',encoding='utf-8-sig'))]; n+=[int(m.group()) for f in [*p.Path('input/human_gen').rglob('*'),*p.Path('in-out/machine_gen').glob('*')] if (m:=re.match(r'\d+',f.name))]; print('next id:',max(n)+1)"
   ```

2. **Insert a line:** add the CSV row where it belongs in the running order,
   with the next ID. Record it as `<id>-<CHARACTER>.<ext>`.
3. **Cut a line:** delete its CSV row. Leave the file where it is, or move it to
   `human_gen/retired/`.
4. **Move a line:** move its CSV row. Never edit `sequence_number`.
5. **Re-record a line:** put the new take in `human_gen/` and move the old one to
   `human_gen/retired/`. Two takes for one ID in the same directory abort the run
   (`TAKE-ID COLLISION`).
6. Run `python stitch_mp3.py dry_run=1`. A `CHARACTER MISMATCH` here almost
   always means an ID was edited by mistake. Undo the edit rather than renaming
   the file.

### 9.5 Numbering schemes that fail silently

Both are the intuitive first idea. Both were measured, and both fail without a
usable error:

| Scheme | What actually happens |
|---|---|
| Fractional inserts (`5.1`, `5.2`) | CSV side: `int("5.1")` raises, so the row resolves to **no file** and is reported only as "missing". File side: `5.1-X.mp3` reads as sequence **5**, colliding with the real take 5. Broken in both directions. |
| Alphanumeric IDs (`D0042-SHAKUNI.mp3`) | `sequence_of` returns `None`, so the file is **invisible**. CSV `D0042` raises `ValueError` → silent no-match. |

`sequence_number` must be an integer. Non-integer identifiers are not available
without a code change.

### 9.6 Four mandatory pre-flight checks (implemented)

Checks 1, 3 and 4 **abort the run regardless of `strict`**. There is no
override flag; those rules are absolute. Check 2 aborts under `strict=1` only,
as changed on 2026-10-07 (below).

| # | Check | Rationale |
|---|---|---|
| 1 | `dialogues_new.csv` absent | The glob fallback orders numerically, breaking the ID scheme (§9.3). Keep the glob path for ad-hoc runs, but it must not be reachable by accident when a manifest is expected. |
| 2 | Character token ≠ the row's `character_name` | The detectable symptom of renumbering (§9.2). Under `strict=0`, listed and allowed. |
| 3 | Two files mapping to one take-id **within one directory** | Ambiguity. `004_YUDHISTHIRA.mp3`, `04_SHAKUNI.mp3`, `0004_KARNA.mp3` and `4_ARJUNA.mp3` all resolve to take 4. |
| 4 | A file with no character token at all (a bare `4.mp3`) | Unverifiable against check 2, therefore invalid. |

**Decision, 2026-10-07: check 2 is allowed under `strict=0`.** The original
rule (decided 2026-09-12) made a character mismatch abort every build, with no
override. Real recordings showed that most mismatches are not renumbering at
all. In the production files, 9 lines from `human_gen/` failed check 2:

| Files | Why they fail check 2 | Real fault? |
|---|---|---|
| `23_24_Shakuni.m4a`, `41_42_Shakuni.m4a`, `63_64_Shakuni.m4a`, `51_52_53_Shakuni.m4a` | One recording covers several takes. The take number is read from the leading digits only (`23`), and the rest (`24_Shakuni`) becomes the character token `24shakuni`. | No |
| `143_`, `144_`, `146_`, `148_`, `149_Dhritrashtra.aac` | Spelled `Dhritrashtra` in the filename but `DHRITARASHTRA` in the CSV | No |

Blocking these forced the files to be renamed before any build, even a
draft one. So check 2 now follows the same rule as missing audio:

- **`strict=1`** (the default, for the final build): aborts, as before.
- **`strict=0`** (drafts): every mismatch is still listed, one line per row,
  under `CHARACTER MISMATCH (allowed, strict is off)`. An `ALLOWED` line follows,
  saying the files are used as they are. Each mismatch is also counted in the
  run's problem total and written to the run log as a `WARNING:`.
- **`dry_run=1`** treats it as allowed, so a check that a `strict=0` build would
  pass does not exit 1.

This weakens the protection of §9.2 under `strict=0`: a genuinely renumbered
file would now be built into a draft master, with a warning but without
stopping. The final build should therefore always be `strict=1`, and the
`CHARACTER MISMATCH` list should be read before then. Checks 1, 3 and 4 are
unchanged: those faults have no harmless explanation.

Two follow-ups are worth considering, but neither is built:

- Recognise a multi-take filename (`23_24_Shakuni`) and strip every leading
  number.
- Accept near-miss spellings of a character name.

Verified on a copy of the project with the 9 files recreated by name:

| Run | Result |
|---|---|
| `strict=0` | exit 0; all 9 listed as allowed; master built from 24 lines; 139 problems counted, the 130 missing lines plus the 9 mismatches |
| `strict=1` (default) | exit 1, `ABORTED - MANDATORY CHECKS FAILED`, `character mismatch : 9` |
| `dry_run=1` | exit 0, `Mandatory check failures : 0` |
| `strict=0` plus a bare `5.mp3` and a second file for take 23 | exit 1; `no character in filename` and `take-id collision` still abort |

Measured on check 3's example before the change: the pipeline sorted the four
colliding files by mtime, silently picked `4_ARJUNA.mp3`, and listed the rest
under `MULTIPLE FILES PER SEQUENCE` without aborting. It now aborts with a
`TAKE-ID COLLISION` block; `MULTIPLE FILES PER SEQUENCE` is still printed as
before, for information.

Check 2 must compare the **character token only**, normalized — not whole
filenames. Measured on the current 15 resolved rows:

| Predicate | Rows flagged |
|---|---|
| Existing `renamed` test (`path.name != expected`) | **5** — all benign |
| Normalized character token only | **0** |

The narrowed form correctly accepts `129_Krishna.m4a` for CSV `KRISHNA`,
`10_YUDHISHTHIRA.m4a` and `11-DURYODHANA.aac`, while still rejecting
`4-DURYODHANA.mp3` on a SHAKUNI row. Strip leading digits and one separator from
the stem, remove non-alphanumerics from both sides, casefold, compare. Note some
`character_name` values contain spaces (`VOICE OF TIME`), which the normalization
must absorb.

### 9.7 When each class of check fails (implemented)

| Mode | `strict` | Mandatory checks (§9.6) | All other checks |
|---|---|---|---|
| `dry_run` | **ignored — carries no significance** | report | report |
| normal, `strict=1` | — | abort | abort |
| normal, `strict=0` | — | **abort** | warn, proceed |

The bottom-right cell is existing behaviour and needs no change. "Other checks"
means the pre-existing ones: missing, corrupt or format-mismatched audio, and a
named filler that cannot be found.

**Every row is parsed and every exception collected into one consolidated report
before anything aborts.** Never fail on first detection — that forces a
fix-run-fix-run loop across a 154-row manifest. This is already the documented
intent of `STRICT_MISSING` and how `preflight()` behaves; the 139 currently
missing rows emerge as a single block. The new checks must join that accumulation.

Report shape should mirror the existing blocks — `MISSING (139)`,
`MATCHED BY SEQUENCE NUMBER (5)`, `MULTIPLE FILES PER SEQUENCE` — one block per
exception class listing every affected row, then a single fatal summary line. The
run log is the audit trail the team reads, so consistency matters more than
novelty.

### 9.8 `dry_run` runs at full depth (implemented)

`dry_run` **must include the expensive normalization pass**, so that format and
corruption faults are caught and not merely structural ones. It performs
everything except `build_sequence()`, stitching and finalizing.

The cost is acceptable because the transcode work is **not wasted**:
`work/normalized/` is cached with staleness by modification time, and the
subsequent real run reuses it — observed as `Reused from cache : 7`. A full-depth
`dry_run` costs the production run nothing overall; it moves the work earlier.

Two consequences that must be documented for operators:

- **`dry_run` requires FFmpeg.** Normalization cannot run without it, so
  `dry_run` is not usable on the Python-only machine that §0.1 otherwise
  supports. On such a machine every non-conforming input reports as a conversion
  failure — truthful, but easily mistaken for a manifest fault.
- **`dry_run` is not side-effect-free, despite the name.** At full depth it
  populates `work/normalized/` and synthesises any referenced generated filler
  (`short_filler.mp3`, `long_filler.mp3`) through `ensure_filler()`. Only the
  master is guaranteed untouched.

**Exit status** follows the convention already in the file rather than being a new
decision: the entry point calls `sys.exit(code)` with `main()`'s return value, and
`main()` already returns 1 on every abort path and 0 on success. `dry_run` returns
non-zero when mandatory exceptions exist, which makes validate-then-build
chainable:

```powershell
python stitch_mp3.py dry_run=1
if ($LASTEXITCODE -eq 0) { python stitch_mp3.py strict=0 }
```

Returning 0 while reporting fatal exceptions would contradict every other path
and would let a wrapper proceed to build the very master these checks exist to
prevent.

**As built, `dry_run` exits 1 only for mandatory faults.** Missing, corrupt and
off-format audio still exit 0. Those are the faults `strict=0` is allowed to
wave through. While 139 rows are unrecorded, exiting 1 on them would mean the
chain above never reached the build. A wrapper that should also stop on missing
audio can run the build with `strict=1`, which aborts on them itself. Changing
this is one line, the `return` in `report_dry_run()`.

### 9.9 Groundwork the checks needed (implemented)

Two pieces, both prerequisites rather than refinements:

- **A `strict`-independent accumulator.** Everything collected today funnels into
  one `problems` counter gated by `if problems and strict:`, so `strict=0` waves
  it all through. The mandatory four need a separate `fatal` list, reported
  alongside the existing blocks but consulted independently of `strict`.
- **Split `alternatives` into intra- and cross-directory matches.**
  `find_dialogue_file()` currently fills one list from two opposite situations:
  `matches[1:]` of the *winning* directory (genuine ambiguity, should abort) and
  *every* match in lower-precedence directories (the designed
  `human_gen` → `machine_gen` fallback, must **not** abort). Both were measured
  producing a non-empty `alternatives`, so `duplicated` cannot be the gate for
  check 3 — the correct predicate is "more than one match within a single
  directory", which the code does not currently surface.

Both existing report lists — `renamed` and `duplicated` — are deliberately
permissive because they were built to inform, not to enforce. Neither is used as
a failure gate; both are still printed, unchanged.

How it was built:

- `mandatory_checks()` returns a `fatal` list of `(title, count)`. It never
  touches `problems`. `main()` acts on it only after normalization and the
  format survey, so one run reports every fault of both classes.
- `find_dialogue_file()` now returns a fourth value, `collisions`: the other
  matches in the **winning** directory only. `alternatives` keeps its old
  meaning, so the existing report is unchanged.
- The character token comes from `audio_common.character_token()`: strip the
  leading digits, then `normalize_character()` (casefold, drop everything but
  `a-z0-9`). The separator needs no special case because normalization drops
  it. `safe_filename()` was not reused: it maps characters to `_`, while this
  comparison has to remove them.
- When the CSV is absent the glob scan still runs, so the rest of the report
  appears, but the manifest check makes the run abort. The glob order is never
  stitched. Rows found by glob carry no CSV record, so check 2 cannot apply to
  them; checks 3 and 4 still do.
- `dry_run=1` (also `dry=1`, `--dry_run=1`) follows `fetch_drive.py`'s flag
  shape. Under `dry_run` the "Strict mode" banner is replaced by a "DRY RUN"
  line. The run ends in a `DRY RUN COMPLETE` block that counts both classes and
  says what a real run would do.
- Exit codes: a normal run returns 1 on a mandatory failure, as on every other
  abort path. `dry_run` returns 1 exactly when a mandatory check failed. It
  returns 0 when only ordinary problems were found, because those are the ones
  `strict=0` is allowed to wave through. A wrapper that also wants to stop on
  missing audio should build with `strict=1`, which aborts on them itself.

### 9.10 What remains undetectable

**ID reuse across revisions cannot be detected from a single run.** If a number is
retired and later assigned to a different line, no check in §9.6 will see it —
detecting that needs a persisted ledger of assigned IDs, which is a larger piece
of work than a pre-flight check. Rule 1 of §9.4 remains process discipline, not an
enforced invariant.

### 9.11 Verification (measured, 2026-10-04)

On the real project (154 rows, 15 resolved):

| Run | Result |
|---|---|
| `dry_run=1` | exit 0; `MANDATORY CHECKS` passed with **0** false positives; 139 missing reported; master's mtime and size unchanged; 7 normalized files reused from cache |

On a scratch copy with faults planted:

| Planted fault | Reported as | Aborted under `strict=0`? |
|---|---|---|
| `4-SHAKUNI.mp3` renamed `4-DURYODHANA.mp3` | `CHARACTER MISMATCH` row 6, CSV SHAKUNI | yes |
| `5-DURYODHANA.mp3` renamed `5.mp3` | `NO CHARACTER IN FILENAME` | yes |
| `0129-krishna.m4a` added beside `129_Krishna.m4a` in `human_gen/` | `TAKE-ID COLLISION`, take 129 | yes |
| `8-SHAKUNI.mp3` in both `human_gen/` and `machine_gen/` | not flagged — designed fallback | no (correct) |
| `dialogues_new.csv` removed | `MANIFEST MISSING` | yes |

All three file faults appeared in **one** report, alongside the 139 missing rows,
and each was written to the run log as a `WARNING:`. `dry_run=1 strict=0` on the
same faults exited 1. With the faults removed, a `strict=0` build exited 0 and
produced the same 15-dialogue, 31-segment master as before.

`VOICE OF TIME` normalizes to `voiceoftime`, so `153-VOICE OF TIME.mp3` and
`0153_Voice_of_Time.m4a` both match its row.

---

## 10. Background score under a line (implemented)

> **Specified and implemented 2026-10-05.** The mixing logic is in
> `background_score.py`. `stitch_mp3.py` calls it from
> `apply_background_scores()`, and the settings are the `SCORE_*` constants in
> `audio_common.py`. §10.9 records how it was verified. `dialogues_new.csv`
> carries both columns, empty on every row, so the master is unchanged until a
> row names a score.

### 10.1 The requirement

Overlay a background track (music, ambience) on a row's dialogue, so the two play
together. The script sets which one is loud at each moment, and the other is
turned down but still faintly audible. The mixed result takes that row's dialogue
slot in the master.

### 10.2 Feasibility (measured)

FFmpeg can do it in one pass with filters it already ships:

| Step | Filter |
|---|---|
| Bring both to the target format | `aresample=24000`, `aformat=channel_layouts=mono` |
| Start the second track late (staggered start only) | `adelay` |
| Volume over time per track | `volume='<expression in t>':eval=frame` |
| Sum, keeping the longer length | `amix=inputs=2:duration=longest:normalize=0` |
| Prevent clipping | `alimiter` |

Prototype: `1-DURYODHANA.mp3` (6.9 s, 24 kHz mono) mixed with
`new-mahabharata-instrumental.m4a` (21.0 s, 44.1 kHz stereo). It took under a
second, and the output was 21.03 s at 24 kHz mono 128 kbps, which is already the
target format. Loudness measured in each window of the cue string followed the
schedule. With `b-2s-m-3s-b-2s` and the dialogue delayed by 2 s, the output was
21.03 s, matching `max(21.0, 2 + 6.9)`.

The together-start form (§10.4) was prototyped as `t-m-5s-b-10s-m-2s` on the
same two files. The output was 21.03 s, `max(6.9, 21.0)` with no delay. Per-window
loudness:

| Window | Dialogue alone | Music alone | Mix | Prominent per cue |
|---|---|---|---|---|
| 0.3–1.8 s | −21.3 dB | −18.9 dB | −20.7 dB | dialogue |
| 2.5–4.5 s | −21.9 dB | −19.9 dB | −21.6 dB | dialogue |
| 5.5–6.7 s | −23.1 dB | −19.7 dB | −17.1 dB | music |
| 9–13 s | — | −20.7 dB | −18.2 dB | music |
| 16–20 s | — | −20.3 dB | −17.4 dB | dialogue, but it ended at 6.9 s, so the music stays up (§10.7) |

While the dialogue leads, the mix tracks the dialogue's level, so the music is
underneath. From 5 s the mix rises by about 3 dB as the music takes over.

The prototype also showed why loudness matching is needed (§10.5). The music
alone measured about 3 dB louder once stereo was folded to mono.

**FFmpeg is required.** Mixing means decoding, and the pure-Python splicer
(§3) can only join complete frames. It cannot sum two signals.

### 10.3 CSV columns

| Column | Content | Empty means |
|---|---|---|
| `background_score` | Filename of the background track, looked up in the same directories as fillers (`human_gen/`, `machine_gen/`, then `work/`) | No mix for this row |
| `score_cues` | Cue string, §10.4 | Required whenever `background_score` is set |

Setting one column without the other is a cue-string fault (§10.7).

### 10.4 Cue string

```
b-2s-m-10s-b-5s-m-2s       staggered start
t-m-5s-b-10s-m-2s          together start
t-m                        together; the line leads to the end
t-m-loop                   together; a short background repeats to the end of the line
```

Letters and durations alternate, separated by `-`.

- `m` = the main dialogue track; `b` = the background track.
- Durations are in seconds, with a trailing `s`, and decimals are allowed (`2.5s`).
- **The letters must alternate.** `m` followed by `m` is an error, not a merge.
- An optional leading `t` selects the together start. It may appear only once,
  first, and must be followed by at least one segment.
- **The last segment's duration is optional** (added 2026-10-07): `t-m` means
  "the line leads until the end". A staggered string still needs a second
  segment, because its first segment is solo (`b-2s-m` is valid; a bare `m` is
  not).
- An optional trailing **`-loop`** (added 2026-10-07) repeats the background
  until the line ends; see "Looping the background" below. It may appear only
  once, last.

There are two start modes. The leading `t` decides which, and everything after
the first segment works the same way in both.

#### Staggered start (no `t`)

**The first segment is solo.** The first letter's track plays alone, and the
other track is silent, not merely quiet. When that segment ends, the other track
**starts from its own beginning**. From then on both play, and each later letter
names the **prominent** track: full volume, with the other turned down.

Worked example, `b-2s-m-10s-b-5s-m-2s`:

| Time | Playing | Prominent |
|---|---|---|
| 0–2 s | background only | — |
| 2–12 s | both; dialogue starts from its beginning at 2 s | dialogue |
| 12–17 s | both | background |
| 17 s to the end | both | dialogue |

`m-2s-b-10s-m-5s-b-2s` is the mirror image: 2 s of dialogue alone, then the
music starts from its beginning and is prominent for 10 s, and so on.

#### Together start (`t-…`)

**Both tracks start at 0 s, from their own beginnings.** There is no solo
segment. Every letter, including the first, names the **prominent** track, with
the other turned down but audible from the first instant.

Worked example, `t-m-5s-b-10s-m-2s`:

| Time | Playing | Prominent |
|---|---|---|
| 0–5 s | both, from 0 s | dialogue |
| 5–15 s | both | background |
| 15 s to the end | both | dialogue |

`t-b-…` is equally valid: the music leads from the first instant, with the line
underneath.

The difference from the staggered start lies only in the first segment. Compare
`m-5s-b-…`, where the music is *silent* for 5 s and then begins from its start,
with `t-m-5s-b-…`, where the music is *quiet* for 5 s and is already 5 s in when
it takes over.

**In both modes the last segment runs to the end of the mix.** Its duration is
accepted but not enforced, because the mix length is set by the tracks (§10.5).
In the together example, "m for 2 s" therefore means "m from 15 s to the end".
Writing the last segment with no duration (`…-m`) says this explicitly. It
also tells the pipeline that a track running out inside that segment is
intended, so no `CUE STRING LONGER THAN THE AUDIO` warning is logged for it.
The audio is identical either way: `t-m` and `t-m-1s` produce the same mix.

#### Looping the background (`-loop`)

Added 2026-10-07: both tracks start together and the line leads. **The mix is
always exactly as long as the line**, whatever the background's length:

```
t-m-loop
```

| Background vs line | What happens with `t-m-loop` |
|---|---|
| **Shorter** | Repeats from its own start until the line ends |
| **Longer** | **Cut where the line ends**, fading out over `ramp_seconds` (0.3 s) up to the cut so it doesn't stop abruptly |

**The cut applies to the exact string `t-m-loop` only** (case and surrounding
spaces ignored). This was decided on 2026-10-07, after an earlier version
let a longer background play on. Every other string ending in `-loop`, such as
`t-b-loop`, `t-m-5s-b-loop` or `b-2s-m-loop`, repeats a *shorter* background
but leaves a *longer* one to play to its own end. There it comes up once the
line ends (§10.7), as without `-loop`.

Rules:

- **What repeats:** the background only, from its own start, without a gap.
  At each seam the background fades out and back in over `ramp_seconds`
  (0.3 s), so music that doesn't loop cleanly does not click.
- **Mix length:** a repeated background is cut where the line ends, so it
  never outlasts it. With `t-m-loop` that also holds for a background that
  was longer to begin with.
- **Staggered starts:** `b-2s-m-loop` and `m-2s-b-loop` also work. The
  background loops from its own start, wherever that falls.
- **Too many repeats:** more than 10 repeats (`LOOP_WARN_REPEATS`) logs a
  warning, since a 2 s sound under a minute-long line usually means the wrong
  file was named. The mix is still made.
- **The report** says what actually happened to the background, e.g.
  `together: 0-ends dialogue up; background repeated 2 times to the end of the line`
  or `...; background cut at the end of the line`. A background that needed
  neither gets no note.

### 10.5 Levels, ramps, length and loudness

| Property | Decision |
|---|---|
| "Faded" | **Quieter, never silent.** The turned-down track must still be faintly audible. `background_score.duck_db` in `dice-skit.json`, default −14. Tune by ear. |
| Switch | A short linear ramp at every boundary, `SCORE_RAMP_SECONDS = 0.3`, so there are no hard volume steps |
| Length | Staggered: `max(first track, delay + second track)`, where delay is the solo segment. Together: `max(main, background)`, because there is no delay. Either way the longer file plays to its end. |
| Trimming | **None**, except with `t-m-loop`. Long music under a short line plays to its full length and is not trimmed when the dialogue ends. With `-loop`, a *short* background is repeated, and the last repeat is cut at the line's end. With exactly `t-m-loop`, a *long* background is also cut at the line's end, with a 0.3 s fade-out. |
| Loudness | The background is gained to the dialogue's integrated loudness (EBU R128, measured after folding to 24 kHz mono) **before** the levels are applied, so "prominent" reliably means "louder". `SCORE_MATCH_LOUDNESS = True`. If either file cannot be measured (e.g. pure silence), no match is applied and a warning is logged. |
| Peak limit | `alimiter` at `SCORE_LIMIT = 0.95` with **`level=disabled`**. Its auto-level is on by default; left on, it re-raises the whole mix and undoes the loudness match. That was observed in the trial. |

A consequence of no trimming: a 21 s piece under a 6.9 s line keeps playing for
about 12 s after the line ends, then the next row's 2 s filler follows. That is
intended. Shorten the background file itself to avoid it.

### 10.6 Scope: per line

Each row's background starts from its own beginning. **One piece running
continuously under several lines is out of scope.** That would need a start row,
an end row, and mixing over the already-joined segment, which is a different
design.

### 10.7 Faults

| Fault | Class | Behaviour |
|---|---|---|
| The prominent track has already ended, e.g. the cue keeps dialogue prominent after the dialogue finished | warning only | **The other track ramps back to full volume** once the prominent one ends, and stays there. A warning in the run log names the row: the cue string is longer than the audio, so its durations probably need adjusting. |
| The solo track ends before its segment does (`m-10s-b-…` on a 6.9 s line). Staggered start only. | warning only | Allowed. There is silence until the second track starts. |
| Background file not found | ordinary problem | `strict=1` aborts. `strict=0` stitches the plain dialogue and warns. |
| Malformed cue string: bad syntax, repeated letter, `t` anywhere but first, `loop` anywhere but last, no segments (`t`, `loop`), a staggered string with a single open-ended segment (`m`), or only one of the two columns set | ordinary problem | Same as above |
| A looped background repeats more than 10 times | warning only | Mixed anyway; the warning asks you to check the right file is named |
| FFmpeg missing | ordinary problem | Same as above. Reported as a conversion failure, as in §7. |

None of these are mandatory checks (§9.6). A mix fault produces a master that is
**missing** a score, not one that is **wrong**: the dialogue is still correct and
on the right line. That is exactly the case `strict` exists for.

`dry_run` validates every cue string and performs every mix, consistent with its
full-depth rule (§9.8).

### 10.8 Where it fits in the pipeline

A per-row stage, **between row-filler resolution and normalization**:

```
resolve inputs -> pre-flight -> mandatory checks -> row fillers
    -> BACKGROUND SCORE -> normalize -> format survey -> build sequence -> stitch
```

For each row with a score, the mix is written to `work/mixed/` and replaces that
row's dialogue path for every later stage. Normalization then sees a file already
in target format and passes it through. Fillers, joining and both backends are
unchanged.

**Caching.** Mixes are written as `work/mixed/<dialogue-stem>.<hash>.mp3`. The
hash covers both source paths, the cue string and every `SCORE_*` and target
setting, so changing any of them produces a new file and never reuses a stale
one. A cached mix is also rebuilt when either source file is newer than it. A
`.json` file beside each mix stores its length, loudness offset and warnings, so
a cached run reports the same as a fresh one.

**Report.** A `BACKGROUND SCORE` block appears only when at least one row names a
score. It lists each mixed row with its background, cue string, length and a
plain reading of the cues. Two more blocks follow when needed:
`CUE STRING LONGER THAN THE AUDIO`, for warnings, and
`BACKGROUND SCORE FAILED`, for faults. Every warning and fault is also written
to the run log as `WARNING: background score for row N ...`.

**Rows with both columns empty** take no new code path: no FFmpeg call, no
report block, and no change to the master.

### 10.9 Verification (measured, 2026-10-05)

`-loop` and the open-ended last segment were added and verified on 2026-10-07:

| Test | Result |
|---|---|
| `t-m-loop`: 3.94 s `dice.mp3` under the 7.73 s `7-DURYODHANA.mp3` | 7.73 s mix, background played 2 times, no warnings |
| `t-m-loop`: 21.36 s `File1.mp3` under the same line | First build: 21.36 s mix (the music played on). **Changed the same day:** now a 7.73 s mix with the music cut at the line's end. Measured on the music alone: −27.7 dB mid-line, −37.3 dB inside the fade, −58.1 dB in the last 50 ms. |
| `t-b-loop`, `b-2s-m-loop` with the 21.36 s music | 21.36 s mix: only the exact `t-m-loop` cuts |
| `t-m-loop` over a 9.0 s silent line | 3 passes at the same level (−47.9 / −47.8 dB, the last pass cut short); each seam dips to silence (−130 / −115 dB) then recovers |
| A 2 s background under 30 s | 15 repeats; the over-10 warning was logged |
| `t-m` vs `t-m-1s` | identical gains; `t-m` logs no warning, `t-m-1s` still does |
| `t-m-loop-loop`, `t-m-2s-loop-b`, a bare `m`, `t-loop` | each rejected with its own message; the row is stitched without music |
| Existing cues `b-9s-m-6s-b-6s` and `t-m-6s-b-15s` | master byte-identical to before; console unchanged |
| Full build, `strict=0`, with `t-m-loop`, `t-m` and a bad cue on three rows | exit 0; two mixed, the bad cue reported under `BACKGROUND SCORE FAILED` |
| The same with `strict=1` | exit 1, as for any ordinary problem |

The original 2026-10-05 tests follow.

On a scratch copy of the project, with `File1.mp3` (21.36 s stereo music) as the
background on six rows:

| Row's cells | Result |
|---|---|
| `b-9s-m-6s-b-6s` | mixed, 21.36 s; dialogue enters at 9 s |
| `t-m-6s-b-15s` on a 2.6 s line | mixed, 21.36 s; warning that the dialogue ended inside its prominent segment, so the music came back up |
| `t-m-30s-b-5s` on a 7.7 s line | mixed, 21.36 s; two warnings: the music came back up, and the cue at 30 s is past the end of the mix and ignored |
| `m-2s-m-3s` | fault: `repeats "m"; letters must alternate` |
| `nope.mp3` + cues | fault: `background "nope.mp3" not found in human_gen, machine_gen, work` |
| background with no cue string | fault: `background_score is set ... but score_cues is empty` |

| Run | Outcome |
|---|---|
| `strict=0` | exit 0; 3 mixed, 3 faulted rows stitched with plain dialogue |
| `strict=1` | exit 1, `ABORTED`; the 3 faults are counted with the ordinary problems |
| `dry_run=1` | exit 0; all faults reported, `Mandatory check failures : 0` |
| Second `strict=0` | `mixed now : 0`, `from cache: 3` |
| Real project, both columns empty | `dry_run=1` exit 0, no `BACKGROUND SCORE` block, master untouched |
| Scratch copy, both columns empty | same 15-dialogue, 31-segment, 3:32.899 master as before |

The master grew by exactly 47.064 s: three 21.432 s mixes replaced lines of
6.888, 2.616 and 7.728 s. A mix is 893 frames, or 21.432 s, while `ffprobe`
reports 21.362 s. The 0.07 s difference is the MP3 encoder's start and end
padding, which the stitched master keeps. The same happens for every file
normalized in §7.

Measured on the staggered mix, the music-led stretches sat **8.7–8.8 dB** below
the music alone. That is the 8.3 dB loudness match plus the limiter's small
reduction of peaks. The dialogue-led stretch matched the dialogue's own level.

---

## 11. Uniform dialogue loudness (implemented)

> **Proposed and implemented 2026-10-05.** The code is in `loudness.py`.
> `stitch_mp3.py` calls it from `apply_loudness()`, and the settings are the
> `loudness` section of `dice-skit.json` (§12). It is on by default;
> `loudness=0` on the command line turns it off for one run. §11.7 records
> the measured result. §11.1 and §11.2 describe the pipeline before this
> stage existed.

### 11.1 The problem

A listener expects every voice at about the same volume. The pipeline does not
provide that: **no stage evens out loudness between lines.**

| Line type | What happens to its level today |
|---|---|
| MP3 already in target format | Copied into the master unchanged (stream copy) |
| `.m4a` / `.aac` / off-format MP3 | Transcoded by §7 normalization, which changes the format but not the level |
| Mixed with a background (§10) | The music is matched to *that line's* dialogue. That sets the balance inside the mix, not the line's level against other lines. A quiet line stays quiet, and its music is turned down to match it. |

So the master inherits whatever level each recording or TTS call happened to
produce.

### 11.2 Measured

Integrated loudness (EBU R128) of the 15 lines on disk, measured after folding to
24 kHz mono, which is how they are heard in the master:

| File | Loudness (LUFS) | True peak (dBFS) |
|---|---|---|
| `0-SAMAY.mp3` | −16.5 | **+2.1** |
| `129_Krishna.m4a` | −17.1 | −0.2 |
| `5-DURYODHANA.mp3` | −18.8 | −2.7 |
| `3-DURYODHANA.mp3` | −19.9 | −2.5 |
| `131_Krishna.m4a` | −21.3 | −4.5 |
| `10_YUDHISHTHIRA.m4a` | −21.9 | −1.5 |
| `16_YUDHISHTHIRA.m4a` | −21.9 | −1.4 |
| `1-DURYODHANA.mp3` | −22.6 | −4.5 |
| `4-SHAKUNI.mp3` | −23.7 | −3.7 |
| `7-DURYODHANA.mp3` | −24.4 | −6.3 |
| `9-SHAKUNI.mp3` | −25.2 | −6.1 |
| `8-SHAKUNI.mp3` | −25.4 | −9.0 |
| `2-SHAKUNI.mp3` | −26.3 | −8.8 |
| `6-SHAKUNI.mp3` | −26.5 | −5.2 |
| `11-DURYODHANA.aac` | −26.5 | −8.8 |

The spread is **10 dB**, enough that the quietest lines sound roughly half as
loud as the loudest.

The spread is **per recording, not per voice.** DURYODHANA alone ranges from
−18.8 to −26.5 LUFS, and SHAKUNI from −23.7 to −26.5. A fixed adjustment per
character would therefore not fix it. Each file has to be measured on its own.

`0-SAMAY.mp3` peaks at **+2.1 dBFS**, so that file is already clipped. Turning
it down makes it quieter but cannot remove distortion that is already there; only
a fresh render or recording can.

### 11.3 Proposal

A **dialogue loudness stage** brings every line to one target before anything
else touches it:

```
resolve inputs -> pre-flight -> mandatory checks -> row fillers
    -> LOUDNESS (new) -> BACKGROUND SCORE -> normalize -> format survey -> stitch
```

Placing it before §10 makes mixed and unmixed lines equally loud. The background
match (§10.5) then works on a dialogue that is already at the target level, so
the music in every mix also ends up at a consistent level.

| Property | Proposal |
|---|---|
| Target | **−19 LUFS** integrated, a common level for speech that leaves room for loud lines. `loudness.target_lufs`. |
| Method | **Measure, then apply one fixed gain** to the whole file, i.e. FFmpeg `loudnorm` in two passes, or `ebur128` measurement plus `volume`. One gain per line keeps the line's own rises and falls. |
| Rejected method | Constantly re-adjusting the volume within a line (`dynaudnorm`, or one-pass `loudnorm`). It would flatten the acting: a line that builds from quiet to shouting would come out level. |
| Peak safety | A true-peak ceiling of **−1 dBFS**, applied by the limiter only where the gain would push a peak past it |
| Scope | **Dialogue only.** Fillers, the opening instrumental, `dice.mp3` and background scores keep their intended levels. |
| Output | Written in the target format, so normalization passes it through |
| Cache | `work/loudness/<stem>.<hash>.mp3` with a `.json` beside it, keyed like §10's mix cache on the source path, mtime and every setting, so re-runs cost nothing |
| Report | A `LOUDNESS` block giving each line's measured loudness and applied gain, plus a warning for any line whose gain needed the limiter |
| Faults | FFmpeg missing, or a file that cannot be measured (e.g. pure silence): an ordinary problem under `strict`. The line is stitched at its original level. |
| Requires | FFmpeg |

### 11.4 What −19 LUFS would do to the current lines

Gain is the target minus the measured loudness. "New peak" is the old peak plus
that gain. Anything above the −1 dBFS ceiling is caught by the limiter.

| File | Measured | Peak | Gain | New peak | Limited by |
|---|---|---|---|---|---|
| `0-SAMAY.mp3` | −16.5 | +2.1 | −2.5 | −0.4 | 0.6 dB |
| `129_Krishna.m4a` | −17.1 | −0.2 | −1.9 | −2.1 | — |
| `5-DURYODHANA.mp3` | −18.8 | −2.7 | −0.2 | −2.9 | — |
| `3-DURYODHANA.mp3` | −19.9 | −2.5 | +0.9 | −1.6 | — |
| `131_Krishna.m4a` | −21.3 | −4.5 | +2.3 | −2.2 | — |
| `10_YUDHISHTHIRA.m4a` | −21.9 | −1.5 | +2.9 | +1.4 | 2.4 dB |
| `16_YUDHISHTHIRA.m4a` | −21.9 | −1.4 | +2.9 | +1.5 | 2.5 dB |
| `1-DURYODHANA.mp3` | −22.6 | −4.5 | +3.6 | −0.9 | 0.1 dB |
| `4-SHAKUNI.mp3` | −23.7 | −3.7 | +4.7 | +1.0 | 2.0 dB |
| `7-DURYODHANA.mp3` | −24.4 | −6.3 | +5.4 | −0.9 | 0.1 dB |
| `9-SHAKUNI.mp3` | −25.2 | −6.1 | +6.2 | +0.1 | 1.1 dB |
| `8-SHAKUNI.mp3` | −25.4 | −9.0 | +6.4 | −2.6 | — |
| `2-SHAKUNI.mp3` | −26.3 | −8.8 | +7.3 | −1.5 | — |
| `6-SHAKUNI.mp3` | −26.5 | −5.2 | +7.5 | +2.3 | 3.3 dB |
| `11-DURYODHANA.aac` | −26.5 | −8.8 | +7.5 | −1.3 | — |

Gains run from −2.5 to +7.5 dB. **8 of the 15 lines** would touch the limiter,
by at most 3.3 dB on `6-SHAKUNI.mp3`. That much limiting is acceptable on speech:
it catches brief peaks and leaves the body of the line alone.

The table also bears on the choice of target. At −19 LUFS the quiet lines need up
to +7.5 dB and half the set gets limited. A lower target, around −21 LUFS, would
halve the limiting but make the whole master quieter. The built stage limited
exactly these 8 lines by these amounts (§11.7).

### 11.5 Costs and limits

- **Every line is re-encoded.** Today conforming MP3s go into the master by
  lossless stream copy; with this stage every dialogue passes through one decode
  and encode at 128 kbps. That is a small but real quality cost, paid once per
  line and then cached.
- **Equal measured loudness is not equal perceived loudness.** A shout and a
  whisper at the same LUFS still sound different, because delivery carries
  intent. Expect some tuning by ear.
- **Some differences are deliberate.** A line meant to be whispered would be
  raised to the level of the rest. A per-row override (§11.6, decision 3) would
  handle that; it is not built yet.
- **It does not repair clipping.** `0-SAMAY.mp3` stays distorted (§11.2).
- **Short lines measure less reliably.** Integrated loudness gates out silence
  and needs some speech to measure. A line under about a second may get an
  imprecise gain. Lines under 1 s are listed under `SHORT LINES` in the report.

### 11.6 Decisions

| # | Decision | As built |
|---|---|---|
| 1 | Target level | −19 LUFS, the proposed default. Change `loudness.target_lufs` in `dice-skit.json` (−31 to −10). |
| 2 | Leave fillers, sound effects and background scores at their own levels? | Yes, dialogue only, always. The proposed `dialogue_only` setting was dropped: its only other value would raise the dice sound to speech level, which nobody wants. |
| 3 | Per-row override column, e.g. `gain_db` | **Not built.** Add it when a deliberately quiet line first needs it. |
| 4 | Can the stage be switched off? | Yes: `loudness.enabled` in `dice-skit.json`, and `loudness=0` / `loudness=1` on the command line, which wins for one run |

### 11.7 Verification (measured, 2026-10-05)

Built on a scratch copy of the project, then measured again on the files it wrote:

| | Before | After |
|---|---|---|
| Quietest line | −26.5 LUFS | −19.1 LUFS |
| Loudest line | −16.5 LUFS | −19.0 LUFS |
| Spread | **10.0 dB** | **0.1 dB** |
| Highest true peak | +2.1 dBFS | −1.2 dBFS |

The `PEAK-LIMITED` block listed the same 8 lines as §11.4 by the same amounts.
`0-SAMAY.mp3` was listed under `ALREADY CLIPPED` too.

**MP3 encoding lowers loudness by about 0.4 dB.** On the first build every line
landed between −19.4 and −19.7 LUFS instead of −19.0. Isolating each step on
`5-DURYODHANA.mp3` showed the cause:

| Step | Result |
|---|---|
| Gain only, written as WAV | −19.0 |
| Gain and limiter, written as WAV | −19.0 |
| Gain, written as MP3 at 128 kbps | −19.4 |
| The same at 160 kbps | −19.4 |
| The source re-encoded with no gain | −19.2, from −18.8 |

The loss is in the encoder, not the limiter, and it does not depend on bitrate.
`loudness.level()` therefore measures the file it wrote and, if it is off by
0.1 dB or more, re-renders once with the corrected gain. That is the "after"
column above. The cost is one extra encode per line, paid once and then cached.

| Run | Outcome |
|---|---|
| `loudness=0`, no `dice-skit.json` | the same 15-dialogue, 31-segment, 3:32.899 master as before §11 |
| Default | 15 lines adjusted; master 3:33.331, since re-encoding adds MP3 padding to each line |
| Second run | `adjusted : 0`, `from cache: 15` |
| A row with a background score | the mix used the leveled line; the music was matched by −4.7 dB, exactly −19.0 minus the music's −14.3 LUFS |
| Real project, `dry_run=1` | exit 0; the 15 lines adjusted into `work/loudness/`; master untouched |

The per-line table (measured loudness, peak, gain, limiting) is written to the run
log under `LOUDNESS PER LINE`, so the console stays readable at 154 rows.

---

## 12. Settings in a JSON file (implemented)

> **Proposed and implemented 2026-10-05.** The loader is `settings.py`, and
> `dice-skit.json` sits beside the scripts, fully commented and holding the
> defaults. `audio_common.py` still exposes the same constant names, now
> filled from the settings, so no other code changed how it reads them.
> §12.10 records how it was verified.

### 12.1 The problem

Before this change, changing a fade level, a gap length or the output bitrate
meant editing Python. That is error-prone for anyone who doesn't work on the code, and it gives
a future GUI nothing to read or write.

### 12.2 One settings file

A single file, `dice-skit.json`, beside the scripts. It holds only the values a
user would change. The Python code keeps the built-in defaults and checks every
value when the file is loaded.

| Property | Proposal |
|---|---|
| Contents | Only values that differ from the defaults. A missing file, or a missing key, means "use the default", so the pipeline works with no JSON at all, as it does today. |
| Precedence | built-in default → JSON → command line. `strict=0` on the command line still overrides the file for one run. |
| Checking | Every value is checked on load for type, range and allowed choices. An unknown key, such as the typo `duck_bd`, is an error rather than being silently ignored. A bad file stops the run before any audio is touched. |
| Run log | The log records the settings actually used and where each came from (default, JSON or command line), so any master can be traced back to its settings. |
| Units | In the key names (`_db`, `_seconds`, `_lufs`), since JSON has no units |
| Version | A `"version": 1` key, so the format can change later without breaking old files |
| Saving | Write to a temporary file, then rename it over the real one, as `concat_backends.py` already does for audio. A crash mid-save cannot leave a half-written file. |

**Settings vs per-line choices.** Global choices (fade level, target loudness)
go in the JSON. Per-line choices (which music, the cue string, fillers) stay in
`dialogues_new.csv`, because they belong to a line.

### 12.3 Comments inside the file

Standard JSON (RFC 8259) has **no comment syntax**. `//` and `/* */` make the file
invalid for Python's `json` module and most other tools. The proposal uses
ordinary keys starting with `_` as comments:

```json
{
  "strict": true,
  "_strict": "true: abort on missing audio. false: build what exists."
}
```

These are real data, so the loader and any GUI must follow three rules:

1. **Every comment key is unique within its object.** If one object holds two
   `"_comment"` keys, Python keeps only the last, silently. Pair each comment with
   its setting (`"strict"` → `"_strict"`), and use `"_comment"` once per section
   to describe the section. The same comment name may appear in different
   sections, e.g. `"_enabled"` in both `loudness` and `tts`.
2. **The loader ignores every key starting with `_`.** All other unknown keys are
   still errors, so typos are still caught.
3. **A GUI keeps the `_` keys when it saves.** It loads the whole file, changes
   only values, and writes everything back in the same order. Python's `json`
   preserves key order, so this is straightforward. The GUI can show the `_` text
   as help beside each field.

Longer comments can be a list of strings, one per line.

Cost: the file roughly triples in size, and a hand-edited value can drift out of
step with its comment. A GUI only changes values, so its saves keep comments
accurate.

The alternative, a `.jsonc` file with `//` comments, reads more naturally, but
saving it from a GUI with a standard JSON library destroys every comment. If that
style were wanted, it would be a read-only reference copy, with help text and
limits kept in a JSON Schema file instead.

### 12.4 Preparing for a GUI

- **The loader's checks are the GUI's contract.** A slider's range comes from the
  same limits the loader enforces. The GUI displays them; it does not define its
  own.
- **The GUI writes only what the user changed.** A later change to a default
  then still reaches every setting the user never touched.
- **One file per project.** A second production gets its own settings.

### 12.5 What the file would hold

| Section | Settings (current constant → proposed key) |
|---|---|
| `run` | `STRICT_MISSING` → `strict`; `CONCAT_BACKEND` → `backend` (`auto` / `ffmpeg` / `python`); `BATCH_SIZE` → `batch_size` |
| `paths` | CSV file, `human_gen/`, `machine_gen/`, `work/`, `log/`, `dialogues-mp3/`, master filename; `search_order` between `human_gen/` and `machine_gen/` |
| `output` | `OUTPUT_FORMAT`, `TARGET_SAMPLE_RATE` (24000), `TARGET_BITRATE` (128000), `TARGET_MONO` |
| `fillers` | `SHORT_FILLER_SECONDS` (2), `LONG_FILLER_SECONDS` (10) |
| `background_score` (§10) | `SCORE_DUCK_DB` (−14), `SCORE_RAMP_SECONDS` (0.3), `SCORE_MATCH_LOUDNESS`, `SCORE_LIMIT` (0.95) |
| `loudness` (§11) | `enabled`, `target_lufs` (−19), `true_peak_dbfs` (−1) |
| `tts` (`generate_mp3.py`) | `TTS_ENABLED`, `LANGUAGE_CODE` (`en-IN`), `MODEL` (`bulbul:v3`), `PACE`, `SPEECH_SAMPLE_RATE`, `OVERWRITE_EXISTING`, and `api_key_source` (§12.7) |
| `drive` (`fetch_drive.py`) | `DRIVE_FOLDER_URL`, `USE_COOKIES` |

`FILLER_BITRATE` is dropped: fillers are always generated in the output format.

### 12.6 What stays out

| Kept out | Why |
|---|---|
| The Sarvam API key | A settings file gets copied, shared and shown in a GUI. See §12.7. |
| CSV column names (`filler_before`, `background_score`, …) | They define the CSV format, not a preference. Renaming one in the JSON would only break the CSV. |
| Accepted file extensions, MPEG frame tables | They describe what the code can handle. Making them settable only lets someone break it. |
| The four mandatory checks | By decision they have no override (§9.6), so they get no setting. |

### 12.7 The Sarvam API key

**Encoding is not protection.** Base64 or similar is reversible by anyone in one
line:

```
c2tfRFVNTVlfMTIzNDU2Nzg5MA==  ->  sk_DUMMY_1234567890
```

**Ordinary encryption only moves the problem.** It needs a decryption key, and if
that key is in the code or beside the JSON, anyone with the files has both.

**Windows DPAPI does work.** Windows encrypts the key with the logged-in user's
credentials, so no decryption key exists in any file. Tested on this machine
with a dummy key, using only Python's standard library (`ctypes` calling
`CryptProtectData` / `CryptUnprotectData`):

| Test | Result |
|---|---|
| Encrypt the dummy key | a 328-character base64 string, storable as `"api_key_dpapi": "AQAAANCMnd8B…"` |
| Decrypt as the same user on the same PC | returns the original |
| Change one byte of the string | refused: `The data is invalid.` |

Limits of DPAPI:

- **It protects against other people, not other programs.** Anything running as
  the same user can decrypt it. Browsers store saved passwords this way.
- **It works only on the PC and login that encrypted it.** The project lives in
  OneDrive. A synced copy opened on another PC, or by another user, cannot
  decrypt the key, so each machine must enter it once.
- **Windows only.** Acceptable for this project as it stands.

**Recommendation: the JSON says where the key is, never what it is.**

```json
"api_key_source": "env",
"_api_key_source": "env: SARVAM_API_KEY from .env (today). keyring: Windows Credential Manager (recommended once a GUI exists)."
```

Windows Credential Manager (through the `keyring` package) uses the same DPAPI
protection but keeps the key in Windows' own vault rather than a project file.
OneDrive therefore never syncs an undecryptable copy. A GUI asks for the key once
and saves it to the vault without touching the JSON. The key can be viewed or
removed in Control Panel → Credential Manager.

| Option | Verdict |
|---|---|
| Windows Credential Manager (`keyring`) | Recommended. Needs `pip install keyring`, not installed today. |
| DPAPI-encrypted value in the JSON | Works with no new packages. Second choice, because of the OneDrive point above. |
| `.env`, as today | Not encrypted. Acceptable if it never leaves this machine or goes into anything shared. |
| Encoded (base64) value in the JSON | **Never.** It protects nothing and looks as if it does. |

Not urgent: `generate_mp3.py` has TTS disabled with its key loading commented
out, and `dice-skit` has no `.env` today.

### 12.8 Sample file with defaults

This is the shipped `dice-skit.json`. Every value is the built-in default, and
keys starting with `_` are the comments of §12.3. The block is generated from
`settings.SPEC` by `python settings.py write`, so it always matches the code.

```json
{
  "_comment": [
    "Settings for the dice-skit audio pipeline.",
    "Keys starting with _ are comments and are ignored.",
    "Delete any setting to use its built-in default.",
    "Command-line flags (strict=0, dry_run=1, loudness=0) override this file for one run.",
    "Paths are relative to this file's folder."
  ],
  "version": 1,
  "_version": "File format version. Do not edit.",
  "run": {
    "_comment": "How a build behaves.",
    "strict": true,
    "_strict": [
      "What to do when a line's audio is missing, corrupt or unconvertible.",
      "true: abort and leave the old master untouched. Use for the final build.",
      "false: skip those lines and build the rest. Use while recordings are arriving.",
      "The four mandatory checks abort either way and cannot be switched off."
    ],
    "backend": "auto",
    "_backend": [
      "How audio files are joined.",
      "auto: FFmpeg if installed, else the built-in joiner (recommended).",
      "ffmpeg: always FFmpeg; fails if it is not installed.",
      "python: built-in joiner; no FFmpeg needed, MP3 only."
    ],
    "batch_size": null,
    "_batch_size": "null = one pass (recommended). A number, e.g. 40, only if one pass runs out of memory."
  },
  "paths": {
    "_comment": "Where files are read from and written to.",
    "csv": "dialogues_new.csv",
    "_csv": "The script: lines, order, fillers, background scores.",
    "human_gen": "human_gen",
    "_human_gen": "Recorded takes and named sound files.",
    "machine_gen": "machine_gen",
    "_machine_gen": "TTS-generated takes.",
    "work": "work",
    "_work": "Caches and generated silence. Safe to delete; rebuilt on the next run.",
    "log": "log",
    "_log": "One log file per run.",
    "output": "dialogues-mp3",
    "_output": "Holds only the finished master.",
    "master": "master_dialogues.mp3",
    "_master": "Master filename, inside the output folder.",
    "search_order": [
      "human_gen",
      "machine_gen"
    ],
    "_search_order": "When both folders hold a file for the same take, the first listed wins."
  },
  "output": {
    "_comment": "Format of the master and of every piece in it.",
    "format": "mp3",
    "_format": "Only mp3 is supported today.",
    "sample_rate": 24000,
    "_sample_rate": [
      "Hz. 24000 matches the TTS output, so those files need no conversion (recommended for speech).",
      "44100 or 48000 suit music-heavy productions, but every TTS line must then be converted.",
      "Also allowed: 16000, 22050, 32000."
    ],
    "bitrate": 128000,
    "_bitrate": [
      "Bits per second. Higher means better quality and a bigger file.",
      "At 16000-24000 Hz: 8000-160000; 128000 is clear speech.",
      "At 32000-48000 Hz: 32000-320000; 192000 or more suits music."
    ],
    "mono": true,
    "_mono": "true: right for dialogue. false: stereo, only if stereo music width matters; about twice the size."
  },
  "fillers": {
    "_comment": "Generated silence between lines, always in the output format above.",
    "short_seconds": 2.0,
    "_short_seconds": [
      "Gap before each line whose filler_before cell is empty.",
      "1-1.5 brisk, 2 natural (default), 3+ slow and dramatic.",
      "Applies to almost every line: +1 s on 154 lines adds about 2.5 minutes."
    ],
    "long_seconds": 10.0,
    "_long_seconds": "Length of long_filler.mp3, for rows that name it, e.g. a scene change."
  },
  "background_score": {
    "_comment": "Music under a line (doc section 10). Only affects rows with background_score and score_cues filled in.",
    "duck_db": -14.0,
    "_duck_db": "How far the faded track is turned down, below 0. -6 subtle, -14 faint but audible (default), -20 almost silent.",
    "ramp_seconds": 0.3,
    "_ramp_seconds": "Crossfade length. 0.1 quick, 0.3 smooth (default), 1.0 slow. Keep it shorter than your shortest cue segment.",
    "match_loudness": true,
    "_match_loudness": "true: bring the music to the line's loudness before mixing (recommended). false: only if the music is pre-mastered to sit under dialogue.",
    "limit": 0.95,
    "_limit": "Peak ceiling for the mix, 0.5-1.0 of full scale. Lower only if mixes sound distorted."
  },
  "loudness": {
    "_comment": "Even out volume across lines (doc section 11). Dialogue only; fillers, sound effects and music keep their levels.",
    "enabled": true,
    "_enabled": "false leaves each line at its recorded level; useful for comparing. Same as loudness=0 on the command line.",
    "target_lufs": -19.0,
    "_target_lufs": [
      "Closer to 0 is louder.",
      "-16 loud: phone speakers, noisy places; more lines get limited.",
      "-19 balanced for speech (default). -23 broadcast standard; quieter, very little limiting."
    ],
    "true_peak_dbfs": -1.0,
    "_true_peak_dbfs": "Highest allowed peak. -1 default; -2 if the master will be re-encoded later, e.g. by an upload platform."
  },
  "tts": {
    "_comment": "Generating lines with Sarvam (generate_mp3.py). The API key is never stored in this file.",
    "enabled": false,
    "_enabled": "true generates missing lines and uses API credit.",
    "api_key_source": "env",
    "_api_key_source": "env: SARVAM_API_KEY from .env (today). keyring: Windows Credential Manager (recommended once a GUI exists).",
    "language": "en-IN",
    "_language": "Language and accent, e.g. en-IN (Indian English), hi-IN (Hindi). See Sarvam's documentation.",
    "model": "bulbul:v3",
    "_model": "Sarvam voice model.",
    "pace": 1.0,
    "_pace": "Speaking speed. 1.0 normal; lower is slower. See Sarvam's documentation for the range.",
    "sample_rate": 24000,
    "_sample_rate": "Rate requested from TTS. Keep it equal to output.sample_rate so generated lines need no conversion.",
    "overwrite_existing": false,
    "_overwrite_existing": "true regenerates every line, e.g. after changing voice or pace. Uses API credit."
  },
  "drive": {
    "_comment": "Downloading recordings (fetch_drive.py).",
    "folder_url": null,
    "_folder_url": "Shared Google Drive folder link. null means pass it on the command line.",
    "use_cookies": false,
    "_use_cookies": "true only if the folder is not link-shared and needs your browser login."
  }
}
```

### 12.9 Decisions and use

| # | Decision | As built |
|---|---|---|
| 1 | File name and location | `dice-skit.json` beside the scripts |
| 2 | Comment style | `_`-prefixed keys (§12.3) |
| 3 | Where the API key comes from | `tts.api_key_source`, `"env"` by default. `generate_mp3.py`'s commented-out key loading now reads it and handles `"keyring"` too, ready for when TTS is enabled. |
| 4 | Ship the full sample, or an empty file with only `version` | The full sample |

Using it:

```powershell
python settings.py           # list every setting, its value, and where it came from
python settings.py write     # create or refresh dice-skit.json with full comments
```

`write` keeps the current values and any `_` comments already in the file,
adding help only for keys that have none. It saves through a temporary file
and a rename. A GUI would call `settings.load()`, `settings.document()` and
`settings.write()`, and read limits and choices from `settings.SPEC`.

Every run's log has a `SETTINGS` block listing each value and its source
(`default` or `dice-skit.json`), followed by the command-line flags in force.
The console shows `Settings : dice-skit.json found` or `not found; built-in
defaults`.

Two smaller changes from the proposal:

- `fillers.short_seconds` and `long_seconds` take decimals (`1.5`), not just whole
  seconds.
- `loudness.dialogue_only` was dropped (§11.6, decision 2), leaving 33 settings.

### 12.10 Verification (measured, 2026-10-05)

| Test | Result |
|---|---|
| No `dice-skit.json` | every value is the default; the master is unchanged |
| The shipped `dice-skit.json` | every source reads `dice-skit.json`; values identical to the defaults |
| `_` comments and overrides together | comments ignored; `strict false`, `short_seconds 1.5`, `target_lufs -21` applied |
| Typo `duck_bd` | `unknown setting "background_score.duck_bd". Known in "background_score": duck_db, …` |
| Section typo `loudnes` | `unknown section "loudnes". Known sections: run, paths, …` |
| `"strict": "no"` | `dice-skit.json: run.strict must be true or false, got 'no'` |
| `target_lufs: -5` | `must be at most -10` |
| `"backend": "sox"` | `must be one of auto, ffmpeg, python` |
| Two `_comment` keys in one section | `key "_comment" appears twice in the same section` |
| `bitrate 192000` at 24000 Hz | `too high for 24000 Hz; the maximum at 16000-24000 Hz is 160000` |
| Trailing comma | `is not valid JSON: Illegal trailing comma …` |
| `"version": 2` | `is format version 2; this code reads version 1` |
| Any of those, then `stitch_mp3.py` | exit 1 at import, before a log or any audio is touched |
| JSON `strict false`, `loudness false`; command line `strict=1 loudness=1` | command line won; the log showed both, with their sources |
| `write` on a file with a custom `_strict` and `_comment` | both kept; the 31 other settings gained the built-in help |

---

## 13. A graphical interface (proposed, NOT implemented)

> **Nothing in this section is in the code.** It records a proposal from
> 2026-10-05 for a GUI on Windows, Android and iOS, with an option to host it
> on a server later. §13.12 lists the decisions still open. The facts in
> §13.2 were checked against the Python, Apple, Flet and FFmpegKit sources
> listed under Sources.

### 13.1 Requirements

| # | Requirement |
|---|---|
| R1 | Built for Windows first, extensible to Android and iOS apps |
| R2 | GUI and backend installable as a `setup.exe`, an `.apk` and an `.ipa` |
| R3 | Minimalistic |
| R4 | Intuitive |
| R5 | No complex transitions |
| R6 | New functionality and screens easy to add |
| R7 | Runs standalone on a PC, phone or tablet, and can also be hosted on a server and used from the cloud |
| R8 | Does not compromise security on the user's device |
| R9 | Minimal attack surface |

### 13.2 Constraints that shape the design

Three facts decide most of the design. Each was checked.

| Fact | Consequence |
|---|---|
| Python's `subprocess` module is **not available on Android or iOS**. Python's documentation says: "Availability: not Android, not iOS, not WASI." | The pipeline starts FFmpeg or ffprobe as a separate program from 15 places in four modules (`audio_common.py`, `background_score.py`, `concat_backends.py`, `loudness.py`). None of those calls can run on a phone as written. |
| Apple's App Review Guideline 2.5.2: apps "may not download, install, or execute code which introduces or changes features or functionality of the app." | Everything an iOS app runs must be inside the app when it is reviewed. FFmpeg cannot be downloaded or updated separately on iOS. |
| **FFmpegKit**, the usual way to bundle FFmpeg in a mobile app, was **retired and archived on 2026-07-02**. Its successor, FFmpegKitNext, is distributed as source only. | On mobile we would have to build FFmpeg ourselves, or use a community fork. That is the largest single cost of R1. |

There is also a licensing point. A full FFmpeg build with `libmp3lame` is
LGPL or GPL, depending on how it is built. Shipping it inside an app,
especially through an app store, carries obligations that must be checked
before release. §13.12, decision 5.

So the GUI itself is easy to make portable. What does not port is the
**audio engine**: the FFmpeg-based processing.

### 13.3 Proposed architecture

Split the work in two, with one narrow, documented boundary between them:

```
   +---------------------------+        +--------------------------------+
   |  FRONT END (screens)      |        |  ENGINE (today's pipeline)     |
   |  Flet: one Python code    |  calls |  settings.py, stitch_mp3.py,   |
   |  base for every platform  | -----> |  loudness.py, background_...   |
   |  shows forms, progress,   | <----- |  returns reports and events,   |
   |  reports, the master      | events |  never prints to the GUI       |
   +---------------------------+        +--------------------------------+
                                                    |
                                        audio ops go through one module:
                                        +-------------------------------+
                                        |  AUDIO TOOLS (new adapter)    |
                                        |  desktop/server: ffmpeg exe   |
                                        |  mobile: FFmpeg library build |
                                        +-------------------------------+
```

**Engine API.** Today the pipeline talks to the user by printing (284 `print`
calls in `stitch_mp3.py` alone) and by exit codes. The engine would instead
expose a few functions that return structured results:

| Function | Returns |
|---|---|
| `check(project)` | The dry-run report: every fault, grouped by type, plus the four mandatory checks (§9.6) |
| `build(project, options, on_event)` | The master's path, its duration and the report. `on_event` receives progress ("leveling 4 of 15") and each warning as it happens. |
| `settings.load()` / `settings.write()` | Already exist (§12) |
| `list_lines(project)` | Each CSV row with the file it resolved to, its source and its status, i.e. today's `ROW SOURCES` table |

The command line keeps working: `stitch_mp3.py` becomes a thin wrapper that
calls `build()` and prints the events, so its output stays the same.

**Audio tools adapter.** The 15 FFmpeg call sites move into one module with
a single function for each operation the pipeline needs: measure loudness,
transcode, apply gain, mix, probe duration. Desktop and server builds run the
`ffmpeg` program, as today. Mobile builds call an FFmpeg library linked into
the app. The rest of the engine never knows which is in use. Pure-Python
features need no change on any platform: the MP3 frame joiner (§3) and the
settings checks.

**Front end: Flet.** Flet runs one Python codebase as a Windows, macOS and
Linux desktop app, an Android and iOS app, and a web app, using Flutter's
renderer. Its `flet build` command produces `windows`, `apk`/`aab`, `ipa` and
`web` outputs. That satisfies R1, R2 and R7 without a second language. The
engine is already Python, so the front end calls it directly, with no
network layer on the device.

| Option considered | Verdict |
|---|---|
| **Flet** (Python + Flutter) | **Recommended.** One language, every target, calls the engine in-process |
| Flutter (Dart) with a Python engine embedded | Strongest mobile support, but two languages and a bridge between them |
| Web app in a desktop shell (Electron, Tauri) with a phone wrapper | Large footprint, a local web server or bridge on the device (more attack surface), awkward on iOS |
| .NET MAUI | Windows, Android and iOS, but no web target, so R7 needs a second front end |
| Tkinter / Qt for Python | Fine on Windows, but no practical path to Android, iOS and web together |

### 13.4 Screens

Four screens, one job each. Navigation is a fixed bar: a side rail on wide
screens and a bottom bar on phones. That is the same layout everywhere, so
users learn it once (R4).

| Screen | Shows | Actions |
|---|---|---|
| **Project** | The project folder, and counts of lines, recordings, TTS lines and missing lines | Open a project; fetch from Google Drive (`fetch_drive.py`) |
| **Lines** | One row per CSV line: take, character, the file it resolved to, the source folder, and a status (ok, missing, mismatch, mixed) | Filter by status; play a line; tap a fault to see the fix (the §0.11 table) |
| **Build** | Check and Build buttons; a progress bar; the report grouped as on the console today (`MISSING`, `PEAK-LIMITED`, ...) | Check (the dry run); Build; strict and loudness switches for this run only |
| **Settings** | Every setting from `settings.SPEC`, grouped by section, with its `_` help text beside it | Edit; Reset to default; Save (`settings.write()`, which keeps the comments, §12.3) |

After a build, the master can be played and its file opened from the Build
screen. There is no separate screen for it.

**Design rules for R3, R4 and R5:**

- **Platform-standard controls only:** switches, sliders, dropdowns, text
  fields. No custom widgets.
- **Settings controls come from `settings.SPEC`.** A setting with `choices`
  becomes a dropdown, a `bool` a switch, and a bounded number a slider plus a
  field. Its limits come from SPEC too, so the GUI can never offer a value the
  loader would reject.
- **No animated transitions.** Screens switch instantly. The only motion is the
  progress bar.
- **One primary action per screen**, always in the same place.
- **Plain-language messages,** reusing the troubleshooting wording, never a
  stack trace. Details are available behind a "Show details" link.
- **Light and dark themes** follow the operating system. The default font
  size scales with the system setting.

### 13.5 Adding functionality and screens (R6)

- Each screen is one Python module that registers a name, an icon and a build
  function. The navigation bar is built from that list, so a new screen is a
  new file plus one line.
- **New settings need no GUI code:** adding an entry to `settings.SPEC` makes
  it appear on the Settings screen automatically, with its help text and
  limits.
- **New report blocks need no GUI code:** the engine emits each block as a
  titled list, and the Build screen renders any block it receives.
- **New pipeline features** are added to the engine first, and the front end
  only calls them. Every feature then works from the command line, the
  desktop app, the phone app and the server alike.

### 13.6 Packaging (R2)

| Target | Built with | Contents | Signing |
|---|---|---|---|
| **Windows `setup.exe`** | `flet build windows`, wrapped in an installer (Inno Setup) | App, embedded Python, engine, a pinned FFmpeg build | Authenticode code-signing certificate, or SmartScreen warns on every install |
| **Android `.apk` / `.aab`** | `flet build apk` (`aab` for Google Play) | App, Python, engine, FFmpeg built as a library | Android signing key |
| **iOS `.ipa`** | `flet build ipa`, which **requires a Mac** | Same as Android | Apple Developer account; distribution through the App Store, TestFlight or enterprise signing |

- **The installer bundles FFmpeg.** Users no longer install it separately, so
  §0.3 becomes developer-only. The build is pinned to a known version and its
  checksum is verified when the installer is built.
- **Per-user install.** No administrator rights, consistent with the
  `--scope user` approach in §0.
- **Building packages:**
  - Windows and Android packages can be built on Windows.
  - The `.ipa` needs macOS.
  - A CI service with macOS runners avoids having to own a Mac.

### 13.7 Running from a server (R7)

Flet can run the **same app** as a web app in two ways:

| Mode | How it works | Fit |
|---|---|---|
| **Server-driven (dynamic)** | Python runs on the server. The browser only renders the UI and sends clicks over a WebSocket. | **Recommended.** The engine and FFmpeg stay on the server. Nothing runs on the user's device but a browser page. |
| **In the browser (static, Pyodide)** | Python runs inside the browser via WebAssembly | Not suitable. `subprocess` is unavailable there too. It is 3–5x slower, and the code is visible to anyone. |

Hosting adds what a single-user device does not need:

- **Accounts and login.** Each user sees only their own projects, kept in a
  separate storage area per user.
- **Uploads instead of folders.** Recordings and the CSV are uploaded, and the
  master is downloaded. The server never reads arbitrary paths.
- **A job queue.** Builds take seconds to minutes, so they run as background
  jobs, with progress sent to the page.
- **HTTPS only,** behind a standard reverse proxy, with upload size limits and
  rate limits.
- **Settings change role.** `paths.*` settings are fixed by the server, not
  editable by users, so a user can never point the engine at another user's
  files.

Because the engine API (§13.3) is the same in every mode, a server build is a
different packaging of the same code, not a rewrite.

### 13.8 Security on the user's device (R8)

| Rule | Why |
|---|---|
| **No listening network port** in the installed apps. The front end calls the engine in-process. | Nothing on the network can reach the app. A local web server, as in Electron-style designs, would be reachable by other programs on the machine. |
| **No shell.** External programs run only through `subprocess.run([list])` with `shell=False`. The code already does this everywhere: there are no `shell=True` uses. | User text (file names, cue strings) can never become a command. |
| **FFmpeg is bundled and found by its own path,** not through `PATH`. | Prevents a different `ffmpeg.exe` placed earlier on `PATH` from being run instead. |
| **Files only inside the chosen project folder.** Every CSV-supplied filename (fillers, background scores) is resolved and checked to stay inside the project's folders. | A CSV cell such as `..\..\Windows\...` cannot read or overwrite files elsewhere. |
| **The API key never leaves the OS vault.** The GUI writes it to Windows Credential Manager or the iOS/Android Keychain/Keystore (`api_key_source: "keyring"`, §12.7). It is never displayed again, never logged, and never written to `dice-skit.json`. | Settings files and logs get shared; the vault does not. |
| **Least privilege.** No administrator rights on Windows. On phones, only file-picker access, plus network access for Drive or TTS. | Nothing more is requested than the features need. |
| **Signed packages.** | Users can verify where the app came from; tampering breaks the signature. |
| **No telemetry, no auto-update that downloads code.** Updates come only through the installer or the app stores. | Required on iOS by Guideline 2.5.2, and it removes a channel through which code could be swapped. |
| **Logs stay local** and contain no secrets. | Matches today's `log/` behaviour. |

### 13.9 Minimal attack surface (R9)

What an attacker could reach, and how each is kept small:

| Entry point | Exposure | Control |
|---|---|---|
| Network | Only outbound calls the user starts: Google Drive (`fetch_drive.py`) and Sarvam TTS. Both are HTTPS, with certificates verified (truststore, §0.5). | No inbound connections on devices. On a server: HTTPS only, authentication on every request. |
| Files the user opens | CSV, `dice-skit.json`, audio files | Existing strict validation in `settings.py` and `load_csv()`. FFmpeg parses the audio; keeping its build current is the main ongoing task. |
| Bundled code | Python, Flet, FFmpeg and Python packages | Pinned versions, a short dependency list, and a periodic check for security advisories |
| Extensibility | New screens and features | Built into the app at compile time. **No plugin loading** at run time. |

**FFmpeg is the largest component that parses untrusted input.** Keeping it
updated is the most important security task over time. On a server, each
build should also run in an isolated worker process with a time limit.

### 13.10 What changes in the existing code

| Change | Size |
|---|---|
| Engine API: `check()`, `build()`, `list_lines()`, returning reports and emitting events instead of printing; the command line becomes a wrapper | Medium. The pipeline logic is unchanged, but its output is restructured. |
| Audio tools adapter: the 15 FFmpeg call sites moved behind one module | Medium. Required for mobile, and useful anyway. |
| A check that every CSV-named file resolves inside the project | Small (§13.8) |
| `keyring` support wired into `generate_mp3.py` | Small. The setting already exists (§12.7). |
| New `gui/` package: four screens plus a screen registry | Medium |
| Packaging scripts for each target | Small to medium; the `.ipa` needs a Mac or CI |
| Mobile FFmpeg library build | **Large.** The main cost of the mobile targets (§13.2). |

### 13.11 Suggested order

1. **Engine API and audio tools adapter.** Behaviour unchanged, verified by
   the same before-and-after master comparison as §11.7.
2. **Windows desktop app with the four screens,** packaged as `setup.exe`.
   This delivers R1–R6 and R8–R9 on Windows.
3. **Server-driven web mode** (R7), with accounts, uploads and a job queue.
4. **Android `.apk`,** once the FFmpeg library build works.
5. **iOS `.ipa`,** which also needs macOS, an Apple Developer account and
   App Store review.

Each step works on its own, so the Windows app is useful long before the
phone apps exist.

### 13.12 Open decisions

| # | Decision | Proposed default |
|---|---|---|
| 1 | GUI framework | Flet (§13.3) |
| 2 | Mobile audio engine | Build FFmpeg as a library, or use a maintained community FFmpegKit fork; evaluate both at step 4 |
| 3 | How should processing be split between device and server? | Hybrid: responsive/local work on-device, with optional server processing for suitable heavy jobs and cloud features (§15). |
| 4 | Where projects live on phones | The app's own storage, with import and export through the system file picker |
| 5 | FFmpeg licence and build options (LGPL vs GPL, which codecs) | Settle before the first public release, especially for app-store distribution |
| 6 | Code-signing certificates and store accounts | Needed before distribution; not needed for development |
| 7 | Server hosting | Decide at step 3 |

---

## 14. Folder layout: input, in-out, output (implemented)

> **Proposed, decided and implemented 2026-10-08.** The code is in
> `layout.py` (migration and `clean`) and `audio_common.configure()` (the
> paths). The settings are under `paths` in `dice-skit.json`. The real project
> was migrated on 2026-10-08 and verified (§14.6).

### 14.1 The problem

Inputs, intermediate files and outputs all sat at the top of the project:
`human_gen/` (input), `machine_gen/` (written by TTS, read by the stitcher),
and `dialogues-mp3/`, `work/` and `log/` (all output). Nothing showed which
folders were safe to delete and which held irreplaceable recordings.

### 14.2 The layout

```
dice-skit\
  input\                       read by the pipeline; nothing here is ever deleted
    dialogues_new.csv          the script
    human_gen\                 recordings and named sound files (fetch_drive.py writes here)
  in-out\
    machine_gen\               generate_mp3.py writes here; stitch_mp3.py reads it
  output\                      everything the pipeline makes; rebuildable
    master\                    master_dialogues.mp3
    work\                      caches and generated silence
    log\                       one log per run
  dice-skit.json               settings, beside the scripts
  *.py, docs\, gui\            code and documentation
```

`dice-skit.json` stays beside the scripts. It is how the pipeline finds every
other folder, so it cannot live inside one of them.

### 14.3 Settings

| Key | Default | Relative to |
|---|---|---|
| `paths.input` | `input` | the project folder (or absolute) |
| `paths.in_out` | `in-out` | the project folder (or absolute) |
| `paths.output` | `output` | the project folder (or absolute) |
| `paths.csv` | `dialogues_new.csv` | `input` |
| `paths.human_gen` | `human_gen` | `input` |
| `paths.machine_gen` | `machine_gen` | `in_out` |
| `paths.master_dir` | `master` | `output` |
| `paths.work` | `work` | `output` |
| `paths.log` | `log` | `output` |
| `paths.master` | `master_dialogues.mp3` | `master_dir` |

Any of them may be absolute. For example, `"output": "D:/dice-skit-output"`
keeps rebuildable files off OneDrive.

**Overlap check.** On load, `output` must not be the same as, inside, or
contain `input` or `in_out`. Otherwise `clean` could delete recordings. A bad
combination stops the run with `ERROR in settings: paths.output ... must not
be the same as, inside, or contain paths.input ...`.

### 14.4 Moving an existing project (automatic, on the first run)

**Decided: automatic.** The first run of any script on an old-layout project
moves it into the new layout before reading the settings. Because this moves
the user's recordings, it is deliberately conservative:

| Rule | Why |
|---|---|
| **Moves, never copies or deletes** | Nothing is duplicated or lost |
| **Checks every destination first.** If any already exists, it moves nothing and says which. | A project is never half-moved |
| **Rolls back on failure.** If one move fails, everything already moved is put back, and any empty folders it created are removed. | The next run sees the old layout and tries again |
| **Updates `dice-skit.json` only after every move succeeded.** Paths left at the old defaults switch to the new ones; a path the user had changed is kept. | Settings never point at folders that don't exist |
| **Lists every move** on the console and in `output\log\migrate<timestamp>.log` | An audit trail |

It detects an old project by its top-level folders (`human_gen`,
`machine_gen`, `dialogues-mp3`, `work`, `log`, `dialogues_new.csv`) or its
old-style `paths` settings. It never runs on a project that already has
`input\` or `output\`.

The old master folder `dialogues-mp3\` becomes `output\master\`.

**A file in use stops the move.** On the first attempt in the real project,
`dialogues_new.csv` was open in another program, and the move of that file
failed with `[WinError 32]`. The rollback worked: nothing had moved, the empty
`input\` it had created was removed, and the error asked for the file to be
closed. After the file was closed, the next run migrated normally.

### 14.5 `python stitch_mp3.py clean`

Empties `output\master\` and `output\work\`.

| Removed | Kept |
|---|---|
| The master | `output\log\` |
| Caches and generated silence | All of `input\` and `in-out\` |

- **Asks first.** It lists each folder with its file count and size, then
  asks `Delete these N file(s)? [y/N]`. Anything but `y` or `yes` deletes
  nothing. `clean yes=1` skips the question, for scripts.
- **Only inside output.** Each folder is checked to lie inside `output` and
  not to contain the log folder. Otherwise `clean` refuses, deleting nothing.
- **Never builds.** `clean` accepts only `yes=1`. Anything else, such as
  `clean strict=0`, is refused.
- **Logged** to `output\log\clean<timestamp>.log`.
- The next build recreates everything it needs. That build is slower,
  because the caches are rebuilt.

### 14.6 Verification (measured, 2026-10-08)

On scratch copies of the project:

| Test | Result |
|---|---|
| Migrate a full copy | All 79 files arrived intact (checksums compared); a second run moved nothing |
| A destination already exists | `cannot move ... these already exist: in-out\machine_gen. Nothing was moved.` |
| A move fails partway (simulated "file in use") | Everything put back; no leftover folders; the next run migrated |
| A customised `paths.log` and `run.strict` | Both kept |
| Build after migrating | Master byte-identical to the old layout's; caches reused on the next run; dry run and the desktop app's engine both worked |
| `clean`, answering N | Nothing deleted |
| `clean`, answering y | 44 files removed from `master` and `work`; logs, `input` and `in-out` untouched; a clean log written |
| `clean` again | `Nothing to delete.` |
| `clean strict=0` | Refused, exit 1 |
| `paths.work` = `../elsewhere` | `clean` refused, exit 1 |
| `paths.output` = `input` | Rejected on load, exit 1; nothing touched |

On the real project:

| Test | Result |
|---|---|
| First attempt, with `dialogues_new.csv` open elsewhere | Failed safely and rolled back; project unchanged |
| Second attempt, file closed | 6 items moved, `dice-skit.json` updated; all 79 files identical to a safety copy taken first |
| `strict=0` build in the new layout | Exit 0; master byte-identical to the one built just before the move |
| Backup hook | Now covers `input\dialogues_new.csv` and `gui\`; no audio and nothing from `output\` |

---

## 15. Product direction: hybrid processing (decided 2026-10-17)

> **Decision:** A **hybrid approach** is likely the most flexible longer-term
> option. This is a product direction, not an implementation commitment about
> which operation must run locally or remotely.

The product is expected to grow beyond command-line audio stitching into a
Windows application with a UI, Android and potentially iOS apps, audio editing,
later video editing, and monthly subscriptions. No single current script or
desktop packaging format should dictate the architecture for all those
platforms.

### 15.1 Responsibilities

- **On-device:** project browsing, responsive editing interactions and preview,
  and processing that can reasonably run locally. Preserve offline capability
  where feasible and keep user media local unless a feature needs otherwise.
- **Server, when useful:** optional compute-heavy jobs, cloud projects or
  cross-device features. Uploading media must be explicit, with secure transfer,
  access control, retention/deletion rules, job progress and cost controls.
- **Shared boundary:** define a portable project format and processing
  interface. The UI should call that interface rather than depend directly on
  script globals, console output or a single platform's filesystem layout.
- **Subscriptions:** paid access must be checked through an appropriate
  entitlement mechanism, not trusted solely to a client-side setting.
  Payment flows must be selected to satisfy the applicable Windows, Google
  Play and Apple distribution rules before implementation.

This direction accepts the trade-off: local execution improves privacy,
offline availability and interactive response, while server processing can
provide consistent, centrally updated and compute-intensive operations but
adds networking, privacy, operational and hosting costs. The first Windows
executable in §16 is deliberately a local prototype; it does not decide that
the eventual product must be local-only.

### 15.2 Architectural sequence

1. Define project and operation interfaces before tying the future UI to
   command-line entry points.
2. Package the existing local stitch pipeline for Windows as a bounded
   prototype (§16).
3. Build the Windows UI against the processing interface; keep editing
   projects portable.
4. Design the audio editing/timeline model before adding destructive media
   operations; extend it for video only when the product scope is clear.
5. Evaluate mobile processing engines and platform constraints, then choose
   which operations stay local and which may be offered as server jobs.
6. Add accounts, subscription entitlements, and payment flows after deciding
   which features are paid and how platform policies apply.

The ordering is provisional; technical feasibility, user needs and operating
costs may change it. In particular, a hybrid design does not require building
the server before a useful local Windows prototype exists.

---

## 16. First Windows executable: local-processing prototype

> **Console prototype implemented and tested 2026-10-17.** The target is
> `output\release\modaud.exe`, with `output\release\ffmpeg\` and a
> self-contained sample project beside it. This is a packaging and workflow
> prototype, not the final multi-platform application architecture in §15.

### 16.1 Release contents and build entry point

The `release` Makefile target invokes `make_release.py`. The Python script
prepares the release folder, packages the existing application with PyInstaller
and stages the sample project. The build machine needs Python, the project's
build dependencies, PyInstaller, GNU Make, and `ffmpeg.exe` plus `ffprobe.exe`
on `PATH`. These are build-machine tools, not runtime requirements for users
of the packaged executable.

Current targets are `make test` (pytest and Black), `make release-windows`
(`make release` is an alias), and `make clean` (removes only the generated
`output\release\` tree). `make android` deliberately exits with a message until
an Android framework and build command are selected.

Release layout:

```
output\release\
  modaud.exe
  README.txt
  ffmpeg\
    ffmpeg.exe
    ffprobe.exe
    [DLLs required by the selected FFmpeg build, if any]
    [applicable FFmpeg license and notices]
  sample_project\
    dice-skit.json
    input\
      dialogues_new.csv
      human_gen\
        [three selected dialogue MP3s and every referenced audio asset]
    in-out\
      machine_gen\
    output\
```

The executable uses the existing stitch pipeline rather than forking its
audio behaviour. `audio_tools.py` supports a bundled FFmpeg directory through
`DICE_SKIT_FFMPEG_DIR`; the packaged launcher sets it to the release's sibling
`ffmpeg` folder before the first tool lookup. Both `ffmpeg.exe` and
`ffprobe.exe` are required by current features. The release builder resolves
them from the build machine's `PATH`, verifies that they run, and stages any
runtime DLLs found beside them. `ffplay.exe` is not required by the current
pipeline.

The sample CSV has three rows matched to three selected MP3s. Its rows
demonstrate distinct `filler_before`, `filler_after`,
`background_score` and `score_cues` behaviour. Every named filler or score
asset referenced by those rows is included in the sample project's
`human_gen` folder. The cue strings were validated by a sample dry run. Before
public distribution, listen to the generated master as well. The bundled README
explains how to run the sample or pass another project folder to the executable.

The executable takes the project folder as its first argument and forwards
remaining arguments to the existing stitch command. The launcher configures
the pipeline from that project, so the current working directory does not
select the project.

### 16.2 Generated-file marker and free-tier limits

**Proposed requirement:** mark audio outputs produced by `modaud` so a later
run can recognize them when applying a free-use limit to files supplied for
concatenation. This marker is not implemented or validated.

For MP3, evaluate a namespaced ID3 metadata frame (for example, a custom
`TXXX` frame) containing an application identifier and marker version. This
is metadata at the start of an MP3 file, not a change to the encoded MPEG
audio frames. Do not insert arbitrary bytes into the MPEG frame stream: that
could make files invalid or impair playback.

Treat this metadata as **provenance information, not trustworthy proof of
payment or a security boundary**. A user or another tool can remove, alter or
copy it; a local executable cannot safely protect a signing secret embedded
inside itself. Therefore:

- A marker may help identify ordinary outputs from this executable and inform
  a friendly local limit or warning, but must not be described as tamper-proof.
- If the free limit must be robust against deliberate circumvention, enforce
  entitlements through an account/service or another design reviewed for that
  threat model. A server-issued authenticated marker can authenticate a
  marker, but removing metadata still means a purely local scanner cannot
  prove a file's origin.
- Do not count or reject files based only on a marker until the free-limit
  policy is defined, including what counts as a file and how imported,
  edited, copied and concatenated outputs are treated.
- Test metadata survival through the existing FFmpeg conversions, the native
  MP3 joiner, other common editors and export paths. If normal processing strips
  tags, decide whether to reapply the marker to final outputs and document the
  resulting limitations.

Before shipping this feature, settle the quota policy and user messaging,
choose a metadata format and versioning scheme, verify that the marker does
not affect playback, and assess privacy and licensing implications. The
application marker must not contain personal data or an API key.

### 16.3 Verification

The packaged prototype was built on Windows and run without FFmpeg on the
process `PATH`:

| Check | Result |
|---|---|
| `make test` | 36 passed, 1 symlink test skipped because directory symlink creation is not permitted; Black check passed |
| `make release-windows` | PyInstaller created `output\release\modaud.exe` |
| Bundled tools | `ffmpeg.exe`, `ffprobe.exe` and the source `LICENSE` copied to `ffmpeg\` |
| Sample dry run | 3 of 3 dialogue files matched; all three distinct cue strings parsed and the project passed pre-flight |
| Sample build | Exit 0; master duration 190.166 seconds (about 190.2 seconds) |
| `make clean` | Removed only `output\release\`; a read-only OneDrive directory was made writable for removal |
| `make android` | Exited with the expected not-configured message |
| Generated-file marker | Not implemented; see §16.2 |

Before public distribution, exercise the Makefile target on a clean Windows
build machine and review FFmpeg's build-specific redistribution obligations,
signing and the free-limit policy.

---

## Sources

- [FFmpeg concat demuxer](https://ffmpeg.org/ffmpeg-formats.html#concat-1) — stream-copy requirements, `auto_convert`, duration handling
- [Supported codecs (Windows)](https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/supported-codecs) — the `D`/`E` audio table showing native MP3 and AAC-LC *encoders* on desktop
- [MediaClip](https://learn.microsoft.com/en-us/uwp/api/windows.media.editing.mediaclip) — "Creates a video clip from a video file"; video-only surface
- [MediaComposition](https://learn.microsoft.com/en-us/uwp/api/windows.media.editing.mediacomposition) — `Clips` vs `BackgroundAudioTracks`, `RenderToFileAsync`
- [MediaEncodingProfile](https://learn.microsoft.com/en-us/uwp/api/windows.media.mediaproperties.mediaencodingprofile) — `CreateMp3` / `CreateM4a`, and the warning that a profile may be created for a codec the device lacks
- [python-soundfile](https://python-soundfile.readthedocs.io/en/latest/) — MP3 write from 0.11.0 / libsndfile 1.1.0; no AAC
- [FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html) — `amix`, `volume` (`eval=frame`), `adelay`, `alimiter`, used by the §10 prototype
- [FFmpeg `ebur128` and `loudnorm`](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) — EBU R128 measurement, used by `loudness.py` (§11)
- [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) — the JSON standard, which has no comment syntax (§12.3)
- [CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) — Windows DPAPI, tested in §12.7
- [keyring](https://pypi.org/project/keyring/) — Python access to Windows Credential Manager, the §12.7 recommendation
- [Python `subprocess`](https://docs.python.org/3/library/subprocess.html) — "Availability: not Android, not iOS, not WASI" (§13.2)
- [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) — guideline 2.5.2 on downloading or executing code (§13.2)
- [FFmpegKit](https://github.com/arthenica/ffmpeg-kit) — retired and archived 2026-07-02; FFmpegKitNext is source-only (§13.2)
- [Flet: publishing](https://flet.dev/docs/publish) — `flet build` targets `windows`, `apk`, `aab`, `ipa`, `web`; `ipa` needs macOS (§13.6)
- [Flet: web apps](https://flet.dev/docs/publish/web) — server-driven vs in-browser (Pyodide) web modes (§13.7)
- [pydub](https://github.com/jiaaro/pydub) — WAV native, everything else via FFmpeg/libav
- [PyWinRT](https://pywinrt.readthedocs.io/en/latest/types.html) — `winrt-<Namespace>` package naming, awaitable handling