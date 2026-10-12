"""
Background score: overlay a background track on one row's dialogue.

Specified in section 10 of docs/audio-concat-study.md. A row names a background
file in its "background_score" column and a cue string in "score_cues":

    b-2s-m-10s-b-5s-m-2s      staggered start
    t-m-5s-b-10s-m-2s         together start

`m` is the row's dialogue, `b` the background. Letters and durations alternate,
and the same letter may not appear twice in a row.

Staggered (no leading `t`): the first letter's track plays alone for its
segment, with the other silent. When that segment ends the other track starts
from its own beginning, and every later letter names the prominent track.

Together (leading `t`): both tracks start at 0 s, and every letter, the first
included, names the prominent track.

The prominent track plays at full volume and the other is turned down by
SCORE_DUCK_DB, never muted, with a SCORE_RAMP_SECONDS linear ramp at every
change. The last segment runs to the end of the mix, whose length is the longer
of the two tracks once the staggered delay is applied. Nothing is trimmed. Once
the prominent track has ended, the other ramps back to full volume and a
warning is recorded.

The mix is written straight in the target format, so normalization passes it
through untouched. Mixing needs FFmpeg: it decodes and sums two signals, which
the frame splicer cannot do.
"""

import hashlib
import json
import math
import re
from pathlib import Path

import audio_common as common
import audio_tools
from concat_backends import atomic_install, temp_path_for


class CueError(ValueError):
    """
    Raised for a malformed cue string, or a background without one.
    """


class MixError(RuntimeError):
    """
    Raised when a mix cannot be produced: a missing tool or a failed FFmpeg run.
    """


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")

# A looped background repeating more than this many times usually means the
# wrong file was named (e.g. a 2-second sound under a minute-long line).
LOOP_WARN_REPEATS = 10


class Cues:
    """
    A parsed cue string.

    Unpacks as (together, segments), the shape parse_cues() returned before
    loop support, so existing callers keep working.
    """

    def __init__(self, together, segments, loop=False, open_end=False, cut=False):
        self.together = together
        self.segments = segments
        self.loop = loop
        # True when the last segment was written with no duration (t-m): the
        # writer said "until the end", so ending early inside it is intended.
        self.open_end = open_end
        # True only for exactly "t-m-loop": a longer background is cut at the
        # line's end, so the mix is always as long as the line.
        self.cut = cut

    def __iter__(self):
        return iter((self.together, self.segments))

    def __repr__(self):
        return (
            f"Cues(together={self.together}, segments={self.segments}, "
            f"loop={self.loop}, open_end={self.open_end}, cut={self.cut})"
        )


# ============================================================
# CUE STRINGS
# ============================================================

def parse_cues(text):
    """
    Parse a cue string into a Cues(together, segments, loop).

    `segments` is a list of (track, start, end) in seconds, where track is "m"
    or "b". The last segment's end is None: it runs to the end of the mix. Its
    duration may be written but is not enforced, and may be left off (t-m).

    `loop` is True when the string ends in "-loop": the background then
    repeats from its own start until the dialogue ends.

    Raises CueError naming the problem.
    """
    tokens = [token.strip() for token in str(text or "").lower().split("-")]

    if not tokens or tokens == [""]:
        raise CueError("cue string is empty")

    # Exactly "t-m-loop" (any case) also cuts a background that is longer than
    # the line, so that mix always lasts exactly as long as the line. Other
    # -loop strings let a longer background play on (decided 2026-10-07).
    cut = tokens == ["t", "m", "loop"]

    loop = tokens[-1] == "loop"

    if loop:
        tokens = tokens[:-1]

    if "loop" in tokens:
        raise CueError(f'"loop" may only appear last, in "{text}"')

    together = bool(tokens) and tokens[0] == "t"

    if together:
        tokens = tokens[1:]

    if "t" in tokens:
        raise CueError(f'"t" may only appear first, in "{text}"')

    if not tokens:
        raise CueError(f'"{text}" has no segments')

    # The final duration is optional: an odd count means it was left off.
    if len(tokens) % 2:
        tokens.append(None)

    segments = []
    start = 0.0
    previous = None
    last = len(tokens) - 2
    open_end = False

    for index in range(0, len(tokens), 2):
        track = tokens[index]
        length = tokens[index + 1]

        if track not in ("m", "b"):
            raise CueError(
                f'"{track}" in "{text}" is not a track; use m or b'
            )

        if track == previous:
            raise CueError(
                f'"{text}" repeats "{track}"; letters must alternate'
            )

        if length is None:
            if index != last:
                raise CueError(
                    f'"{text}" must alternate letter and duration, e.g. m-2s-b-10s'
                )

            # Only the last segment may omit its duration; it runs to the end.
            segments.append((track, start, None))
            previous = track
            open_end = True
            break

        match = _DURATION.match(length)

        if not match:
            raise CueError(
                f'"{length}" in "{text}" is not a duration; use e.g. 2s or 2.5s'
            )

        seconds = float(match.group(1))

        if seconds <= 0:
            raise CueError(f'"{length}" in "{text}" must be longer than 0s')

        segments.append((track, start, start + seconds))
        start += seconds
        previous = track

    if not together and len(segments) == 1:
        # A staggered start needs a second segment: the first is solo, and
        # the other track enters only when it ends.
        if segments[0][2] is None:
            raise CueError(
                f'"{text}" has a single open-ended segment; add "t-" to start '
                "both tracks together, or a second segment"
            )

    last_track, last_start, _ = segments[-1]
    segments[-1] = (last_track, last_start, None)

    return Cues(together, segments, loop, open_end, cut)


