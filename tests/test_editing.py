import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_agent.media import UserError, probe
from video_agent.pipeline import analyze, digest
from video_agent.planning import (
    Cue,
    Cut,
    EditPlan,
    approve,
    compile_plan,
    create_plan,
    load_approved,
    load_plan,
    select_cuts,
)
from video_agent.rendering import render
from video_agent.schema import Config

spec = importlib.util.spec_from_file_location(
    "fixture", Path(__file__).parents[1] / "scripts/make_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.fixture
def planned(tmp_path):
    source = tmp_path / "검증 ' 영상.mp4"
    fixture.make_fixture(source)
    run = analyze(source, tmp_path / "runs", Config(transcribe=False), tmp_path / "models")
    path = tmp_path / "plan.json"
    create_plan(run, None, path, fps=25)
    return source, run, path


def test_approval_compile_and_changed_content(planned, tmp_path):
    _, _, path = planned
    plan = load_plan(path)
    assert len(plan.cuts) == 2
    assert all(c.decision == "pending" for c in plan.cuts)
    assert compile_plan(select_cuts(plan, []))["output_duration_seconds"] == 10
    approved = tmp_path / "approved.json"
    approve(path, [1], approved)
    selected, execution = load_approved(approved)
    assert execution["output_duration_seconds"] == pytest.approx(8.4)
    assert selected.cuts[1].decision == "rejected"
    data = json.loads(approved.read_text())
    data["plan"]["source_duration_ms"] += 1
    approved.write_text(json.dumps(data))
    with pytest.raises(UserError, match="changed"):
        load_approved(approved)


def test_cut_remaps_captions_and_rejects_overlap(planned):
    _, _, path = planned
    data = load_plan(path).model_dump()
    data["cues"] = [
        {"id": 1, "source_segment_ids": [1], "start_ms": 3000, "end_ms": 4500, "text": "Hello"}
    ]
    plan = EditPlan.model_validate(data)
    execution = compile_plan(select_cuts(plan, [1]))
    assert execution["cues"][0]["start_ms"] == 1400
    assert execution["cues"][0]["end_ms"] == 2900
    data["cues"][0]["start_ms"] = 1000
    with pytest.raises(ValidationError, match="overlaps"):
        select_cuts(EditPlan.model_validate(data), [1])
    data["cues"] = []
    data["protected_speech"] = [{"start_ms": 1000, "end_ms": 2000}]
    with pytest.raises(ValidationError, match="overlaps"):
        select_cuts(EditPlan.model_validate(data), [1])


def test_invalid_plans(planned):
    _, _, path = planned
    plan = load_plan(path)
    with pytest.raises(UserError):
        select_cuts(plan, [999])
    with pytest.raises(UserError):
        select_cuts(plan, [1, 1])
    with pytest.raises(UserError, match="Resolve"):
        compile_plan(plan)
    plan.cuts = [Cut(id=1, start_frame=0, end_frame=plan.total_frames, decision="accepted")]
    with pytest.raises(UserError, match="entire"):
        compile_plan(plan)
    data = load_plan(path).model_dump()
    data["cuts"][1]["start_frame"] = 1
    with pytest.raises(ValidationError):
        EditPlan.model_validate(data)


def frame(path, at):
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=64:36,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


def test_render_cut_audio_captions_and_cache(planned, tmp_path):
    source, _, path = planned
    original = digest(source)
    plan = load_plan(path)
    plan.cues = [
        Cue(id=1, source_segment_ids=[1], start_ms=2500, end_ms=4500, text="Review caption")
    ]
    plan = select_cuts(plan, [1])
    execution = compile_plan(plan)
    output = tmp_path / "render ' 한글"
    metrics = render(plan, execution, output, approved=False)
    assert metrics["status"] == "completed"
    assert probe(output / "edited.mp4")["duration"] == pytest.approx(8.4, abs=0.045)
    metadata = probe(output / "edited.mp4")
    assert metadata["audio_present"]
    audio = next(s for s in metadata["streams"] if s["codec_type"] == "audio")
    assert float(audio["duration"]) == pytest.approx(8.4, abs=0.05)
    assert sum(frame(output / "edited.mp4", 3.2)) / (64 * 36) < 20
    assert sum(frame(output / "edited.mp4", 3.6)) / (64 * 36) > 230
    assert frame(output / "preview.mp4", 1.5) != frame(output / "edited.mp4", 1.5)
    assert "00:00:00,900 --> 00:00:02,900" in (output / "subtitles.en.srt").read_text()
    again = render(plan, execution, output, approved=False, resume=True)
    assert all(s["cached"] for s in again["stages"].values())
    # A broken derived output is regenerated; the clean stage remains cached.
    (output / "preview.mp4").write_bytes(b"broken")
    again = render(plan, execution, output, approved=False, resume=True)
    assert again["stages"]["edited"]["cached"]
    assert not again["stages"]["preview"]["cached"]
    assert digest(source) == original
    with pytest.raises(FileExistsError):
        render(plan, execution, output, approved=False)
    with pytest.raises(UserError, match="identical"):
        render(plan, execution, output, approved=True, resume=True)


def test_silent_video_render(tmp_path):
    source = tmp_path / "no-audio.mp4"
    fixture.make_fixture(source, no_audio=True)
    plan = EditPlan(
        source=str(source),
        source_sha256=digest(source),
        source_duration_ms=10000,
        fps=25,
        evidence={},
        speech_protection_available=False,
        protected_speech=[],
        cues=[],
        cuts=[],
    )
    output = tmp_path / "silent-render"
    render(plan, compile_plan(plan), output, approved=False)
    assert not probe(output / "edited.mp4")["audio_present"]
    assert (output / "subtitles.en.srt").read_text() == ""


def test_changed_analysis_rejected(planned, tmp_path):
    _, run, _ = planned
    (run / "silence.json").write_text("{}")
    with pytest.raises(UserError, match="changed"):
        create_plan(run, None, tmp_path / "bad.json")


def test_vfr_nonzero_origin_delayed_audio_multiple_cuts(planned, tmp_path):
    from array import array

    source, _, _ = planned
    shifted = tmp_path / "shifted.mkv"
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
            "asetpts=PTS+3.5/TB",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-c:a",
            "pcm_s16le",
            str(shifted),
        ],
        check=True,
    )
    plan = EditPlan(
        source=str(shifted),
        source_sha256=digest(shifted),
        # VFR muxers can differ by a final packet; bind to measured media like create_plan.
        source_duration_ms=round(probe(shifted)["duration"] * 1000),
        fps=30,
        evidence={},
        speech_protection_available=False,
        protected_speech=[],
        cues=[],
        cuts=[
            Cut(id=1, start_frame=6, end_frame=54, decision="accepted"),
            Cut(id=2, start_frame=246, end_frame=294, decision="accepted"),
        ],
    )
    output = tmp_path / "vfr-render"
    execution = compile_plan(plan)
    render(plan, execution, output, approved=False)
    metadata = probe(output / "edited.mp4")
    assert metadata["duration"] == pytest.approx(execution["output_duration_seconds"], abs=0.034)
    assert execution["output_frames"] == plan.total_frames - 96
    assert sum(frame(output / "edited.mp4", 3.2)) / (64 * 36) < 20
    assert sum(frame(output / "edited.mp4", 3.6)) / (64 * 36) > 230
    pcm = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output / "edited.mp4"),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    samples = array("f", pcm)
    assert max(abs(v) for v in samples[24000:48000]) < 0.005
    assert max(abs(v) for v in samples[110400:115200]) > 0.1


