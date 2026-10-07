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
import re
import subprocess
from pathlib import Path

import audio_common as common
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


# ============================================================
# CUE STRINGS
# ============================================================


def parse_cues(text):
    """
    Parse a cue string into (together, segments).

    `segments` is a list of (track, start, end) in seconds, where track is "m"
    or "b". The last segment's end is None: it runs to the end of the mix, and
    its written duration is accepted but not enforced.

    Raises CueError naming the problem.
    """
    tokens = [token.strip() for token in str(text or "").lower().split("-")]

    if not tokens or tokens == [""]:
        raise CueError("cue string is empty")

    together = tokens[0] == "t"

    if together:
        tokens = tokens[1:]

    if "t" in tokens:
        raise CueError(f'"t" may only appear first, in "{text}"')

    if not tokens:
        raise CueError(f'"{text}" has no segments after "t"')

    if len(tokens) % 2:
        raise CueError(f'"{text}" must alternate letter and duration, e.g. m-2s-b-10s')

    segments = []
    start = 0.0
    previous = None

    for index in range(0, len(tokens), 2):
        track = tokens[index]
        length = tokens[index + 1]

        if track not in ("m", "b"):
            raise CueError(f'"{track}" in "{text}" is not a track; use m or b')

        if track == previous:
            raise CueError(f'"{text}" repeats "{track}"; letters must alternate')

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

    last_track, last_start, _ = segments[-1]
    segments[-1] = (last_track, last_start, None)

    return together, segments


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


def build_schedule(together, segments, main_length, background_length):
    """
    Work out when each track plays and how loud, from the cue segments and the
    two track lengths.

    Returns a dict:

        main_start, background_start   offsets in the mix, seconds
        main_end, background_end       where each track runs out
        total                          length of the mix
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
    total = max(main_end, background_end)

    ends = {"m": main_end, "b": background_end}

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
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


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


def mix(main_path, background_path, cues):
    """
    Produce the mixed file for one row, reusing the cache when it is fresh.

    Returns a dict with: path, action ("cached" or "mixed"), together, total,
    match_db, warnings, segments.

    Raises CueError for a bad cue string and MixError for anything else that
    stops the mix.
    """
    together, segments = parse_cues(cues)

    if not common.have_ffmpeg():
        raise MixError(
            "mixing a background score needs FFmpeg, which was not found on " "PATH"
        )

    main_path = Path(main_path)
    background_path = Path(background_path)
    target = mixed_path_for(main_path, background_path, cues)

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

    schedule = build_schedule(together, segments, main_length, background_length)

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

    _render(main_path, background_path, schedule, match_db, target)

    meta = {
        "together": together,
        "total": round(probe_duration(target), 3),
        "match_db": round(match_db, 2),
        "warnings": schedule["warnings"],
        "main_start": schedule["main_start"],
        "background_start": schedule["background_start"],
    }

    _meta_path(target).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    meta.update(path=target, action="mixed", segments=segments)

    return meta


def _render(main_path, background_path, schedule, match_db, target):
    """
    Run the FFmpeg mix and install the result at `target`.
    """
    rate = common.TARGET_SAMPLE_RATE

    def chain(index, start, levels, pre_gain_db):
        steps = [
            f"aresample={rate}",
            "aformat=sample_fmts=flt:channel_layouts=mono",
        ]

        if abs(pre_gain_db) > 0.005:
            steps.append(f"volume={pre_gain_db:.2f}dB")

        delay_ms = int(round(start * 1000))

        if delay_ms:
            steps.append(f"adelay={delay_ms}")

        steps.append(f"volume='{gain_expression(levels)}':eval=frame")

        return f"[{index}:a]" + ",".join(steps)

    graph = (
        chain(0, schedule["main_start"], schedule["main_levels"], 0.0)
        + "[m];"
        + chain(
            1,
            schedule["background_start"],
            schedule["background_levels"],
            match_db,
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
            "-i",
            str(background_path),
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


def describe_cues(together, segments):
    """
    One-line human reading of a parsed cue string, for the report.
    """
    parts = []

    for index, (track, start, end) in enumerate(segments):
        span = f"{start:g}-{'end' if end is None else f'{end:g}'}s"

        if index == 0 and not together:
            parts.append(f"{span} {_name(track)} solo")
        else:
            parts.append(f"{span} {_name(track)} up")

    return ("together: " if together else "") + ", ".join(parts)
