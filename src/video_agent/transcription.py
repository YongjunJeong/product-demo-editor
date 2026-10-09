"""Offline faster-whisper adapter; model downloads are a separate explicit action."""

from pathlib import Path

from .media import UserError
from .schema import Config, Segment, Transcript, Word

MODELS = {"tiny", "base", "small", "medium", "large-v3", "turbo"}


def prepare_model(model: str, directory: Path) -> str:
    if model not in MODELS:
        raise UserError(f"Choose a multilingual model: {', '.join(sorted(MODELS))}")
    try:
        import truststore

        truststore.inject_into_ssl()
        from faster_whisper.utils import download_model

        return download_model(model, cache_dir=str(directory))
    except ImportError as exc:
        raise UserError('Install ASR support: pip install -e ".[asr]"') from exc
    except Exception as exc:
        raise UserError(f"Model download failed: {exc}") from exc


def transcribe(audio: Path, config: Config, directory: Path, duration: float) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise UserError('Install ASR support: pip install -e ".[asr]"') from exc
    try:
        from faster_whisper.utils import download_model

        snapshot = download_model(config.model, cache_dir=str(directory), local_files_only=True)
        model = WhisperModel(
            snapshot,
            device="cpu",
            compute_type="int8",
            cpu_threads=config.cpu_threads,
            download_root=str(directory),
            local_files_only=True,
        )
        segments, info = model.transcribe(
            str(audio),
            language=config.language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        result = []
        for segment in segments:
            start, end = max(0.0, segment.start), min(duration, segment.end)
            if end <= start:
                continue
            words = [
                Word(
                    start=max(start, w.start),
                    end=min(end, w.end),
                    text=w.word,
                    probability=w.probability,
                )
                for w in segment.words or []
                if min(end, w.end) > max(start, w.start)
            ]
            result.append(
                Segment(
                    id=len(result) + 1, start=start, end=end, text=segment.text.strip(), words=words
                )
            )
        return Transcript(
            status="completed", language=info.language, segments=result, model_snapshot=snapshot
        ).model_dump()
    except Exception as exc:
        raise UserError(
            f"Local transcription failed: {exc}. If the model is missing, run "
            f"video-agent prepare-model --model {config.model}"
        ) from exc
