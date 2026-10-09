import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_agent.agent_exchange import export_agent, import_agent
from video_agent.capcut import export_capcut, verify_bundle
from video_agent.media import UserError, probe
from video_agent.pipeline import analyze, digest
from video_agent.planning import (
    approve,
    compile_plan,
    load_approved,
    load_plan,
    select_cuts,
)
from video_agent.rendering import render
from video_agent.schema import Config


@pytest.fixture
def agent_exchange(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "fixture", Path(__file__).parents[1] / "scripts/make_fixture.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    source = tmp_path / "fixture.mp4"
    fixture.make_fixture(source)
    run = analyze(source, tmp_path / "runs", Config(transcribe=False), tmp_path / "models")
    # Known synthetic speech annotations, not an ASR quality claim.
    transcript = {
        "status": "completed",
        "language": "ko",
        "segments": [
            {"id": 1, "start": 2.1, "end": 3.0, "text": "먼저 시작합니다."},
            {"id": 2, "start": 3.5, "end": 4.5, "text": "다시 시작합니다."},
            {"id": 3, "start": 6.0, "end": 7.0, "text": "저장 버튼을 누릅니다."},
        ],
    }
    (run / "transcript.json").write_text(json.dumps(transcript))
    # A silence boundary overlaps speech by 0.3s: its safe interior must survive.
    (run / "silence.json").write_text(json.dumps({"regions": [{"start": 4.2, "end": 5.8}]}))
    manifest = json.loads((run / "manifest.json").read_text())
    for name in ["transcript", "silence"]:
        manifest["artifacts"][name] = digest(run / f"{name}.json")
    (run / "manifest.json").write_text(json.dumps(manifest))
    exchange = tmp_path / "exchange"
    export_agent(run, exchange, "Remove a restarted explanation", 8)
    response = json.loads((exchange / "response.template.json").read_text())
    response["engine"] = "synthetic-test-response"
    response["segments"] = [
        {"id": 1, "text": "Let's start."},
        {"id": 2, "text": "Let's start again."},
        {"id": 3, "text": "Click Save."},
    ]
    reply = tmp_path / "response.json"
    reply.write_text(json.dumps(response))
    return source, exchange, reply, response


def test_safe_interior_and_text_only_export(agent_exchange):
    source, exchange, _, _ = agent_exchange
    request = (exchange / "request.json").read_text()
    assert str(source) not in request
    plan = load_plan(exchange / "base-plan.json")
    assert len(plan.cuts) == 1
    cut = plan.cuts[0]
    assert cut.start_frame / plan.fps >= 4.65
    assert cut.end_frame / plan.fps <= 5.6
    assert not plan.overlaps_protected(cut)
    assert (cut.end_frame - cut.start_frame) / plan.fps >= 0.7


def proposal(kind="remove_speech", **extra):
    return {
        "id": 1,
        "kind": kind,
        "segment_ids": [2],
        "rationale": "Restarted explanation",
        "confidence": "medium",
        "requires_visual_review": True,
        **extra,
    }


def test_semantic_preview_render_export_and_legacy_guard(agent_exchange, tmp_path):
    source, exchange, reply, response = agent_exchange
    original = digest(source)
    response["proposals"] = [proposal()]
    reply.write_text(json.dumps(response))
    out = tmp_path / "draft"
    report = import_agent(exchange / "request.json", reply, out)
    assert not report["approved"] and report["preview_cuts"] == [1]
    plan = load_plan(out / "plan.json")
    assert plan.cuts[0].decision == "pending"
    # Rejecting the speech edit retains all speech and captions.
    assert len(compile_plan(select_cuts(plan, []))["cues"]) == 3
    selected = select_cuts(plan, [1])
    execution = compile_plan(selected)
    assert [c["source_segment_ids"] for c in execution["cues"]] == [[1], [3]]
    assert execution["cues"][-1]["start_ms"] < 6000
    rendered = tmp_path / "render"
    approval = tmp_path / "approved.json"
    approve(out / "plan.json", [1], approval)
    render(*load_approved(approval), rendered, approved=True)
    assert probe(rendered / "edited.mp4")["duration"] == pytest.approx(
        execution["output_duration_seconds"], abs=0.04
    )
    bundle = tmp_path / "capcut"
    export_capcut(approval, rendered, bundle)
    assert verify_bundle(bundle)["status"] == "verified"
    assert digest(source) == original
    # The exception cannot silently expand into the neighboring retained speech.
    data = selected.model_dump()
    data["cuts"][0]["end_frame"] = 200
    with pytest.raises(ValidationError, match="overlaps"):
        type(plan).model_validate(data)


@pytest.mark.parametrize("invalid", ["hash", "missing", "unknown", "nonconsecutive", "duplicate"])
def test_invalid_response_creates_no_output(agent_exchange, tmp_path, invalid):
    _, exchange, reply, response = agent_exchange
    if invalid == "hash":
        response["request_sha256"] = "wrong"
    elif invalid == "missing":
        response["segments"].pop()
    elif invalid == "unknown":
        response["proposals"] = [proposal(segment_ids=[999])]
    elif invalid == "nonconsecutive":
        response["proposals"] = [proposal(segment_ids=[1, 3])]
    else:
        response["proposals"] = [proposal(), proposal()]
    reply.write_text(json.dumps(response))
    with pytest.raises((UserError, ValidationError)):
        import_agent(exchange / "request.json", reply, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_changed_artifact_and_nonoverwrite(agent_exchange, tmp_path):
    _, exchange, reply, _ = agent_exchange
    out = tmp_path / "draft"
    import_agent(exchange / "request.json", reply, out)
    with pytest.raises(FileExistsError):
        import_agent(exchange / "request.json", reply, out)
    base = exchange / "base-plan.json"
    base.write_text(base.read_text() + "\n")
    with pytest.raises(UserError, match="changed"):
        import_agent(exchange / "request.json", reply, tmp_path / "changed")


def test_wait_speed_and_overlap_rejection(agent_exchange, tmp_path):
    _, exchange, reply, response = agent_exchange
    response["proposals"] = [proposal(kind="speed_wait", segment_ids=[], candidate_id=1, rate=3)]
    reply.write_text(json.dumps(response))
    out = tmp_path / "speed"
    report = import_agent(exchange / "request.json", reply, out)
    assert report["preview_speeds"] == [1] and report["proposed_seconds"] < 10
    plan = load_plan(out / "plan.json")
    assert len(compile_plan(select_cuts(plan, [], [1]))["cues"]) == 3
    response["proposals"].append(proposal(kind="cut_wait", id=2, segment_ids=[], candidate_id=1))
    reply.write_text(json.dumps(response))
    with pytest.raises(ValidationError, match="overlap"):
        import_agent(exchange / "request.json", reply, tmp_path / "conflict")
    assert not (tmp_path / "conflict").exists()


def test_failed_export_can_retry_same_path(agent_exchange, tmp_path):
    source, _, _, _ = agent_exchange
    run = next((source.parent / "runs").iterdir())
    output = tmp_path / "retry-export"
    with pytest.raises(FileNotFoundError):
        export_agent(tmp_path / "missing-run", output, "Preserve the demo")
    assert not output.exists()
    assert not list(tmp_path.glob(".agent-export-*"))
    export_agent(run, output, "Preserve the demo")
    assert (output / "request.json").is_file()
    original = (output / "request.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_agent(run, output, "Different goal")
    assert (output / "request.json").read_bytes() == original


def test_invalid_glossary_leaves_no_export(agent_exchange, tmp_path):
    source, _, _, _ = agent_exchange
    run = next((source.parent / "runs").iterdir())
    glossary = tmp_path / "bad.yaml"
    glossary.write_text("terms: [{source: '', target: Save}]")
    output = tmp_path / "bad-export"
    with pytest.raises(ValidationError):
        export_agent(run, output, "Preserve the demo", glossary=glossary)
    assert not output.exists()
    assert not list(tmp_path.glob(".agent-export-*"))