def score_request(record):
    """
    Read a record's background-score columns.

    Returns None when the row is not mixed, else (background_name, cue_text).
    Raises CueError when only one of the two columns is set.
    """
    name = record.get("background_score")
    cues = record.get("score_cues")

    if not name and not cues:
        return None

    if not name:
        raise CueError(
            f'{common.SCORE_CUES_COLUMN} is set ("{cues}") but '
            f"{common.SCORE_COLUMN} is empty"
        )

    if not cues:
        raise CueError(
            f'{common.SCORE_COLUMN} is set ("{name}") but '
            f"{common.SCORE_CUES_COLUMN} is empty"
        )

    return name, cues


# ============================================================
# SCHEDULE
# ============================================================

def build_schedule(together, segments, main_length, background_length, loop=False,
                   open_end=False, cut=False):
    """
    Work out when each track plays and how loud, from the cue segments and the
    two track lengths.

    With `loop`, a background shorter than the dialogue repeats from its own
    start until the dialogue ends, so the mix ends with the dialogue. A
    background already long enough is not looped and plays to its own end, as
    without `loop`, unless `cut` is set.

    `cut` (exactly "t-m-loop") also cuts a background longer than the dialogue
    at the dialogue's end, so the mix is always exactly as long as the line.

    `open_end` means the last segment was written with no duration ("until
    the end"), so a track running out inside it is intended and not reported.
    The behaviour itself is unchanged: the other track still comes back up.

    Returns a dict:

        main_start, background_start   offsets in the mix, seconds
        main_end, background_end       where each track runs out
        total                          length of the mix
        loops                          how many times the background plays;
                                       1 when it is not looped
        cut_at                         where a longer background is cut, in
                                       mix time; None when it is not cut
        main_levels, background_levels lists of (time, gain), time-ordered,
                                       the first at 0
        warnings                       plain-text messages for the run log
    """
    duck = 10 ** (common.SCORE_DUCK_DB / 20)
    warnings = []

    first = segments[0][0]
    delay = 0.0 if together else segments[0][2]

    main_start = delay if (not together and first == "b") else 0.0
    background_start = delay if (not together and first == "m") else 0.0

    main_end = main_start + main_length
    background_end = background_start + background_length
    loops = 1

    if loop and background_end < main_end:
        background_end = main_end
        loops = math.ceil((main_end - background_start) / background_length)

        if loops > LOOP_WARN_REPEATS:
            warnings.append(
                f"the {background_length:.2f}s background repeats {loops} "
                f"times to cover the {main_length:.2f}s dialogue; check the "
                "right file is named"
            )

    # Only "t-m-loop" cuts a longer background, so its mix is exactly as long
    # as the line (decided 2026-10-07).
    cut_at = None

    if cut and background_end > main_end:
        cut_at = main_end
        background_end = main_end

    total = max(main_end, background_end)

    ends = {"m": main_end, "b": background_end}

    # Ending early inside the last segment is intended when that segment was
    # written open-ended, or when the background was asked to loop.
    quiet_last = open_end or loop

    if not together:
        solo_end = segments[0][2]

        if ends[first] < solo_end:
            warnings.append(
                f"the solo {_name(first)} ends at {ends[first]:.2f}s, before "
                f"its segment ends at {solo_end:g}s; silence until the "
                f"{_name(_other(first))} starts"
            )

    # The first segment of a staggered mix is solo, so both tracks sit at full
    # volume there: only one is actually sounding.
    scheduled = segments if together else segments[1:]

    main_levels = []
    background_levels = []

    for track, start, end in scheduled:
        # Clamped to the mix: a cue string longer than the audio is harmless.
        if start >= total:
            warnings.append(
                f"the cue at {start:g}s starts after the mix ends at "
                f"{total:.2f}s and is ignored"
            )
            break

        lead_end = ends[track]

        if lead_end <= start:
            # The prominent track has already run out: keep the other one up.
            warnings.append(
                f"{_name(track)} is prominent from {start:g}s but ended at "
                f"{lead_end:.2f}s; the {_name(_other(track))} stays at full "
                "volume"
            )
            main_levels.append((start, 1.0))
            background_levels.append((start, 1.0))
            continue

        main_levels.append((start, 1.0 if track == "m" else duck))
        background_levels.append((start, 1.0 if track == "b" else duck))

        segment_end = total if end is None else min(end, total)

        if lead_end < segment_end:
            # The behaviour never changes: the other track comes back up.
            # Only the report differs, when the writer asked for this.
            if not (end is None and quiet_last):
                warnings.append(
                    f"{_name(track)} ends at {lead_end:.2f}s, inside its "
                    f"prominent segment from {start:g}s; the "
                    f"{_name(_other(track))} ramps back to full volume"
                )

            if track == "m":
                background_levels.append((lead_end, 1.0))
            else:
                main_levels.append((lead_end, 1.0))

    if not main_levels:
        main_levels = [(0.0, 1.0)]
        background_levels = [(0.0, 1.0)]

    if together:
        # The first cue's gains apply from the first instant.
        main_levels[0] = (0.0, main_levels[0][1])
        background_levels[0] = (0.0, background_levels[0][1])
    else:
        # The solo track plays at full volume until the second track enters,
        # when the first scheduled cue takes over. The entering track is
        # silent until then anyway, so it can carry that cue's gain from 0 s.
        solo_levels = main_levels if first == "m" else background_levels
        entering_levels = background_levels if first == "m" else main_levels

        solo_levels.insert(0, (0.0, 1.0))
        entering_levels[0] = (0.0, entering_levels[0][1])

    return {
        "main_start": main_start,
        "background_start": background_start,
        "main_end": main_end,
        "background_end": background_end,
        "total": total,
        "loops": loops,
        "cut_at": cut_at,
        "main_levels": main_levels,
        "background_levels": background_levels,
        "warnings": warnings,
    }


