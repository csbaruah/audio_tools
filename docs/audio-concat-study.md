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
machine; the rest of the document is the study itself.

---

## 0. Setting up on a new PC

Everything here assumes Windows. The pipeline is plain Python and does not need
to be installed or built.

### 0.1 What actually needs installing

Worth knowing before you start, because it is less than it looks:

| Component | Needs | Why |
|---|---|---|
| `stitch_mp3.py` | **Python only** — zero third-party packages | Verified: `audio_common.py`, `concat_backends.py`, `stitch_mp3.py` and `generate_mp3.py` import nothing outside the standard library |
| `stitch_mp3.py` with `.m4a` inputs, or inputs not already 24 kHz mono 128 kbps MP3 | **+ FFmpeg** | AAC cannot be carried in an MP3 stream and resampling needs a decoder — see §7 |
| `fetch_drive.py` | **+ `gdown`, `truststore`** | Drive crawling; `truststore` for corporate TLS — see §0.5 |
| `generate_mp3.py` | `sarvamai`, `python-dotenv`, plus a `SARVAM_API_KEY` | Only if you re-enable TTS generation, which is off by default |

So a machine that only ever stitches already-conforming MP3s needs nothing but
Python. FFmpeg becomes necessary the moment a `.m4a` or an off-format file
appears, which in practice is immediately.

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
  audio_common.py         shared config, validation, CSV loading, run log
  concat_backends.py      ffmpeg + pure-Python backends, normalization
  stitch_mp3.py           RUNNABLE - builds the master
  fetch_drive.py          RUNNABLE - pulls audio from Google Drive
  generate_mp3.py         Sarvam TTS (disabled)
  dialogues_new.csv       the running order, and the filler_before column
  docs/
    audio-concat-study.md this document
```

Four directories are created automatically and never need copying:

| Directory | Contents | Copy it? |
|---|---|---|
| `human_gen/` | Human recordings from Drive, plus named filler audio (sound effects, opening music) | Only the fillers — re-fetch the rest |
| `machine_gen/` | Sarvam TTS output from `generate_mp3.py` | Only if you have TTS output worth keeping |
| `work/` | Generated `short_filler.mp3` and the `normalized/` transcode cache | No — rebuildable |
| `dialogues-mp3/` | **Output only**: `master_dialogues.mp3` | No |
| `log/` | One `run<timestamp>.log` per invocation | No |

`dialogues-mp3/` deliberately holds nothing but the finished master, so it can
be deleted and rebuilt at any time without touching source audio.

`stitch_aud.py`, if present, is the original pre-split script kept only for
reference and is not used. Note that it also contains non-breaking spaces and
will not compile.

All paths are anchored to the script directory rather than the working
directory, so it does not matter where you invoke from.

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

Downloads land in **`human_gen/`** as one flat directory, whatever Drive's
subfolder structure was — the Drive folder holds human-recorded takes. Files
already present in *either* `human_gen/` or `machine_gen/` are skipped, matched
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
python stitch_mp3.py strict=0        # skip what is unusable, stitch the rest
python stitch_mp3.py 20              # only the first 20 rows
python stitch_mp3.py 20 strict=0     # both, in either order
```

Output is `dialogues-mp3/master_dialogues.mp3`.

### 0.9 Verify it worked

```powershell
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 dialogues-mp3\master_dialogues.mp3
start dialogues-mp3\master_dialogues.mp3
```

`start` opens it in your default player. Scrub across a join to confirm the
audio is continuous and the reported duration is right.

A healthy run prints something like:

```
Usable   : 14
  from human_gen       : 4
  from machine_gen     : 10
Already in target format : 10
Reused from cache        : 6
Format   : MPEG-2 Layer 3 24000 Hz mono 128 kbps (consistent across 16 file(s))
Selected     : ffmpeg
Warnings    : 140
Run log     : ...\log\run20260911-194904.log
```

The second run should report the transcodes as `Reused from cache`, doing no
FFmpeg work at all.

Every run also writes `log/run<timestamp>.log` — see §0.10.

