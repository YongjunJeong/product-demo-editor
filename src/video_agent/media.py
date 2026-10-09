"""FFmpeg inspection only; original media is never modified."""

import json
import re
import shutil
import subprocess
import tempfile
from itertools import pairwise
from pathlib import Path

from .schema import Config, Interval


class UserError(Exception):
    """An actionable CLI error, without a traceback."""


def executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise UserError(f"{name} is missing. Install FFmpeg and add it to PATH.")
    return path


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise UserError(f"{Path(command[0]).name} failed: {result.stderr[-2000:]}")
    return result.stdout + result.stderr


def packet_duration(source: Path, stream_index: int, start: float) -> float:
    endpoint = start
    with (
        tempfile.TemporaryFile(mode="w+") as errors,
        subprocess.Popen(
            [
                executable("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                str(stream_index),
                "-show_packets",
                "-show_entries",
                "packet=pts_time,duration_time",
                "-of",
                "csv=p=0",
                str(source),
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        ) as process,
    ):
        assert process.stdout is not None
        for line in process.stdout:
            fields = line.strip().split(",")
            try:
                pts = float(fields[0])
                length = float(fields[1]) if fields[1] != "N/A" else 0.0
                endpoint = max(endpoint, pts + length)
            except (ValueError, IndexError):
                continue
        if process.wait():
            errors.seek(0)
            raise UserError(f"Cannot determine video duration: {errors.read()[-1000:]}")
    return endpoint - start


def probe(source: Path) -> dict:
    data = json.loads(
        run(
            [
                executable("ffprobe"),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(source),
            ]
        )
    )
    video = next(
        (
            s
            for s in data["streams"]
            if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise UserError("Input has no video stream.")
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    # Containers such as Matroska can report an absolute endpoint as format duration.
    # When stream duration is absent, inspect packet timestamps instead of assuming it.
    duration = float(video.get("duration") or 0)
    if not duration:
        duration = packet_duration(source, video["index"], float(video.get("start_time", 0)))
    if not 0 < duration < float("inf"):
        raise UserError("Input has no finite positive duration.")
    return {
        "schema_version": "1.0",
        "time_base": "source_seconds",
        "duration": duration,
        "width": video["width"],
        "height": video["height"],
        "codec": video["codec_name"],
        "avg_frame_rate": video.get("avg_frame_rate"),
        "nominal_frame_rate": video.get("r_frame_rate"),
        "video_stream_index": video["index"],
        "audio_stream_index": audio["index"] if audio else None,
        "audio_present": audio is not None,
        "video_start_time": float(video.get("start_time", 0)),
        "audio_start_time": float(audio.get("start_time", 0)) if audio else None,
        "streams": data["streams"],
    }


def extract_audio(source: Path, output: Path, metadata: dict) -> None:
    # Normalize audio to the first video frame, preserving relative stream offset.
    offset = metadata["audio_start_time"] - metadata["video_start_time"]
    filters = ["asetpts=PTS-STARTPTS"]
    if offset > 0:
        filters.append(f"adelay={round(offset * 1000)}:all=1")
    elif offset < 0:
        filters.extend([f"atrim=start={-offset}", "asetpts=PTS-STARTPTS"])
    filters.append("apad")
    run(
        [
            executable("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            str(source),
            "-map",
            f"0:{metadata['audio_stream_index']}",
            "-vn",
            "-af",
            ",".join(filters),
            "-t",
            str(metadata["duration"]),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output),
        ]
    )


def detect_silence(audio: Path, duration: float, config: Config) -> dict:
    output = run(
        [
            executable("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-i",
            str(audio),
            "-af",
            (
                f"silencedetect=noise={config.silence_threshold_db}dB:"
                f"d={config.silence_min_duration}"
            ),
            "-f",
            "null",
            "-",
        ]
    )
    regions = []
    start = None
    for kind, value in re.findall(r"silence_(start|end): ([\d.e+\-]+)", output):
        value = max(0.0, min(duration, float(value)))
        if kind == "start":
            start = value
        elif start is not None:
            if value > start:
                regions.append(Interval(start=start, end=value).model_dump())
            start = None
    if start is not None and duration > start:
        regions.append(Interval(start=start, end=duration).model_dump())
    return {
        "schema_version": "1.0",
        "time_base": "source_seconds",
        "status": "completed",
        "regions": regions,
        "note": "Silence candidates are not instructions to remove video.",
    }


def detect_scenes(source: Path, metadata: dict, config: Config) -> dict:
    output = run(
        [
            executable("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-i",
            str(source),
            "-map",
            f"0:{metadata['video_stream_index']}",
            "-an",
            "-vf",
            (
                f"setpts=PTS-STARTPTS,scale={config.scene_width}:-2,"
                f"select='gt(scene,{config.scene_threshold})',showinfo"
            ),
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ]
    )
    duration = metadata["duration"]
    cuts = sorted(
        {float(t) for t in re.findall(r"pts_time:([\d.e+\-]+)", output) if 0 < float(t) < duration}
    )
    boundaries = [0.0, *cuts, duration]
    scenes = [
        dict(id=i + 1, **Interval(start=a, end=b).model_dump())
        for i, (a, b) in enumerate(pairwise(boundaries))
    ]
    return {
        "schema_version": "1.0",
        "time_base": "source_seconds",
        "status": "completed",
        "cuts": cuts,
        "scenes": scenes,
        "note": "Pixel changes only, not semantic scenes or loading detection.",
    }