def _name(track):
    return "dialogue" if track == "m" else "background"


def _other(track):
    return "b" if track == "m" else "m"


def gain_expression(levels):
    """
    An FFmpeg volume expression in `t` for a list of (time, gain) switch points.

    Each change of gain becomes a linear ramp of SCORE_RAMP_SECONDS starting at
    the switch time, so there are no hard volume steps.
    """
    ramp = common.SCORE_RAMP_SECONDS
    previous = levels[0][1]
    expression = f"{previous:.6f}"

    for at, gain in levels[1:]:
        if abs(gain - previous) < 1e-9:
            continue

        expression = (
            f"if(lt(t,{at:.3f}),{expression},"
            f"if(lt(t,{at + ramp:.3f}),"
            f"{previous:.6f}+({gain - previous:.6f})*(t-{at:.3f})/{ramp},"
            f"{gain:.6f}))"
        )
        previous = gain

    return expression


# ============================================================
# MEASUREMENT
# ============================================================

def _run(command):
    return audio_tools.run(command)


def probe_duration(path):
    """
    Duration of an audio file in seconds, via ffprobe.
    """
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )

    try:
        return float(result.stdout.strip())
    except ValueError:
        raise MixError(
            f"could not read the duration of {Path(path).name}: "
            f"{result.stderr.strip() or 'no output from ffprobe'}"
        )


_LUFS = re.compile(r"I:\s+(-?\d+(?:\.\d+)?) LUFS")


