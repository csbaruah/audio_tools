"""
Concatenation backends for the dice-skit audio pipeline.

Two implementations sit behind one small interface:

    FfmpegBackend   the concat demuxer with `-c copy`. Format-agnostic, and
                    the only option that can ever handle .m4a / .mp4, because
                    those are ISO-BMFF containers whose moov atom indexes the
                    whole stream and therefore cannot be spliced byte-wise.

    RawMp3Backend   a dependency-free MPEG frame splicer. An MP3 is a bare
                    sequence of self-contained frames, so joining two is a
                    matter of stripping their ID3 tags and Xing headers and
                    writing the frames back to back. MP3 only, by nature.

Neither backend re-encodes. See docs/audio-concat-study.md for the full
comparison, including the options that were evaluated and rejected.

To add .m4a support later, implement a third backend against ConcatBackend
and teach select_backend() about it; nothing else needs to change.
"""

import shutil
import subprocess
from pathlib import Path

import audio_common as common
from audio_common import (
    Mp3FormatError,
    build_info_frame,
    build_silence_frame,
    describe_format,
    format_key,
    is_seek_header_frame,
    is_valid_audio,
    iter_frames,
    parse_frame_header,
    reference_frame_header,
    strip_tags,
)

# ============================================================
# SHARED HELPERS
# ============================================================


def temp_path_for(target, tag):
    """
    Derive a sibling temporary path for `target`.

    Temporaries are prefixed with an underscore so the stitch stage's file
    discovery can recognise and exclude them.
    """
    target = Path(target)

    return target.with_name(f"_{target.stem}_{tag}{target.suffix}")


def atomic_install(temp_file, target, validate=True):
    """
    Move a freshly written temporary file into its final location, but only
    after it validates.

    This is the discipline the whole pipeline relies on: a good output file is
    never deleted until its replacement is known to be readable.
    """
    temp_file = Path(temp_file)
    target = Path(target)

    if validate and not is_valid_audio(temp_file):
        if temp_file.exists():
            temp_file.unlink()

        raise RuntimeError(
            f"Refusing to install an invalid file over {target.name}: "
            f"{temp_file.name} failed validation."
        )

    if target.exists():
        target.unlink()

    temp_file.replace(target)


