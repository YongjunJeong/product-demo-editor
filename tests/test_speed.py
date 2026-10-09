import importlib.util
import json
import subprocess
from array import array
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_agent.media import UserError, probe
from video_agent.pipeline import digest
from video_agent.planning import (
    Cue,
    Cut,
    EditPlan,
    ProtectedRange,
    Speed,
    add_speed,
    approve,
    compile_plan,
    fingerprint,
    load_approved,
    select_cuts,
)
from video_agent.rendering import render

spec = importlib.util.spec_from_file_location(
    "fixture", Path(__file__).parents[1] / "scripts/make_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def base(source="test.mp4", sha="test"):
    return EditPlan(
        source=source,
        source_sha256=sha,
        source_duration_ms=10000,
        fps=25,
        evidence={},
        speech_protection_available=False,
        protected_speech=[],
        cues=[],
        cuts=[],
        schema_version="1.1",
        speeds=[],
    )


def test_cut_plus_speed_caption_mapping():
    plan = base()
    plan.cuts = [Cut(id=1, start_frame=5, end_frame=45)]
    plan.speeds = [Speed(id=1, start_frame=50, end_frame=150, rate=2)]
    plan.cues = [
        Cue(id=1, source_segment_ids=[1], start_ms=7000, end_ms=8000, text="After the wait")
    ]
    assert compile_plan(select_cuts(plan, []))["output_duration_seconds"] == 10
    execution = compile_plan(select_cuts(plan, [1], [1]))
    assert execution["output_duration_seconds"] == 6.4
    assert execution["cues"][0]["start_ms"] == 3400
    assert execution["cues"][0]["end_ms"] == 4400
    assert [s["effective_rate"] for s in execution["kept"]] == [1, 1, 2, 1]


@pytest.mark.parametrize("kind", ["speech", "caption", "cut", "speed"])
def test_conflicts_rejected(kind):
    plan = base()
    plan.speeds = [Speed(id=1, start_frame=50, end_frame=100, rate=2)]
    if kind == "speech":
        plan.protected_speech = [ProtectedRange(start_ms=3000, end_ms=4000)]
    elif kind == "caption":
        plan.cues = [Cue(id=1, source_segment_ids=[1], start_ms=3000, end_ms=4000, text="Speech")]
    elif kind == "cut":
        plan.cuts = [Cut(id=1, start_frame=60, end_frame=90)]
    else:
        plan.speeds.append(Speed(id=2, start_frame=75, end_frame=125, rate=2))
    with pytest.raises(ValidationError, match="overlap"):
        select_cuts(plan, [1] if kind == "cut" else [], [1])


@pytest.mark.parametrize("rate", [0, 1, 4.1, float("nan"), float("inf")])
def test_invalid_rates(rate):
    with pytest.raises(ValidationError):
        Speed(id=1, start_frame=0, end_frame=50, rate=rate)


def test_frame_rounding_and_pending():
    plan = base()
    plan.speeds = [Speed(id=1, start_frame=0, end_frame=100, rate=1.5)]
    with pytest.raises(UserError, match="Resolve"):
        compile_plan(plan)
    execution = compile_plan(select_cuts(plan, [], [1]))
    assert execution["kept"][0]["output_end_frame"] == 67
    assert execution["kept"][0]["effective_rate"] == pytest.approx(100 / 67)
    assert execution["output_frames"] == 217
    with pytest.raises(UserError):
        select_cuts(plan, [], [99])
    with pytest.raises(UserError):
        select_cuts(plan, [], [1, 1])


def test_legacy_approval_and_speed_tampering(tmp_path):
    plan = base().model_dump(exclude={"speeds"})
    plan["schema_version"] = "1.0"
    execution = compile_plan(EditPlan.model_validate(plan))
    assert execution["schema_version"] == "1.0"
    assert "effective_rate" not in execution["kept"][0]
    payload = {"plan": plan, "execution": execution}
    old = tmp_path / "old.json"
    old.write_text(
        json.dumps({**payload, "approval_sha256": fingerprint(payload), "approved_at": "test"})
    )
    assert load_approved(old)[1] == execution
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(plan))
    speed = tmp_path / "speed.json"
    add_speed(draft, 2, 6, 2, "mute", speed)
    approved = tmp_path / "approved.json"
    approve(speed, [], approved, [1])
    assert load_approved(approved)[1]["output_duration_seconds"] == 8
    data = json.loads(approved.read_text())
    data["plan"]["speeds"][0]["rate"] = 3
    approved.write_text(json.dumps(data))
    with pytest.raises(UserError, match="changed"):
        load_approved(approved)
    with pytest.raises(FileExistsError):
        add_speed(draft, 2, 6, 2, "mute", speed)
    with pytest.raises(UserError):
        add_speed(draft, -1, 6, 2, "mute", tmp_path / "bad.json")


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