def integrated_loudness(path):
    """
    Integrated loudness (LUFS) of a file once folded to the target mono rate,
    which is how it will sound in the mix. None when it cannot be measured,
    e.g. a file that is entirely silent.
    """
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"aresample={common.TARGET_SAMPLE_RATE},"
            "aformat=sample_fmts=flt:channel_layouts=mono,ebur128",
            "-f",
            "null",
            "-",
        ]
    )

    values = _LUFS.findall(result.stderr)

    if not values:
        return None

    value = float(values[-1])

    # ebur128 reports -70 LUFS for silence; matching against that is useless.
    return value if value > -69.0 else None


# ============================================================
# MIXING
# ============================================================

def mixed_path_for(main_path, background_path, cues):
    """
    Cache path for one mix.

    The name carries the dialogue's stem for readability, plus a short hash of
    everything that shapes the result, so a change to the cue string, the
    background or the level settings never reuses a stale mix.
    """
    key = json.dumps(
        {
            "main": str(Path(main_path).resolve()),
            "background": str(Path(background_path).resolve()),
            "cues": cues.strip().lower(),
            "duck": common.SCORE_DUCK_DB,
            "ramp": common.SCORE_RAMP_SECONDS,
            "match": common.SCORE_MATCH_LOUDNESS,
            "limit": common.SCORE_LIMIT,
            "rate": common.TARGET_SAMPLE_RATE,
            "bitrate": common.TARGET_BITRATE,
        },
        sort_keys=True,
    )

    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]

    return common.MIXED_DIRECTORY / f"{Path(main_path).stem}.{digest}.mp3"


def _meta_path(mixed):
    return mixed.with_suffix(".json")


def _is_fresh(mixed, sources):
    """
    True when a cached mix is newer than both sources and still decodes.
    """
    if not mixed.exists() or mixed.stat().st_size == 0:
        return False

    newest = max(Path(source).stat().st_mtime for source in sources)

    if mixed.stat().st_mtime < newest:
        return False

    if not _meta_path(mixed).exists():
        return False

    return common.is_valid_audio(mixed, verbose=False)