def test_failed_preview_can_resume_clean_stage(planned, tmp_path, monkeypatch):
    from video_agent import rendering

    _, _, path = planned
    plan = select_cuts(load_plan(path), [])
    execution = compile_plan(plan)
    original = rendering.invoke

    def fail_preview(binary, args, output, log):
        if log.name == "preview.log":
            raise UserError("simulated preview failure")
        original(binary, args, output, log)

    monkeypatch.setattr(rendering, "invoke", fail_preview)
    output = tmp_path / "interrupted"
    with pytest.raises(UserError, match="simulated"):
        render(plan, execution, output, approved=False)
    monkeypatch.setattr(rendering, "invoke", original)
    metrics = render(plan, execution, output, approved=False, resume=True)
    assert metrics["stages"]["edited"]["cached"]
    assert not metrics["stages"]["preview"]["cached"]


def test_unapproved_and_malformed_files_are_friendly_cli_errors(planned, tmp_path):
    import sys

    _, _, path = planned
    for index, value in enumerate([None, [], {"plan": {}}]):
        malformed = tmp_path / f"invalid-{index}.json"
        malformed.write_text(json.dumps(value))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "video_agent.cli",
                "render",
                str(malformed),
                "--output",
                str(tmp_path / f"out-{index}"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert not (tmp_path / f"out-{index}").exists()
    with pytest.raises(ValidationError):
        load_approved(path)


def test_wrong_subtitle_transcript_rejected(planned, tmp_path):
    _, run, _ = planned
    subtitle = tmp_path / "wrong-subtitles.json"
    subtitle.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "language": "en",
                "time_base": "source_milliseconds",
                "status": "draft",
                "request_id": "test",
                "source_sha256": "wrong",
                "request_sha256": "test",
                "response_sha256": "test",
                "cues": [],
            }
        )
    )
    with pytest.raises(UserError, match="different transcript"):
        create_plan(run, subtitle, tmp_path / "bad-plan.json")
