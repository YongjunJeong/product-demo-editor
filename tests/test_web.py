import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from video_agent.web import make_server


@pytest.fixture
def server(tmp_path):
    server = make_server(tmp_path / "ui", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    server.workspace.pool.shutdown(wait=True)
    thread.join()


def request(server, method, path, body=None, token=True, origin=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Video-Token"] = server.workspace.token
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    response = conn.getresponse()
    result = response.status, response.read()
    conn.close()
    return result


def wait(server):
    deadline = time.monotonic() + 30
    while server.workspace.snapshot()["busy"]:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return server.workspace.snapshot()


def task(server, name, body):
    status, response = request(server, "POST", f"/api/{name}", body)
    assert status == 202, response
    state = wait(server)
    assert not state.get("error"), state.get("error")
    return state


def test_loopback_auth_origin_and_no_arbitrary_files(server):
    assert server.server_address[0] == "127.0.0.1"
    assert request(server, "GET", "/")[0] == 200
    assert request(server, "GET", "/api/state", token=False)[0] == 403
    assert request(server, "POST", "/api/analyze", {}, token=False)[0] == 403
    assert request(server, "POST", "/api/analyze", {}, origin="https://example.com")[0] == 403
    assert request(server, "GET", "/api/state", host="example.com")[0] == 403
    assert request(server, "GET", "/../../etc/passwd")[0] == 403
    assert request(server, "GET", "/media/unknown")[0] == 404
    assert request(server, "GET", "/api/state")[0] == 200


def test_media_ranges(server, tmp_path):
    path = tmp_path / "media.mp4"
    path.write_bytes(b"0123456789")
    key = server.workspace.register(path)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    url = f"/media/{key}?token={server.workspace.token}"
    for value, status, expected in [
        ("bytes=2-5", 206, b"2345"),
        ("bytes=-3", 206, b"789"),
        ("bytes=99-", 416, b""),
    ]:
        conn.request("GET", url, headers={"Range": value})
        response = conn.getresponse()
        assert response.status == status
        assert response.read() == expected
    conn.close()


def test_real_ui_pipeline_approval_and_invalidation(server, tmp_path):
    # Use the existing synthetic fixture generator, never user media.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fixture", Path(__file__).parents[1] / "scripts/make_fixture.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    source = tmp_path / "ui 합성.mp4"
    fixture.make_fixture(source)
    state = task(server, "analyze", {"source": str(source), "transcribe": False})
    assert state["plan"]["cuts"] and not state["approved"]
    request(server, "POST", "/api/render", {})
    assert "먼저 승인" in wait(server)["error"]
    task(server, "speed", {"start": 2, "end": 6, "rate": 2, "audio": "mute"})
    state = task(server, "preview", {"cuts": [1], "speeds": [1], "texts": []})
    review_id = state["review"]["id"]
    assert state["review"]["summary"]["output_seconds"] == 6.4
    review_path = Path(state["review"]["path"])
    original = review_path.read_bytes()
    review_path.write_bytes(original + b" ")
    request(server, "POST", "/api/approve", {"review_id": review_id})
    assert "변경" in wait(server)["error"]
    review_path.write_bytes(original)
    request(server, "POST", "/api/approve", {"review_id": "stale"})
    assert wait(server)["error"]
    state = task(server, "approve", {"review_id": review_id})
    assert Path(state["approved"]).is_file()
    state = task(server, "render", {})
    assert Path(state["rendered"], "edited.mp4").is_file()
    state = task(server, "export", {})
    assert Path(state["bundle"], "bundle.json").is_file()
    from video_agent.web import Workspace

    restarted = Workspace(server.workspace.root)
    try:
        assert restarted.state["approved"] == state["approved"]
        assert restarted.state["bundle"] == state["bundle"]
        assert not restarted.state["error"]
        Path(state["review"]["preview_path"]).write_bytes(b"damaged")
        restarted.restore(state["project_id"])
        assert restarted.state["approved"] is None
        assert "미리보기" in restarted.state["error"]
    finally:
        restarted.pool.shutdown()

    request(server, "POST", "/api/preview", {"cuts": [999], "speeds": [], "texts": []})
    state = wait(server)
    assert state["error"] and not state["approved"] and not state["review"]
    assert source.is_file()


def test_single_job_and_failure_recovery(server, monkeypatch):
    gate = threading.Event()
    started = threading.Event()

    def execute(*_args):
        started.set()
        gate.wait(5)
        raise ValueError("test failure")

    monkeypatch.setattr(server.workspace, "execute", execute)
    request(server, "POST", "/api/analyze", {})
    assert started.wait(2)
    assert request(server, "POST", "/api/analyze", {})[0] == 400
    gate.set()
    state = wait(server)
    assert state["error"] == "test failure"
    assert not state["busy"]


def test_restart_draft_retry_and_project_list(server, tmp_path, monkeypatch):
    from video_agent.web import Workspace

    missing = tmp_path / "missing.mp4"
    request(server, "POST", "/api/analyze", {"source": str(missing), "transcribe": False})
    failed = wait(server)
    assert failed["retry"]["action"] == "analyze"
    key = failed["project_id"]
    restored = Workspace(server.workspace.root)
    try:
        assert restored.state["project_id"] == key
        assert restored.state["retry"]["data"]["source"] == str(missing)
        assert len(restored.snapshot()["projects"]) == 1
        monkeypatch.setattr(restored, "execute", lambda *_args: {"message": "recovered"})
        restored.submit("retry", {})
        restored.pool.shutdown(wait=True)
        assert restored.state["project_id"] == key
        assert restored.state["message"] == "recovered"
        assert restored.state["error"] is None
    finally:
        restored.pool.shutdown(wait=True)


def test_interrupted_checkpoint_is_not_running_forever(tmp_path):
    from video_agent.web import Workspace

    workspace = Workspace(tmp_path)
    workspace.state.update(
        project_id="a" * 32,
        title="Interrupted",
        busy=True,
        last_action={"action": "preview", "data": {"cuts": [], "speeds": [], "texts": []}},
    )
    workspace.store.save(workspace.state, {})
    workspace.pool.shutdown()
    restored = Workspace(tmp_path)
    try:
        assert restored.state["busy"] is False
        assert restored.state["retry"]["action"] == "preview"
        assert "중단" in restored.state["error"]
    finally:
        restored.pool.shutdown()


def test_picker_requires_token_and_lists_only_videos(server, tmp_path):
    from urllib.parse import quote

    (tmp_path / "video.mp4").write_bytes(b"test")
    (tmp_path / "private.txt").write_text("test")
    path = "/api/files?path=" + quote(str(tmp_path))
    assert request(server, "GET", path, token=False)[0] == 403
    status, body = request(server, "GET", path)
    assert status == 200
    names = {item["name"] for item in json.loads(body)["items"]}
    assert "video.mp4" in names and "private.txt" not in names
    assert request(server, "POST", "/api/open-project", {"id": "../escape"})[0] == 400


def test_saved_plan_and_draft_restore(server, tmp_path):
    from video_agent.pipeline import digest
    from video_agent.planning import EditPlan
    from video_agent.web import Workspace

    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture for hash only")
    plan = EditPlan(
        source=str(source),
        source_sha256=digest(source),
        source_duration_ms=2000,
        fps=25,
        evidence={},
        speech_protection_available=False,
        protected_speech=[],
        cues=[],
        cuts=[],
    )
    path = tmp_path / "plan.json"
    path.write_text(plan.model_dump_json())
    initial = task(server, "load-plan", {"path": str(path)})
    draft = {
        "cuts": [],
        "speeds": [],
        "texts": [],
        "segments": [],
        "translationDirty": False,
        "cueDirty": False,
    }
    task(server, "save-draft", draft)
    restored = Workspace(server.workspace.root)
    try:
        assert restored.state["draft"] == draft
        assert restored.state["plan_path"] == initial["plan_path"]
        assert restored.state["source_media"] in restored.media
        source.write_bytes(b"changed")
        restored.restore(initial["project_id"])
        assert restored.state["error"]
        assert restored.state["approved"] is None
    finally:
        restored.pool.shutdown()


def test_corrupt_session_is_reported_without_crashing(tmp_path):
    from video_agent.web import Workspace

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    key = "b" * 32
    (sessions / f"{key}.json").write_text("[]")
    (sessions / "current.json").write_text(json.dumps({"id": key}))
    workspace = Workspace(tmp_path)
    try:
        assert workspace.state["error"]
        assert workspace.snapshot()["projects"][0]["title"] == "손상된 작업 기록"
        assert not workspace.state["busy"]
    finally:
        workspace.pool.shutdown()