def mix(main_path, background_path, cue_text):
    """
    Produce the mixed file for one row, reusing the cache when it is fresh.

    Returns a dict with: path, action ("cached" or "mixed"), together, loop,
    loops, total, match_db, warnings, segments.

    Raises CueError for a bad cue string and MixError for anything else that
    stops the mix.
    """
    cues = parse_cues(cue_text)
    together, segments = cues

    if not common.have_ffmpeg():
        raise MixError(
            "mixing a background score needs FFmpeg, which was not found on "
            "PATH"
        )

    main_path = Path(main_path)
    background_path = Path(background_path)
    target = mixed_path_for(main_path, background_path, cue_text)

    if _is_fresh(target, (main_path, background_path)):
        try:
            meta = json.loads(_meta_path(target).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = None

        if meta is not None:
            meta.update(path=target, action="cached", segments=segments)

            return meta

    main_length = probe_duration(main_path)
    background_length = probe_duration(background_path)

    schedule = build_schedule(
        together,
        segments,
        main_length,
        background_length,
        loop=cues.loop,
        open_end=cues.open_end,
        cut=cues.cut,
    )

    match_db = 0.0

    if common.SCORE_MATCH_LOUDNESS:
        main_lufs = integrated_loudness(main_path)
        background_lufs = integrated_loudness(background_path)

        if main_lufs is not None and background_lufs is not None:
            match_db = main_lufs - background_lufs
        else:
            schedule["warnings"].append(
                "loudness could not be measured, so the background was not "
                "matched to the dialogue"
            )

    _render(main_path, background_path, schedule, match_db, target,
            loop_length=background_length if schedule["loops"] > 1 else None)

    meta = {
        "together": together,
        "loop": cues.loop,
        "loops": schedule["loops"],
        "cut_at": schedule["cut_at"],
        "total": round(probe_duration(target), 3),
        "match_db": round(match_db, 2),
        "warnings": schedule["warnings"],
        "main_start": schedule["main_start"],
        "background_start": schedule["background_start"],
    }

    _meta_path(target).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    meta.update(path=target, action="mixed", segments=segments)

    return meta


def _render(main_path, background_path, schedule, match_db, target,
            loop_length=None):
    """
    Run the FFmpeg mix and install the result at `target`.

    `loop_length` is the background's own length when it must repeat. The
    repeats are made by FFmpeg (-stream_loop) and the background is cut at
    the dialogue's end. Each seam gets a short fade out and back in, of
    SCORE_RAMP_SECONDS, so music that does not loop cleanly does not click.
    """
    rate = common.TARGET_SAMPLE_RATE

    def chain(index, start, levels, pre_gain_db, seams=None, length=None, fade=None):
        steps = [
            f"aresample={rate}",
            "aformat=sample_fmts=flt:channel_layouts=mono",
        ]

        if abs(pre_gain_db) > 0.005:
            steps.append(f"volume={pre_gain_db:.2f}dB")

        if seams:
            steps.append(f"volume='{_seam_expression(seams)}':eval=frame")

        if length is not None:
            steps.append(f"atrim=duration={length:.3f}")
            steps.append("asetpts=PTS-STARTPTS")

        if fade:
            steps.append(fade)

        delay_ms = int(round(start * 1000))

        if delay_ms:
            steps.append(f"adelay={delay_ms}")

        steps.append(f"volume='{gain_expression(levels)}':eval=frame")

        return f"[{index}:a]" + ",".join(steps)

    background_inputs = ["-i", str(background_path)]
    seams = None
    length = None

    if loop_length:
        background_inputs = ["-stream_loop", "-1", *background_inputs]
        length = schedule["background_end"] - schedule["background_start"]
        seams = [loop_length * n for n in range(1, schedule["loops"])]

    if schedule.get("cut_at") is not None:
        # A longer background cut at the line's end. It fades out over the
        # ramp length up to the cut, so it doesn't stop abruptly.
        length = schedule["cut_at"] - schedule["background_start"]
        ramp = common.SCORE_RAMP_SECONDS
        seams = None
        fade_out = f"afade=t=out:st={max(0.0, length - ramp):.3f}:d={min(ramp, length):.3f}"
    else:
        fade_out = None

    graph = (
        chain(0, schedule["main_start"], schedule["main_levels"], 0.0)
        + "[m];"
        + chain(
            1,
            schedule["background_start"],
            schedule["background_levels"],
            match_db,
            seams=seams,
            length=length,
            fade=fade_out,
        )
        + "[b];"
        # level=disabled: the limiter's auto-level otherwise re-raises the
        # whole mix and undoes the loudness match.
        f"[m][b]amix=inputs=2:duration=longest:normalize=0,"
        f"alimiter=limit={common.SCORE_LIMIT}:level=disabled[out]"
    )

    target.parent.mkdir(parents=True, exist_ok=True)

    temp_file = temp_path_for(target, "mix")

    if temp_file.exists():
        temp_file.unlink()

    result = _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(main_path),
            *background_inputs,
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-map_metadata",
            "-1",
            "-ar",
            str(rate),
            "-ac",
            "1" if common.TARGET_MONO else "2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{common.TARGET_BITRATE // 1000}k",
            "-write_xing",
            "1",
            str(temp_file),
        ]
    )

    if result.returncode != 0:
        if temp_file.exists():
            temp_file.unlink()

        last = result.stderr.strip().splitlines()[-1:] or ["unknown error"]

        raise MixError(f"FFmpeg could not mix {main_path.name}: {last[0]}")

    atomic_install(temp_file, target)


def _seam_expression(seams):
    """
    A volume expression in `t` that dips to silence and back at each loop
    seam: a linear fade out over SCORE_RAMP_SECONDS before the seam and a fade
    in over the same time after it. 1.0 everywhere else.

    Written as one minimum over the seams, so a long loop does not nest
    hundreds of conditions.
    """
    ramp = common.SCORE_RAMP_SECONDS

    # Distance to the nearest seam, scaled so it reaches 1 at `ramp` away.
    terms = [f"min(1,abs(t-{s:.3f})/{ramp})" for s in seams]

    expression = terms[0]

    for term in terms[1:]:
        expression = f"min({expression},{term})"

    return expression


def describe_cues(together, segments, loop=False):
    """
    One-line human reading of a parsed cue string, for the report.

    Describes what the string asks for. The caller adds what actually happened
    (how many times the background played, or that it was cut).
    """
    parts = []

    for index, (track, start, end) in enumerate(segments):
        span = f"{start:g}-{'end' if end is None else f'{end:g}'}s"

        if index == 0 and not together:
            parts.append(f"{span} {_name(track)} solo")
        else:
            parts.append(f"{span} {_name(track)} up")

    return ("together: " if together else "") + ", ".join(parts)