@pytest.mark.parametrize("audio", ["mute", "tempo"])
def test_real_cut_speed_render_and_audio_policy(tmp_path, audio):
    source = tmp_path / "fixture.mp4"
    fixture.make_fixture(source)
    plan = base(str(source), digest(source))
    plan.cuts = [Cut(id=1, start_frame=5, end_frame=45)]
    plan.speeds = [Speed(id=1, start_frame=50, end_frame=150, rate=2, audio=audio)]
    plan.cues = [
        Cue(id=1, source_segment_ids=[1], start_ms=7000, end_ms=8000, text="Normal speed again")
    ]
    plan = select_cuts(plan, [1], [1])
    execution = compile_plan(plan)
    output = tmp_path / "render"
    metrics = render(plan, execution, output, approved=False)
    assert metrics["removed_frames"] == 40
    assert metrics["speed_saved_frames"] == 50
    assert probe(output / "edited.mp4")["duration"] == pytest.approx(6.4, abs=0.041)
    assert sum(frame(output / "edited.mp4", 1.7)) / (64 * 36) < 20
    assert sum(frame(output / "edited.mp4", 2.1)) / (64 * 36) > 230
    assert "00:00:03,400 --> 00:00:04,400" in (output / "subtitles.en.srt").read_text()
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
    middle = samples[48000:72000]
    if audio == "mute":
        assert max(abs(v) for v in middle) < 0.005
    else:
        crossings = sum(a <= 0 < b for a, b in pairwise(middle))
        assert crossings / 0.5 == pytest.approx(440, abs=20)
    assert max(abs(v) for v in samples[129600:134400]) > 0.1
    audio_stream = next(
        s for s in probe(output / "edited.mp4")["streams"] if s["codec_type"] == "audio"
    )
    assert float(audio_stream["duration"]) == pytest.approx(6.4, abs=0.045)


@pytest.mark.parametrize("rate", [1.5, 3.5, 4])
def test_fractional_and_high_rates_without_audio(tmp_path, rate):
    source = tmp_path / "no-audio.mp4"
    fixture.make_fixture(source, no_audio=True)
    plan = base(str(source), digest(source))
    plan.fps = 30
    plan.speeds = [Speed(id=1, start_frame=30, end_frame=270, rate=rate)]
    plan = select_cuts(plan, [], [1])
    execution = compile_plan(plan)
    output = tmp_path / "render"
    render(plan, execution, output, approved=False)
    metadata = probe(output / "edited.mp4")
    assert metadata["duration"] == pytest.approx(execution["output_duration_seconds"], abs=0.034)
    assert not metadata["audio_present"]


@pytest.mark.parametrize("rate", [1.5, 3.5])
def test_fractional_audio_matches_compiled_duration(tmp_path, rate):
    source = tmp_path / "audio.mp4"
    fixture.make_fixture(source)
    plan = base(str(source), digest(source))
    plan.speeds = [Speed(id=1, start_frame=50, end_frame=150, rate=rate, audio="tempo")]
    plan = select_cuts(plan, [], [1])
    execution = compile_plan(plan)
    output = tmp_path / "fractional"
    render(plan, execution, output, approved=False)
    metadata = probe(output / "edited.mp4")
    audio = next(s for s in metadata["streams"] if s["codec_type"] == "audio")
    assert float(audio["duration"]) == pytest.approx(execution["output_duration_seconds"], abs=0.05)


def test_one_frame_speed_rejected_at_add(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(base().model_dump()))
    with pytest.raises(UserError, match="too short"):
        add_speed(plan, 0, 0.04, 4, "mute", tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()
