"""
Dialogue loudness: bring every line to one integrated loudness.

Specified in section 11 of docs/audio-concat-study.md. Each dialogue file is
measured (EBU R128 integrated loudness and true peak, after folding to the
target mono rate) and then given ONE fixed gain to reach
DIALOGUE_TARGET_LUFS. A single gain per line keeps the line's own rises and
falls; re-adjusting the volume continuously would flatten the acting.

A true-peak limiter at DIALOGUE_TRUE_PEAK_DBFS acts only where the gain would
push a peak past it, so boosted quiet lines cannot clip.

Dialogue only. Fillers, sound effects and background scores keep their own
levels. The result is written straight in the target format, so
normalization passes it through and the background-score mix (section 10)
works on dialogue that is already at the target level.

Needs FFmpeg.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import audio_common as common
from concat_backends import atomic_install, temp_path_for


class LoudnessError(RuntimeError):
    """
    Raised when a line cannot be measured or adjusted.
    """


# Integrated loudness needs some speech to measure. Below this the value is
# unreliable and the line is reported, though still adjusted.
SHORT_LINE_SECONDS = 1.0

# Gains this close to zero are not worth a re-encode.
NEGLIGIBLE_GAIN_DB = 0.1

_INTEGRATED = re.compile(r"I:\s+(-?\d+(?:\.\d+)?) LUFS")
_TRUE_PEAK = re.compile(r"Peak:\s+(-?inf|-?\d+(?:\.\d+)?) dBFS")
_DURATION = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _run(command):
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _fold():
    layout = "mono" if common.TARGET_MONO else "stereo"

    return (
        f"aresample={common.TARGET_SAMPLE_RATE},"
        f"aformat=sample_fmts=flt:channel_layouts={layout}"
    )


def measure(path):
    """
    Measure a file as it will be heard in the master.

    Returns (integrated_lufs, true_peak_dbfs, seconds). Raises LoudnessError
    when the file is silent or cannot be decoded.
    """
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"{_fold()},ebur128=peak=true",
            "-f",
            "null",
            "-",
        ]
    )

    integrated = _INTEGRATED.findall(result.stderr)
    peaks = _TRUE_PEAK.findall(result.stderr)
    times = _DURATION.findall(result.stderr)

    if result.returncode != 0 or not integrated:
        last = result.stderr.strip().splitlines()[-1:] or ["no output"]

        raise LoudnessError(f"could not measure {Path(path).name}: {last[0]}")

    lufs = float(integrated[-1])

    # ebur128 reports -70 LUFS when everything was gated out as silence.
    if lufs <= -69.0:
        raise LoudnessError(f"{Path(path).name} is silent; nothing to measure")

    peak = float(peaks[-1]) if peaks and peaks[-1] != "-inf" else -99.0

    seconds = 0.0

    if times:
        hours, minutes, secs = times[-1]
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)

    return lufs, peak, seconds


def leveled_path_for(source):
    """
    Cache path for a loudness-matched copy of `source`.

    The hash covers the source path and every setting that shapes the result,
    so changing the target or the output format never reuses a stale file.
    """
    key = json.dumps(
        {
            "source": str(Path(source).resolve()),
            "target": common.DIALOGUE_TARGET_LUFS,
            "peak": common.DIALOGUE_TRUE_PEAK_DBFS,
            "rate": common.TARGET_SAMPLE_RATE,
            "bitrate": common.TARGET_BITRATE,
            "mono": common.TARGET_MONO,
        },
        sort_keys=True,
    )

    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]

    return common.LOUDNESS_DIRECTORY / f"{Path(source).stem}.{digest}.mp3"


def _meta_path(path):
    return path.with_suffix(".json")


def _cached(target, source):
    """
    The stored result for a fresh cache entry, or None.
    """
    meta_file = _meta_path(target)

    if not target.exists() or not meta_file.exists():
        return None

    if target.stat().st_mtime < Path(source).stat().st_mtime:
        return None

    if not common.is_valid_audio(target, verbose=False):
        return None

    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def level(source):
    """
    Bring one dialogue file to the target loudness, reusing the cache.

    Returns a dict:

        path        file to use from here on (the source itself when the gain
                    is negligible)
        action      "leveled", "cached" or "unchanged"
        measured    integrated loudness before, LUFS
        achieved    integrated loudness of the written file, LUFS (absent when
                    unchanged)
        peak        true peak before, dBFS
        gain        dB applied, including the one-pass encoder correction
        limited     dB the limiter must take off the loudest peak, 0 if none
        short       True when the line is too short to measure reliably

    Raises LoudnessError when the file cannot be measured or written.
    """
    source = Path(source)
    target = leveled_path_for(source)

    meta = _cached(target, source)

    if meta is not None:
        meta.update(path=target, action="cached")

        return meta

    lufs, peak, seconds = measure(source)

    gain = common.DIALOGUE_TARGET_LUFS - lufs
    limited = max(0.0, peak + gain - common.DIALOGUE_TRUE_PEAK_DBFS)

    meta = {
        "measured": round(lufs, 2),
        "peak": round(peak, 2),
        "gain": round(gain, 2),
        "limited": round(limited, 2),
        "short": seconds < SHORT_LINE_SECONDS,
    }

    if abs(gain) < NEGLIGIBLE_GAIN_DB and limited == 0:
        meta.update(path=source, action="unchanged")

        return meta

    _render(source, gain, target)

    # MP3 encoding itself shifts integrated loudness, measured at about
    # -0.4 dB on this material at every bitrate. Measure what was actually
    # written and correct once, so the stored file lands on the target.
    achieved, _, _ = measure(target)
    error = common.DIALOGUE_TARGET_LUFS - achieved

    if abs(error) >= NEGLIGIBLE_GAIN_DB:
        gain += error
        _render(source, gain, target)
        achieved, _, _ = measure(target)

    meta["gain"] = round(gain, 2)
    meta["achieved"] = round(achieved, 2)

    _meta_path(target).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    meta.update(path=target, action="leveled")

    return meta


def _render(source, gain, target):
    """
    Apply `gain` dB plus the true-peak limiter and write the target format.
    """
    ceiling = 10 ** (common.DIALOGUE_TRUE_PEAK_DBFS / 20)

    # level=disabled: the limiter's auto-level would otherwise undo the gain.
    graph = (
        f"{_fold()},volume={gain:.2f}dB," f"alimiter=limit={ceiling:.4f}:level=disabled"
    )

    target.parent.mkdir(parents=True, exist_ok=True)

    temp_file = temp_path_for(target, "level")

    if temp_file.exists():
        temp_file.unlink()

    result = _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-af",
            graph,
            "-ar",
            str(common.TARGET_SAMPLE_RATE),
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

        raise LoudnessError(f"FFmpeg could not adjust {source.name}: {last[0]}")

    atomic_install(temp_file, target)
