"""Small checkpointed pipeline. Each artifact is published atomically."""

import hashlib
import importlib.metadata
import json
import platform
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .media import UserError, detect_scenes, detect_silence, executable, extract_audio, probe, run
from .schema import Config, Transcript
from .transcription import transcribe


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def versions() -> dict:
    result = {
        "app": __version__,
        "implementation_sha256": hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))
        ).hexdigest(),
        "python": platform.python_version(),
        "architecture": platform.machine(),
    }
    for name in ("ffmpeg", "ffprobe"):
        result[name] = run([executable(name), "-version"]).splitlines()[0]
    for package in ("faster-whisper", "ctranslate2", "pydantic", "PyYAML"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def analyze(
    source: Path,
    root: Path,
    config: Config,
    model_dir: Path,
    resume: Path | None = None,
    progress=None,
) -> Path:
    started = time.perf_counter()
    source = source.expanduser().resolve()
    if not source.is_file():
        raise UserError(f"Source video does not exist: {source}")
    identity = {
        "source": str(source),
        "sha256": digest(source),
        "config": config.model_dump(),
        "versions": versions(),
        "model_directory": str(model_dir.resolve()),
    }
    if resume:
        directory = resume.resolve()
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["identity"] != identity:
            raise UserError(
                "Resume requires the same source, config, model directory and versions."
            )
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        directory = root.resolve() / run_id
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {"identity": identity, "artifacts": {}}
        write_json(directory / "manifest.json", manifest)
        write_json(directory / "config.json", config.model_dump())
    metrics = {
        "schema_version": "1.0",
        "status": "running",
        "run_id": directory.name,
        "video_duration_seconds": None,
        "stages": {},
        "frames_extracted": 0,
        "frames_sent_to_ai": 0,
        "semantic_calls": 0,
        "external_requests": 0,
        "llm_tokens_input": 0,
        "llm_tokens_output": 0,
        "estimated_external_cost_usd": 0,
        "local_asr_calls": 0,
    }

    def stage(name: str, function: Callable[[], dict]) -> dict:
        if progress:
            progress(name)
        path = directory / f"{name}.json"
        cached = path.exists() and manifest["artifacts"].get(name) == digest(path)
        then = time.perf_counter()
        print(
            json.dumps({"stage": name, "status": "cached" if cached else "running"}),
            file=sys.stderr,
            flush=True,
        )
        value = json.loads(path.read_text()) if cached else function()
        if name == "transcript":
            value = Transcript.model_validate(value).model_dump()
            if any(s["end"] > metadata["duration"] for s in value["segments"]):
                raise UserError("Transcript extends beyond the source video.")
        if not cached:
            write_json(path, value)
            manifest["artifacts"][name] = digest(path)
            write_json(directory / "manifest.json", manifest)
        metrics["stages"][name] = {"seconds": time.perf_counter() - then, "cached": cached}
        return value

    audio = directory / "analysis.wav"
    try:
        metadata = stage("metadata", lambda: probe(source))
        metrics["video_duration_seconds"] = metadata["duration"]

        def ensure_audio() -> Path:
            if not audio.exists():
                temporary = directory / "analysis.partial.wav"
                temporary.unlink(missing_ok=True)
                extract_audio(source, temporary, metadata)
                temporary.replace(audio)
            return audio

        stage(
            "silence",
            lambda: (
                detect_silence(ensure_audio(), metadata["duration"], config)
                if metadata["audio_present"]
                else {
                    "schema_version": "1.0",
                    "time_base": "source_seconds",
                    "status": "skipped_no_audio",
                    "regions": [],
                }
            ),
        )
        stage("scenes", lambda: detect_scenes(source, metadata, config))

        def speech() -> dict:
            if not metadata["audio_present"] or not config.transcribe:
                return Transcript(
                    status="skipped_no_audio"
                    if not metadata["audio_present"]
                    else "skipped_by_user",
                    language=config.language,
                ).model_dump()
            metrics["local_asr_calls"] += 1
            return transcribe(ensure_audio(), config, model_dir, metadata["duration"])

        transcript = Transcript.model_validate(stage("transcript", speech))
        metrics["transcript_characters"] = sum(len(s.text) for s in transcript.segments)
        metrics["speech_segments"] = len(transcript.segments)
        metrics["status"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        metrics["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        metrics["error"] = str(exc)
        print(f"Run artifacts preserved: {directory}", file=sys.stderr)
        raise
    finally:
        audio.unlink(missing_ok=True)
        (directory / "analysis.partial.wav").unlink(missing_ok=True)
        elapsed = time.perf_counter() - started
        metrics["processing_seconds"] = elapsed
        duration = metrics["video_duration_seconds"]
        metrics["real_time_factor"] = elapsed / duration if duration else None
        metrics_path = directory / "run_metrics.json"
        if metrics_path.exists():
            write_json(
                directory / f"metrics_previous_{uuid.uuid4().hex[:8]}.json",
                json.loads(metrics_path.read_text()),
            )
        write_json(metrics_path, metrics)
    return directory
