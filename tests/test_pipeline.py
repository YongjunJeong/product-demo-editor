import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_agent.media import UserError
from video_agent.pipeline import analyze, digest
from video_agent.schema import Config, Interval, Transcript

spec = importlib.util.spec_from_file_location(
    "fixture", Path(__file__).parents[1] / "scripts/make_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def read(run, name):
    return json.loads((run / f"{name}.json").read_text())


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "테스트 영상.mp4"
    fixture.make_fixture(path)
    return path


def test_real_analysis_and_resume(source, tmp_path):
    original = digest(source)
    config = Config(transcribe=False)
    run = analyze(source, tmp_path / "runs", config, tmp_path / "models")
    assert read(run, "metadata")["duration"] == pytest.approx(10, abs=0.1)
    assert read(run, "scenes")["cuts"] == pytest.approx([5], abs=0.08)
    regions = read(run, "silence")["regions"]
    assert len(regions) == 2
    assert regions[0]["start"] == pytest.approx(0, abs=0.05)
    assert regions[0]["end"] == pytest.approx(2, abs=0.05)
    assert regions[1]["start"] == pytest.approx(8, abs=0.05)
    assert regions[1]["end"] == pytest.approx(10, abs=0.05)
    assert read(run, "transcript")["status"] == "skipped_by_user"
    assert read(run, "run_metrics")["external_requests"] == 0
    analyze(source, tmp_path / "runs", config, tmp_path / "models", resume=run)
    assert all(s["cached"] for s in read(run, "run_metrics")["stages"].values())
    assert digest(source) == original
    assert not (run / "analysis.wav").exists()
    # A corrupted checkpoint is recomputed rather than trusted.
    (run / "scenes.json").write_text("broken")
    analyze(source, tmp_path / "runs", config, tmp_path / "models", resume=run)
    assert not read(run, "run_metrics")["stages"]["scenes"]["cached"]
    with pytest.raises(UserError, match="same source"):
        analyze(
            source,
            tmp_path,
            Config(transcribe=False, scene_threshold=0.4),
            tmp_path / "models",
            resume=run,
        )


def test_no_audio(tmp_path):
    path = tmp_path / "silent.mp4"
    fixture.make_fixture(path, no_audio=True)
    run = analyze(path, tmp_path / "runs", Config(), tmp_path / "models")
    assert read(run, "transcript")["status"] == "skipped_no_audio"
    assert read(run, "silence")["status"] == "skipped_no_audio"


def test_transcription_failure_is_resumable(source, tmp_path, monkeypatch):
    from video_agent import pipeline

    def fail(*args):
        raise UserError("test ASR failure")

    monkeypatch.setattr(pipeline, "transcribe", fail)
    with pytest.raises(UserError, match="test ASR failure"):
        analyze(source, tmp_path / "runs", Config(), tmp_path / "models")
    run = next((tmp_path / "runs").iterdir())
    assert read(run, "run_metrics")["status"] == "failed"
    monkeypatch.setattr(
        pipeline,
        "transcribe",
        lambda *args: Transcript(status="completed", language="ko").model_dump(),
    )
    analyze(source, tmp_path / "runs", Config(), tmp_path / "models", resume=run)
    assert read(run, "run_metrics")["stages"]["scenes"]["cached"]
    assert read(run, "run_metrics")["status"] == "completed"


def test_bad_input_cli(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "video_agent.cli", "analyze", str(tmp_path / "missing.mov")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_invalid_contracts():
    with pytest.raises(ValidationError):
        Interval(start=2, end=1)
    with pytest.raises(ValidationError):
        Config(scene_threshold=2)
    with pytest.raises(ValidationError):
        Config(silence_threshold_db=float("nan"))


def test_all_silent_audio(tmp_path):
    path = tmp_path / "all-silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=160x90:r=10:d=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    run = analyze(path, tmp_path / "runs", Config(transcribe=False), tmp_path / "models")
    region = read(run, "silence")["regions"][0]
    assert region["start"] == pytest.approx(0, abs=0.05)
    assert region["end"] == pytest.approx(2, abs=0.05)
    assert read(run, "scenes")["cuts"] == []


def test_variable_frame_rate_and_nonzero_start(source, tmp_path):
    path = tmp_path / "variable.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-n",
            "-i",
            str(source),
            "-vf",
            "select='if(lt(t,5),not(mod(n,2)),1)',setpts=PTS+2/TB",
            "-af",
            "asetpts=PTS+2/TB",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    run = analyze(path, tmp_path / "runs", Config(transcribe=False), tmp_path / "models")
    metadata = read(run, "metadata")
    assert metadata["video_start_time"] == pytest.approx(2, abs=0.05)
    assert metadata["duration"] == pytest.approx(10, abs=0.15)
    assert read(run, "scenes")["cuts"] == pytest.approx([5], abs=0.08)


@pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None,
    reason="Model-cache failure requires the optional ASR package",
)
def test_missing_model_is_local_failure(source, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "video_agent.cli",
            "analyze",
            str(source),
            "--model-dir",
            str(tmp_path / "empty-models"),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "prepare-model" in result.stderr
    assert "Traceback" not in result.stderr
    run = next((tmp_path / "runs").iterdir())
    assert read(run, "run_metrics")["status"] == "failed"
    assert not (run / "transcript.json").exists()


def test_missing_asr_package_is_local_failure(source, tmp_path):
    # Force the actual optional-import failure in a subprocess, even on an ASR-enabled Mac.
    bootstrap = """
import importlib.abc
import runpy
import sys
class MissingASR(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'faster_whisper' or fullname.startswith('faster_whisper.'):
            raise ModuleNotFoundError('ASR intentionally unavailable in this test')
sys.meta_path.insert(0, MissingASR())
sys.argv = ['video-agent', *sys.argv[1:]]
runpy.run_module('video_agent.cli', run_name='__main__')
"""
    original = digest(source)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            "analyze",
            str(source),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Install ASR support" in result.stderr and "Traceback" not in result.stderr
    run = next((tmp_path / "runs").iterdir())
    assert read(run, "run_metrics")["status"] == "failed"
    assert not (run / "transcript.json").exists()
    assert digest(source) == original


def test_delayed_audio_keeps_source_time(source, tmp_path):
    path = tmp_path / "delayed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-n",
            "-i",
            str(source),
            "-itsoffset",
            "1.5",
            "-i",
            str(source),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c",
            "copy",
            "-t",
            "10",
            str(path),
        ],
        check=True,
    )
    run = analyze(path, tmp_path / "runs", Config(transcribe=False), tmp_path / "models")
    regions = read(run, "silence")["regions"]
    assert regions[0]["start"] == pytest.approx(0, abs=0.05)
    assert regions[0]["end"] == pytest.approx(3.5, abs=0.1)