def write_concat_list(files, list_file):
    """
    Create an FFmpeg concat-demuxer list.

    Each line looks like:

        file 'C:/path/file.mp3'
    """
    with open(
        list_file,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:

        for filename in files:

            # Resolve to absolute path.
            path = Path(filename).resolve()

            # FFmpeg concat files work better with forward slashes.
            path_string = path.as_posix()

            # Escape single quotes.
            path_string = path_string.replace(
                "'",
                "'\\''",
            )

            f.write(f"file '{path_string}'\n")


# ============================================================
# NORMALIZATION
# ============================================================
#
# Splicing requires every segment to share one audio format. Inputs that do
# not -- .m4a files carrying AAC, MP3s at a different sample rate -- are
# transcoded once into audio_common.NORMALIZED_DIRECTORY and reused from there.
#
# This is the only step in the pipeline that re-encodes audio, and it is
# unavoidable: AAC cannot be carried inside an MP3 stream, so there is no
# stream-copy path from .m4a to .mp3. It requires FFmpeg; the pure-Python
# backend can splice but cannot decode.


class NormalizationError(RuntimeError):
    """
    Raised when an input cannot be converted to the target format.
    """


def normalized_path_for(source, cache_directory=None):
    """
    Cache path for a normalized copy of `source`.

    Keyed on the source stem, so re-running is cheap. Staleness is detected by
    modification time rather than by hashing the content.
    """
    cache_directory = (
        Path(cache_directory) if cache_directory else common.NORMALIZED_DIRECTORY
    )

    return cache_directory / f"{Path(source).stem}.mp3"


def is_cache_fresh(source, cached):
    """
    True when a cached transcode can be reused.
    """
    source = Path(source)
    cached = Path(cached)

    if not cached.exists() or cached.stat().st_size == 0:
        return False

    if cached.stat().st_mtime < source.stat().st_mtime:
        return False

    return common.is_valid_audio(cached, verbose=False)


def transcode_to_target(source, target_path, header):
    """
    Decode `source` and re-encode it to the target MP3 format.

    Only the first audio stream is taken, and source metadata is dropped, so
    that the output is a clean single-stream MP3 regardless of what the input
    container held.
    """
    source = Path(source)
    target_path = Path(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_file = temp_path_for(target_path, "transcode")

    if temp_file.exists():
        temp_file.unlink()

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-ar",
        str(header.sample_rate),
        "-ac",
        "1" if header.channel_mode == common.CHANNEL_MODE_MONO else "2",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{header.bitrate // 1000}k",
        "-write_xing",
        "1",
        str(temp_file),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        if temp_file.exists():
            temp_file.unlink()

        raise NormalizationError(
            f"FFmpeg could not convert {source.name} to the target format.\n"
            f"{result.stderr.strip()}"
        )

    atomic_install(temp_file, target_path)

    # The transcode is only useful if it actually hit the target format.
    if common.needs_normalizing(target_path, header):
        raise NormalizationError(
            f"Converted {source.name}, but the result is still not in the "
            f"target format ({describe_format(header)})."
        )


def normalize_input(source, header=None, cache_directory=None, verbose=True):
    """
    Return a path to `source` in the target format, transcoding if needed.

    Already-conforming files are returned unchanged, which keeps the majority
    of this pipeline's inputs on the lossless stream-copy path. Everything else
    is transcoded once and cached.

    Returns (path, action) where action is "pass-through", "cached", or
    "transcoded".
    """
    source = Path(source)
    header = header or common.canonical_target_header()

    if not common.needs_normalizing(source, header):
        return source, "pass-through"

    if not common.have_ffmpeg():
        raise NormalizationError(
            f"{source.name} is not in the target format "
            f"({describe_format(header)}) and must be transcoded, but ffmpeg "
            "was not found on PATH.\n"
            "Install FFmpeg, or supply this input as a "
            f"{describe_format(header)} MP3."
        )

    cached = normalized_path_for(source, cache_directory)

    if is_cache_fresh(source, cached):
        if verbose:
            print(f"  {source.name} -> cached {cached.name}")

        return cached, "cached"

    if verbose:
        print(f"  {source.name} -> transcoding to {describe_format(header)}")

    transcode_to_target(source, cached, header)

    return cached, "transcoded"


# ============================================================
# BACKEND INTERFACE
# ============================================================


class ConcatBackend:
    """
    Interface every concatenation backend implements.
    """

    name = "abstract"

    # One-line description of what this backend needs, used in logs.
    requirements = ""

    def supports(self, paths, output_format):
        """
        True when this backend can join `paths` into `output_format`.
        """
        raise NotImplementedError

    def make_silence(self, target, seconds, reference=None):
        """
        Write `seconds` of silence to `target`, matching the audio format of
        the rest of the stream.
        """
        raise NotImplementedError

    def concat(self, paths, target):
        """
        Join `paths` in order and write the result to `target`.
        """
        raise NotImplementedError

    def finalize(self, target):
        """
        Add seek metadata to a finished file. May be a no-op.
        """


# ============================================================
# FFMPEG BACKEND
# ============================================================


class FfmpegBackend(ConcatBackend):
    """
    Concatenation via FFmpeg's concat demuxer with stream copy.

    `-c copy` means FFmpeg never decodes or re-encodes the inputs, which makes
    this both fast and lossless. The demuxer's one hard requirement is that
    every input shares the same streams -- same codec, same time base.
    """

    name = "ffmpeg"
    requirements = "ffmpeg on PATH"

    def supports(self, paths, output_format):
        return common.have_ffmpeg()

    # --------------------------------------------------------
    # Silence
    # --------------------------------------------------------

    def make_silence(self, target, seconds, reference=None):
        """
        Generate silence with `anullsrc`, matched to the TTS output format so
        it concatenates without re-encoding.
        """
        target = Path(target)

        header = reference or reference_frame_header()

        print(
            f"Creating filler: {target.name} ({seconds} seconds, "
            f"{describe_format(header)})"
        )

        temp_file = temp_path_for(target, "silence")

        if temp_file.exists():
            temp_file.unlink()

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={header.sample_rate}:cl=mono",
            "-t",
            str(seconds),
            "-ar",
            str(header.sample_rate),
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{header.bitrate // 1000}k",
            str(temp_file),
        ]

        common.run_command(command)

        atomic_install(temp_file, target)

        print(f"Created: {target}")

    # --------------------------------------------------------
    # Concat
    # --------------------------------------------------------

    def concat(self, paths, target):
        target = Path(target)

        concat_list = temp_path_for(target.with_suffix(".txt"), "concat")
        temp_file = temp_path_for(target, "concat")

        for path in (concat_list, temp_file):
            if path.exists():
                path.unlink()

        write_concat_list(paths, concat_list)

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(temp_file),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            print()
            print("FFmpeg concat failed:")
            print(result.stderr)

            for path in (concat_list, temp_file):
                if path.exists():
                    path.unlink()

            raise RuntimeError(
                f"Could not concatenate {len(paths)} files into {target.name}"
            )

        try:
            atomic_install(temp_file, target)

        finally:
            if concat_list.exists():
                concat_list.unlink()

    # --------------------------------------------------------
    # Finalize
    # --------------------------------------------------------

    def finalize(self, target):
        """
        Re-mux once so the MP3 muxer writes a Xing/Info header.

        The header carries duration and frame counts plus a 100-entry seek
        table, which improves scrubbing in players that use it. Audio is not
        re-encoded; this is a stream copy.
        """
        target = Path(target)

        print("Writing MP3 Xing/Info seek metadata " "without re-encoding audio...")

        temp_file = temp_path_for(target, "seekable")

        if temp_file.exists():
            temp_file.unlink()

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(target),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "copy",
            "-write_xing",
            "1",
            "-id3v2_version",
            "3",
            str(temp_file),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            print()
            print("FFmpeg failed while finalizing:")
            print(result.stderr)

            if temp_file.exists():
                temp_file.unlink()

            raise RuntimeError("Could not write seek metadata.")

        atomic_install(temp_file, target)

        print("Seek metadata written.")


# ============================================================
# PURE-PYTHON MP3 BACKEND
# ============================================================


class RawMp3Backend(ConcatBackend):
    """
    Concatenation by splicing MPEG frames directly, with no external tools.

    Correctness rests on three things:

      1. Every input must share the same frame format. Padding may vary --
         a padded and an unpadded frame differ by one byte but interleave
         freely -- but version, layer, bitrate, sample rate and channel mode
         must match, exactly as FFmpeg's concat demuxer requires.

      2. ID3v2 (head) and ID3v1 (tail) blocks are stripped from every input.
         Left in place they would land mid-stream, where some decoders emit an
         audible click. This is the flaw in `copy /b a.mp3+b.mp3 out.mp3`.

      3. Each input's Xing/Info seek frame is dropped, and a single fresh
         Info header is written for the joined stream. Without this the output
         would inherit the first input's frame count and report a wrong
         duration.

    One caveat, benign for this pipeline: MP3 frames may back-reference up to
    511 bytes of the previous frame's main data via the bit reservoir, so the
    first frame after a join can in principle glitch for a single 24 ms frame.
    Every join here is adjacent to a silence filler, so any artefact lands in
    silence.
    """

    name = "python"
    requirements = "none (pure Python, MP3 only)"

    def supports(self, paths, output_format):
        if str(output_format).lower() != "mp3":
            return False

        return all(Path(path).suffix.lower() == ".mp3" for path in paths)

    # --------------------------------------------------------
    # Silence
    # --------------------------------------------------------

    def make_silence(self, target, seconds, reference=None):
        """
        Synthesise silence as zero-filled Layer III frames.

        A frame whose side information and main data are all zero decodes to
        digital silence, so no encoder is needed.
        """
        target = Path(target)

        header = reference or reference_frame_header()

        frame = build_silence_frame(header)
        frame_seconds = header.samples_per_frame / header.sample_rate
        frame_count = max(1, int(round(seconds / frame_seconds)))

        print(
            f"Creating filler: {target.name} ({seconds} seconds, "
            f"{describe_format(header)}, {frame_count} frames)"
        )

        temp_file = temp_path_for(target, "silence")

        if temp_file.exists():
            temp_file.unlink()

        audio_bytes = frame_count * header.frame_length
        offsets = [index * header.frame_length for index in range(frame_count)]

        with open(temp_file, "wb") as f:
            f.write(
                build_info_frame(
                    header,
                    frame_count=frame_count,
                    total_bytes=header.frame_length + audio_bytes,
                    frame_offsets=offsets,
                )
            )

            for _ in range(frame_count):
                f.write(frame)

        atomic_install(temp_file, target)

        print(f"Created: {target}")

    # --------------------------------------------------------
    # Concat
    # --------------------------------------------------------

    def concat(self, paths, target):
        """
        Splice every input's frames into one stream, then prepend a fresh
        Info seek header.

        Frames are written to a temporary audio-only file first so that memory
        use stays bounded regardless of how long the master becomes; the final
        file is the Info frame followed by that stream.
        """
        target = Path(target)

        audio_temp = temp_path_for(target, "frames")
        temp_file = temp_path_for(target, "splice")

        for path in (audio_temp, temp_file):
            if path.exists():
                path.unlink()

        reference = None
        reference_source = None
        frame_offsets = []
        audio_bytes = 0
        dropped_headers = 0

        try:
            with open(audio_temp, "wb") as out:

                for path in paths:
                    path = Path(path)
                    data = path.read_bytes()

                    start, end = strip_tags(data)

                    file_frames = 0

                    for offset, header in iter_frames(data, start, end):

                        if reference is None:
                            reference = header
                            reference_source = path

                        elif format_key(header) != format_key(reference):
                            raise Mp3FormatError(
                                "Cannot splice MP3s with different formats.\n"
                                f"  {reference_source.name}: "
                                f"{describe_format(reference)}\n"
                                f"  {path.name}: {describe_format(header)}\n"
                                "\n"
                                "Re-encode the inputs to a single format, or "
                                'set CONCAT_BACKEND = "ffmpeg" to let FFmpeg '
                                "handle the mismatch."
                            )

                        # Drop per-file seek headers; one is written for the
                        # joined stream at the end.
                        if is_seek_header_frame(data, offset, header):
                            dropped_headers += 1
                            continue

                        frame_offsets.append(audio_bytes)

                        out.write(data[offset : offset + header.frame_length])

                        audio_bytes += header.frame_length
                        file_frames += 1

                    if file_frames == 0:
                        raise Mp3FormatError(f"No usable MPEG frames found in {path}")

            if reference is None:
                raise Mp3FormatError("No usable MPEG frames found in any input.")

            info_frame = build_info_frame(
                reference,
                frame_count=len(frame_offsets),
                total_bytes=reference.frame_length + audio_bytes,
                frame_offsets=frame_offsets,
            )

            with open(temp_file, "wb") as out:
                out.write(info_frame)

                with open(audio_temp, "rb") as audio:
                    shutil.copyfileobj(audio, out)

            duration = (
                len(frame_offsets) * reference.samples_per_frame / reference.sample_rate
            )

            print(
                f"Spliced {len(frame_offsets)} frames "
                f"({describe_format(reference)}), "
                f"dropped {dropped_headers} seek header frame(s), "
                f"{common.format_duration(duration)}"
            )

            atomic_install(temp_file, target)

        finally:
            for path in (audio_temp, temp_file):
                if path.exists():
                    path.unlink()

    # --------------------------------------------------------
    # Finalize
    # --------------------------------------------------------

    def finalize(self, target):
        """
        No-op: concat() writes the Info seek header inline.
        """
        print(
            "Seek metadata was written inline during splicing; "
            "no finalize pass needed."
        )


# ============================================================
# BACKEND SELECTION
# ============================================================

BACKENDS = {
    FfmpegBackend.name: FfmpegBackend,
    RawMp3Backend.name: RawMp3Backend,
}


def select_backend(paths, output_format=None, preference=None):
    """
    Choose a concatenation backend.

    `preference` may be "auto", "ffmpeg", or "python"; it defaults to
    audio_common.CONCAT_BACKEND. Under "auto", FFmpeg wins when it is on PATH
    -- it is the proven, format-agnostic path -- and the pure-Python splicer
    takes over when FFmpeg is absent and every input is an MP3.

    Raises RuntimeError with an actionable message when nothing can serve.
    """
    preference = (preference or common.CONCAT_BACKEND or "auto").lower()
    output_format = output_format or common.OUTPUT_FORMAT

    if preference not in ("auto",) and preference not in BACKENDS:
        raise RuntimeError(
            f'Unknown CONCAT_BACKEND "{preference}". '
            f'Expected "auto", {", ".join(sorted(BACKENDS))}.'
        )

    if preference != "auto":
        backend = BACKENDS[preference]()

        if not backend.supports(paths, output_format):
            raise RuntimeError(
                f'CONCAT_BACKEND is set to "{preference}", but that backend '
                f"cannot handle this job.\n"
                f"  Requires: {backend.requirements}\n"
                f"  Output format: {output_format}\n"
                f'  Set CONCAT_BACKEND = "auto" to pick automatically.'
            )

        return backend

    ffmpeg = FfmpegBackend()

    if ffmpeg.supports(paths, output_format):
        return ffmpeg

    raw = RawMp3Backend()

    if raw.supports(paths, output_format):
        return raw

    raise RuntimeError(
        "No concatenation backend is available.\n"
        f"  Output format: {output_format}\n"
        "  ffmpeg: not found on PATH\n"
        "  pure-Python splicer: only supports MP3 inputs and MP3 output\n"
        "\n"
        "Install FFmpeg and make sure ffmpeg.exe is on PATH, or convert the "
        "inputs to MP3."
    )