### 0.10 Reading the run log

Each invocation of `stitch_mp3.py` writes a timestamped log under `log/`,
containing the full console transcript, a per-row source manifest, and every
warning. It is written even when the run aborts, so a failed run still leaves a
complete record.

**Which file was used for each row** is recorded in a `ROW SOURCES` table. This
is log-only: at 154 rows it would swamp the console, but it is exactly what you
want when auditing which take ended up in the master.

```
ROW SOURCES
======================================================================
  seq  source        file                                status
----------------------------------------------------------------------
    0  machine_gen   0-SAMAY.mp3                         ok
    1  machine_gen   1-DURYODHANA.mp3                    ok
   10  human_gen     10_YUDHISHTHIRA.m4a                 ok
   11  -             11-DURYODHANA.mp3                   MISSING
   16  human_gen     16_YUDHISHTHIRA.m4a                 ok
----------------------------------------------------------------------
  not found      140
  human_gen      4
  machine_gen    10
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

Three kinds of warning are recorded:

- **`no audio for row N / <file> - searched human_gen, machine_gen`** — the
  sequence number resolved in neither directory. This is the expected state
  while audio is still incomplete.
- **`filler_before "<name>" not found ... used short_filler.mp3`** — a named
  filler is absent, so the row fell back to plain silence.
- **`could not convert <file> to the target format`** — usually FFmpeg missing.

### 0.11 Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ffmpeg: command not found` after installing | Terminal predates the PATH change. Restart the terminal, or VS Code entirely. |
| `ffprobe` missing but `ffmpeg` works | An FFmpeg package that aliases only `ffmpeg`. Install `Gyan.FFmpeg`. |
| `CONVERSION FAILED ... ffmpeg was not found on PATH` | An `.m4a` or off-format input needs transcoding. Install FFmpeg (§0.3). |
| `SSLError: certificate verify failed` | Corporate TLS proxy. `pip install truststore` (§0.5). |
| `Failed to retrieve folder contents ... (status code 404)` | Drive folder is not link-shared, or the link points at a file. |
| `ERROR: gdown is not installed` | `pip install --user gdown truststore`. |
| Aborts with a long `MISSING (...)` list | Expected when audio is incomplete. Fetch the rest, or use `strict=0`. |
| A row picks the wrong take | Both directories hold that sequence number. `human_gen/` always wins — delete or rename the copy you do not want. The `MULTIPLE FILES PER SEQUENCE` block names both, and `ROW SOURCES` in the log shows what every row used. |
| Downloaded files are not being used | They must start with the sequence number. `17_Anything.m4a` works; `Yudhishthira17.m4a` does not. |
| Files placed in `dialogues-mp3/` are ignored | By design — it is output only. Put source audio in `human_gen/` or `machine_gen/`. |
| `FORMAT MISMATCH` after normalization | A transcode did not land on the target. Check `TARGET_SAMPLE_RATE` / `TARGET_BITRATE` in `audio_common.py`. |
| `NAMED FILLER NOT FOUND` | A `filler_before` file is absent from both directories. It falls back to silence under `strict=0`. |
| Python opens the Microsoft Store | Disable the `python.exe` App execution aliases (§0.2). |

### 0.12 Quick reference

```powershell
# one-time setup
winget install --id Gyan.FFmpeg --scope user     # then RESTART the terminal
python -m pip install --user gdown truststore

# every time
cd "C:\path\to\dice-skit"
python fetch_drive.py "<drive-folder-url>" dry=1   # preview  -> machine_gen/
python fetch_drive.py "<drive-folder-url>"         # download -> machine_gen/
python stitch_mp3.py                               # strict; or strict=0
Get-Content (Get-ChildItem log\*.log | Sort LastWriteTime -Desc)[0].FullName -Tail 40
```

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

---

## 6. The sample-rate mismatch this study uncovered

`samay-1.mp3` is **22 050 Hz**. Every dialogue file is **24 000 Hz**.

The original `stitch_aud.py` prepended the intro unconditionally and joined it
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

## 7. Mixing `.m4a` with `.mp3` (implemented)

The pipeline now receives some dialogue as `.m4a`. Per §1 none of the
byte-level work transfers, and the constraint is sharper than "m4a needs a
muxer": the output is an **MP3**, and AAC cannot be carried inside an MP3
stream at all. There is no stream-copy path from `.m4a` to `.mp3`. The audio
must be decoded and re-encoded.

So the pipeline gained a **normalization stage** (`concat_backends.normalize_input`)
that runs before stitching:

```
input already in target format?  -> pass through untouched (stream copy)
otherwise                        -> transcode once into _normalized/, cache it
```

The target is a single canonical format, `TARGET_SAMPLE_RATE` / `TARGET_BITRATE`
in `audio_common.py`, set to **24 kHz mono 128 kbps** — the Sarvam TTS format,
which is the bulk of the material. Choosing the majority format means most files
need no transcode and keep the lossless path; only the outliers are re-encoded.
Targeting the `.m4a` files' 44.1 kHz instead would have forced a re-encode of
every MP3, upsampling 24 kHz to 44.1 kHz for no information gain while adding a
fresh generation of lossy loss to all of them.

Measured on the current input set:

| Input | Source format | Action |
|---|---|---|
| 9 dialogue MP3s | MPEG-2 L3 24 kHz mono 128 k | pass-through, stream copy |
| `10_YUDHISHTHIRA.m4a` | AAC-LC 44.1 kHz mono | transcoded |
| `129_Krishna.m4a` | AAC-LC 44.1 kHz mono | transcoded |
| `131_Krishna.m4a` | AAC-LC 44.1 kHz mono | transcoded |
| `samay-1.mp3` (intro) | MPEG-2 L3 **22.05 kHz** mono | transcoded |

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

### Filename matching

The `.m4a` files arrived as `10_YUDHISHTHIRA.m4a` and `129_Krishna.m4a` —
underscore separator and mixed case, against the MP3 convention's
`10-YUDHISHTHIRA.mp3` and the CSV's uppercase `KRISHNA`. Rather than require
renaming, `find_dialogue_file()` matches on the **leading sequence number
alone**, tolerating separator, case and extension. Deviations are reported so
drift stays visible:

```
MATCHED BY SEQUENCE NUMBER (4) - filename differs from the CSV convention:
    129-KRISHNA.mp3              <- 129_Krishna.m4a
```

When two files claim the same sequence number, the most recently modified wins,
on the assumption that a newly delivered file supersedes an older one.

Sequence numbers start at **0**, which is the opening `SAMAY` line. Nothing
special-cases it — `0-SAMAY.mp3` is found by the same numeric match as any
other row.

### Two source directories

Audio arrives from two places, and `dialogues-mp3/` is **not** one of them — it
holds only the finished master, nothing else:

| Directory | Role | Written by |
|---|---|---|
| `human_gen/` | Source, precedence 1. Human-recorded takes, plus named filler audio | `fetch_drive.py` |
| `machine_gen/` | Source, precedence 2. Sarvam TTS output | `generate_mp3.py` |
| `work/` | Derived: `short_filler.mp3` and `normalized/` transcode cache | the pipeline |
| `dialogues-mp3/` | **Output only** — `master_dialogues.mp3` and nothing else | `stitch_mp3.py` |

Keeping the output directory free of inputs means it can be deleted and rebuilt
without losing source material, and there is never any doubt about which files
are products and which are ingredients.

**Directory precedence beats modification time.** `human_gen/` wins outright: a
real recording is never displaced by synthesised speech, however new the
synthesised file is. Modification time only breaks ties *within* one directory.
Both rules are reported:

```
MULTIPLE FILES PER SEQUENCE (1) - directory precedence, then newest:
    using 1-REC.mp3  (human_gen)
      ignoring 1-DURYODHANA.mp3  (machine_gen)
```

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

The gap before each line is driven by the CSV's **`filler_before`** column
rather than by a single global filler. Each row contributes its filler followed
by its dialogue:

```
filler_before(row 0)   new-mahabharata-instrumental.m4a   21.096 s
dialogue row 0         0-SAMAY.mp3                        61.512 s
filler_before(row 1)   empty -> short_filler.mp3           2.064 s
dialogue row 1         1-DURYODHANA.mp3                    6.888 s
...
```

An empty cell means the default 2-second silence. Because fillers precede their
row, there is no trailing gap after the final line, and **the opening music is
just row 0's filler** — the earlier dedicated intro setting (`INTRO_FILE`) was
removed rather than kept alongside it.

Three consequences worth noting:

- **Filler audio is normalized like anything else.** The opening instrumental
  is 44.1 kHz *stereo* AAC, so it is decoded, downmixed to mono and resampled
  to 24 kHz by the same path as the `.m4a` dialogue. `0-SAMAY.mp3` itself is
  48 kHz and gets resampled too.
- **Each distinct source is converted once.** `short_filler.mp3` is referenced
  by most rows but appears once in the transcode map, so a 150-row run does not
  re-convert it 150 times.
- **A named filler that is absent degrades to the default gap** and is
  reported, rather than failing the run. Under `strict=1` it still counts as a
  problem and aborts; under `strict=0` the run continues with silence in its
  place:

```
NAMED FILLER NOT FOUND (1) - falling back to short_filler.mp3:
    row 19 / 17-YUDHISHTHIRA.mp3       wanted dice.mp3
```

Filler audio lives in the same directory as the dialogue but carries no leading
sequence number, which is what keeps it out of the dialogue set: both discovery
paths require a number, so `dice.mp3` and
`new-mahabharata-instrumental.m4a` are never mistaken for lines.

Verified on the current 14-row set: 28 segments, and the master's duration
matches the summed segments to **0.0 ms**.

### If the output format ever needs to be `.m4a` too

| Option | Dependency | Re-encodes | Assessment |
|---|---|---|---|
| **FFmpeg concat demuxer** | `ffmpeg` | no | `FfmpegBackend.supports()` already returns True for any format; set `OUTPUT_FORMAT = "m4a"`. Requires identical codec parameters across inputs. **Recommended.** |
| **MP4Box `-cat`** | GPAC | no | Purpose-built for MP4 concatenation, tolerates some mismatches FFmpeg refuses. A second external binary for a narrow gain. |
| **Media Foundation** | none (ships with Windows) | optional | `IMFSourceReader` → `IMFSinkWriter` with `MFCreateMPEG4MediaSink`; AAC-LC encoder is native (`D/E`). Costs significant `comtypes` COM plumbing. |
| **Pure Python MP4 muxer** | none | no | No mature library rewrites sample tables across concatenated inputs. Not recommended. |

---

## Sources

- [FFmpeg concat demuxer](https://ffmpeg.org/ffmpeg-formats.html#concat-1) — stream-copy requirements, `auto_convert`, duration handling
- [Supported codecs (Windows)](https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/supported-codecs) — the `D`/`E` audio table showing native MP3 and AAC-LC *encoders* on desktop
- [MediaClip](https://learn.microsoft.com/en-us/uwp/api/windows.media.editing.mediaclip) — "Creates a video clip from a video file"; video-only surface
- [MediaComposition](https://learn.microsoft.com/en-us/uwp/api/windows.media.editing.mediacomposition) — `Clips` vs `BackgroundAudioTracks`, `RenderToFileAsync`
- [MediaEncodingProfile](https://learn.microsoft.com/en-us/uwp/api/windows.media.mediaproperties.mediaencodingprofile) — `CreateMp3` / `CreateM4a`, and the warning that a profile may be created for a codec the device lacks
- [python-soundfile](https://python-soundfile.readthedocs.io/en/latest/) — MP3 write from 0.11.0 / libsndfile 1.1.0; no AAC
- [pydub](https://github.com/jiaaro/pydub) — WAV native, everything else via FFmpeg/libav
- [PyWinRT](https://pywinrt.readthedocs.io/en/latest/types.html) — `winrt-<Namespace>` package naming, awaitable handling
