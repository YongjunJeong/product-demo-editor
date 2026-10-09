"""Single-user loopback UI. Reuses the CLI pipeline; never serves arbitrary paths."""

import copy
import json
import mimetypes
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .capcut import export_capcut, verify_bundle, verify_render
from .media import UserError
from .pipeline import analyze, digest
from .planning import (
    EditPlan,
    add_speed,
    approve,
    compile_plan,
    create_plan,
    load_approved,
    load_plan,
    select_cuts,
    summary,
)
from .rendering import render
from .schema import Config
from .subtitles import export_translation, import_translation, read_json, save_new
from .ui_store import Store

STATIC = Path(__file__).parent / "static"


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.state = {"busy": False, "error": None, "message": "영상을 선택해 시작하세요."}
        self.media = {}
        self.store = Store(self.root)
        try:
            current = self.store.current()
            if current:
                self.restore(current)
        except (OSError, ValueError, KeyError, TypeError, UserError) as exc:
            self.state.update(
                error=str(exc), message="저장된 작업을 복구하지 못했습니다. 작업 목록을 확인하세요."
            )

    def restore(self, key):
        state, media = self.store.read(key)
        interrupted = state.get("busy", False)
        state["busy"] = False
        if interrupted:
            state.update(
                error="서버 종료로 작업이 중단되었습니다. 재시도할 수 있습니다.",
                message="중단된 작업 복구",
                retry=state.get("last_action"),
            )
        try:
            if state.get("plan_path"):
                plan = load_plan(Path(state["plan_path"]))
                if plan.model_dump() != EditPlan.model_validate(state["plan"]).model_dump():
                    raise UserError("저장 후 편집 계획이 변경되었습니다.")
                state["plan"] = plan.model_dump()
                if digest(Path(plan.source)) != plan.source_sha256:
                    raise UserError("원본이 변경되었습니다. 다시 분석하세요.")
            if state.get("review"):
                review = state["review"]
                if (
                    review.get("preview_path")
                    and digest(Path(review["preview_path"])) != review["preview_sha256"]
                ):
                    raise UserError("미리보기 영상이 변경되었거나 누락되었습니다.")
                if digest(Path(review["path"])) != review["sha256"]:
                    raise UserError("검토 계획이 변경되었습니다.")
            if state.get("approved"):
                load_approved(Path(state["approved"]))
            if state.get("rendered"):
                verify_render(Path(state["approved"]), Path(state["rendered"]))
            if state.get("bundle"):
                verify_bundle(Path(state["bundle"]))
        except (OSError, ValueError, KeyError, UserError) as exc:
            state.update(
                review=None,
                approved=None,
                rendered=None,
                bundle=None,
                preview_media=None,
                final_media=None,
                retry=None,
                error=str(exc),
                message="파일 변경 또는 누락: 다시 검토가 필요합니다.",
            )
        self.state, self.media = state, media

    def progress(self, stage):
        labels = {
            "metadata": "영상 정보 확인",
            "silence": "무음 구간 분석",
            "scenes": "화면 전환 분석",
            "transcript": "한국어 음성 인식",
            "edited": "편집 영상 렌더",
            "preview": "자막 미리보기 렌더",
        }
        with self.lock:
            self.state["message"] = labels.get(stage, stage) + " 진행 중…"
            self.state["stage"] = stage

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            result["projects"] = self.store.listing()
            return result

    def folder(self):
        path = self.root / uuid.uuid4().hex
        path.mkdir()
        return path

    def register(self, path):
        key = uuid.uuid4().hex
        with self.lock:
            self.media[key] = Path(path).resolve()
        return key

    def submit(self, action, data):
        if action not in {
            "retry",
            "open-project",
            "save-draft",
            "analyze",
            "load-run",
            "load-plan",
            "translate",
            "speed",
            "preview",
            "approve",
            "render",
            "export",
        }:
            raise UserError("Unknown action")
        with self.lock:
            if self.state["busy"]:
                raise UserError("작업이 진행 중입니다. 완료 후 다시 시도하세요.")
            if action == "open-project":
                self.restore(data["id"])
                self.store.save(self.state, self.media)
                return
            retrying = action == "retry"
            if action == "retry":
                retry = self.state.get("retry")
                if not retry:
                    raise UserError("재시도할 작업이 없습니다.")
                action, data = retry["action"], retry["data"]
            if not retrying and action in {"analyze", "load-run", "load-plan"}:
                self.state = {
                    "project_id": uuid.uuid4().hex,
                    "title": Path(data.get("source") or data.get("path") or "새 작업").name,
                }
                self.media = {}
            self.state["last_action"] = {"action": action, "data": copy.deepcopy(data)}
            self.state["retry"] = None
            self.state["started_at"] = datetime.now(UTC).isoformat()
            if action in {"analyze", "load-run", "load-plan", "translate", "speed", "preview"}:
                self.state.update(
                    review=None,
                    approved=None,
                    rendered=None,
                    bundle=None,
                    preview_media=None,
                    final_media=None,
                )
            self.state.update(
                busy=True, error=None, message="처리 중… 창을 닫아도 서버에서 계속됩니다."
            )
            self.store.save(self.state, self.media)
        self.pool.submit(self.run, action, data)

    def run(self, action, data):
        try:
            if action == "open-project":
                with self.lock:
                    self.restore(data["id"])
                result = {}
            elif action == "save-draft":
                if not self.state.get("plan") or any(
                    not isinstance(data.get(k), list)
                    for k in ("cuts", "speeds", "texts", "segments")
                ):
                    raise UserError("편집 계획과 입력 초안 형식을 확인하세요.")
                result = {
                    "draft": data,
                    "review": None,
                    "approved": None,
                    "rendered": None,
                    "bundle": None,
                    "preview_media": None,
                    "final_media": None,
                    "message": "입력 초안을 저장했습니다. 미리보기 후 승인하세요.",
                }
            else:
                result = self.execute(action, data)
            with self.lock:
                if action not in {"open-project", "save-draft"}:
                    result["draft"] = None
                self.state.update(result, busy=False)
                if action != "open-project":
                    self.state["error"] = None
                self.store.save(self.state, self.media)
        except Exception as exc:  # noqa: BLE001 -- job boundary must clear busy after any failure
            with self.lock:
                self.state.update(
                    busy=False,
                    error=str(exc),
                    message="작업을 완료하지 못했습니다.",
                    retry={"action": action, "data": data} if action != "open-project" else None,
                )
                self.store.save(self.state, self.media)

    def plan_result(self, path):
        plan = load_plan(path)
        return {
            "plan_path": str(path),
            "plan": plan.model_dump(),
            "source_media": self.register(plan.source),
            "review": None,
            "approved": None,
            "rendered": None,
            "bundle": None,
            "preview_media": None,
            "final_media": None,
        }

    def from_run(self, run):
        folder = self.folder()
        plan = folder / "plan.json"
        create_plan(run, None, plan)
        result = self.plan_result(plan)
        transcript = read_json(run / "transcript.json")
        result.update(run=str(run.resolve()), transcript=transcript, exchange=None, translations=[])
        if transcript["status"] == "completed" and transcript["segments"]:
            exchange = folder / "translation"
            export_translation(run / "transcript.json", exchange)
            result["exchange"] = str(exchange)
        return result

    def execute(self, action, data):
        state = self.snapshot()
        if action == "analyze":
            if type(data.get("transcribe")) is not bool:
                raise UserError("transcribe must be a boolean")
            run = analyze(
                Path(data["source"]).expanduser(),
                self.root / "runs",
                Config(transcribe=data["transcribe"]),
                Path.home() / ".cache/video-agent/models",
                progress=self.progress,
            )
            result = self.from_run(run)
            result["message"] = "분석 완료. 번역을 입력하거나 편집 후보를 검토하세요."
            return result
        if action == "load-run":
            return {
                **self.from_run(Path(data["path"]).expanduser()),
                "message": "분석 결과를 불러왔습니다.",
            }
        if action == "load-plan":
            # Snapshot the input so subsequent external edits cannot change this UI draft.
            plan = load_plan(Path(data["path"]).expanduser())
            path = self.folder() / "plan.json"
            save_new(path, plan.model_dump())
            return {
                **self.plan_result(path),
                "run": None,
                "transcript": None,
                "exchange": None,
                "translations": [],
                "message": "편집 계획을 불러왔습니다. 모든 선택은 다시 검토하세요.",
            }
        if action == "translate":
            if not state.get("exchange"):
                raise UserError("전사가 있는 분석 결과를 먼저 불러오세요.")
            exchange = Path(state["exchange"])
            response = read_json(exchange / "response.template.json")
            response["segments"] = data["segments"]
            folder = self.folder()
            save_new(folder / "response.json", response)
            import_translation(
                exchange / "request.json", folder / "response.json", folder / "subtitles"
            )
            path = folder / "plan.json"
            create_plan(Path(state["run"]), folder / "subtitles/subtitles.en.json", path)
            return {
                **self.plan_result(path),
                "translations": response["segments"],
                "message": "영어 자막 초안을 저장했습니다. 컷·배속 선택을 다시 검토하세요.",
            }
        if action == "speed":
            path = self.folder() / "plan.json"
            add_speed(
                Path(state["plan_path"]),
                float(data["start"]),
                float(data["end"]),
                float(data["rate"]),
                data["audio"],
                path,
            )
            return {
                **self.plan_result(path),
                "message": "배속 후보를 추가했습니다. 적용할 항목을 선택하세요.",
            }
        if action == "preview":
            plan = load_plan(Path(state["plan_path"]))
            texts = data["texts"]
            if not isinstance(texts, list) or len(texts) != len(plan.cues):
                raise UserError("자막 개수가 현재 계획과 일치하지 않습니다.")
            raw = plan.model_dump()
            for cue, text in zip(raw["cues"], texts, strict=True):
                cue["text"] = text
            selected = select_cuts(EditPlan.model_validate(raw), data["cuts"], data["speeds"])
            folder = self.folder()
            path = folder / "plan.json"
            save_new(path, selected.model_dump())
            execution = compile_plan(selected)
            render(selected, execution, folder / "preview", approved=False, progress=self.progress)
            review = {
                "id": uuid.uuid4().hex,
                "sha256": digest(path),
                "preview_path": str(folder / "preview/preview.mp4"),
                "preview_sha256": digest(folder / "preview/preview.mp4"),
                "path": str(path),
                "cuts": data["cuts"],
                "speeds": data["speeds"],
                "summary": summary(selected, data["cuts"], data["speeds"]),
            }
            return {
                **self.plan_result(path),
                "review": review,
                "preview_media": self.register(folder / "preview/preview.mp4"),
                "message": "미리보기 완료. 영상과 자막을 확인한 뒤 이 버전을 승인하세요.",
            }
        if action == "approve":
            review = state.get("review")
            if not review or data.get("review_id") != review["id"]:
                raise UserError("현재 미리보기와 일치하는 검토 ID가 필요합니다.")
            if digest(Path(review["path"])) != review["sha256"]:
                raise UserError(
                    "미리보기 이후 계획 파일이 변경되었습니다. 다시 미리보기를 생성하세요."
                )
            path = self.folder() / "approved.json"
            approve(Path(review["path"]), review["cuts"], path, review["speeds"])
            return {
                "approved": str(path),
                "rendered": None,
                "bundle": None,
                "message": "이 버전의 편집을 승인했습니다. 최종 영상을 생성할 수 있습니다.",
            }
        if action == "render":
            if not state.get("approved"):
                raise UserError("미리보기를 검토하고 먼저 승인하세요.")
            plan, execution = load_approved(Path(state["approved"]))
            output = self.folder() / "render"
            render(plan, execution, output, approved=True, progress=self.progress)
            return {
                "rendered": str(output),
                "bundle": None,
                "final_media": self.register(output / "edited.mp4"),
                "message": "최종 영상이 생성되었습니다. CapCut용 파일을 내보낼 수 있습니다.",
            }
        if action == "export":
            if not state.get("approved") or not state.get("rendered"):
                raise UserError("승인된 최종 렌더가 필요합니다.")
            output = self.folder() / "capcut"
            export_capcut(Path(state["approved"]), Path(state["rendered"]), output)
            return {"bundle": str(output), "message": "CapCut용 영상·SRT 내보내기 완료."}
        raise UserError("Unknown action")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        # Media URLs carry the per-session token. Never log URLs.
        pass

    def send(self, status, body, kind="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; frame-ancestors 'none'; connect-src 'self'",
        )
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        host = f"127.0.0.1:{self.server.server_port}"
        return (
            self.headers.get("Host") == host
            and self.headers.get("Origin", f"http://{host}") == f"http://{host}"
        )

    def authorized(self, token=None):
        supplied = token or self.headers.get("X-Video-Token", "")
        return self.trusted() and secrets.compare_digest(supplied, self.server.workspace.token)

    def do_GET(self):
        url = urlsplit(self.path)
        if not self.trusted():
            return self.send(403, b'{"error":"Invalid origin or host"}')
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if url.path in assets:
            name, kind = assets[url.path]
            return self.send(200, (STATIC / name).read_bytes(), kind)
        if url.path == "/api/files" and self.authorized():
            try:
                folder = (
                    Path(parse_qs(url.query).get("path", [str(Path.home())])[0])
                    .expanduser()
                    .resolve()
                )
                entries = sorted(
                    (p for p in folder.iterdir() if not p.name.startswith(".")),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
                items = [
                    {"name": p.name, "path": str(p), "directory": p.is_dir()}
                    for p in entries
                    if p.is_dir()
                    or p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
                ]
                return self.send(
                    200,
                    json.dumps(
                        {"path": str(folder), "parent": str(folder.parent), "items": items}
                    ).encode(),
                )
            except (OSError, ValueError) as exc:
                return self.send(400, json.dumps({"error": str(exc)}).encode())
        if url.path == "/api/state" and self.authorized():
            return self.send(200, json.dumps(self.server.workspace.snapshot()).encode())
        if url.path.startswith("/media/") and self.authorized(
            parse_qs(url.query).get("token", [""])[0]
        ):
            return self.serve_media(url.path.removeprefix("/media/"))
        self.send(403, b'{"error":"Session token required"}')

    def serve_media(self, key):
        with self.server.workspace.lock:
            path = self.server.workspace.media.get(key)
        if path is None or not path.is_file():
            return self.send(404, b'{"error":"Media not found"}')
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            try:
                unit, value = range_header.split("=", 1)
                left, right = value.split("-", 1)
                if unit != "bytes" or "," in value:
                    raise ValueError
                if left:
                    start = int(left)
                    end = min(int(right), end) if right else end
                else:
                    count = int(right)
                    if count <= 0:
                        raise ValueError
                    start = max(0, size - count)
                if start < 0 or start > end:
                    raise ValueError
                status = 206
            except ValueError:
                return self.send(416, b"", extra={"Content-Range": f"bytes */{size}"})
        self.send_response(status)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self.authorized():
            return self.send(403, b'{"error":"Invalid session or origin"}')
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= 1024 * 1024
                or self.headers.get("Content-Type") != "application/json"
            ):
                raise UserError("Expected JSON up to 1 MB")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or not self.path.startswith("/api/"):
                raise UserError("Invalid request")
            self.server.workspace.submit(self.path.removeprefix("/api/"), data)
            self.send(202, b'{"status":"started"}')
        except (ValueError, UserError, OSError, KeyError, TypeError) as exc:
            self.send(400, json.dumps({"error": str(exc)}).encode())


def make_server(root: Path, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.workspace = Workspace(root)
    return server


def serve(root: Path, port=8765):
    server = make_server(root, port)
    print(
        f"Local Video Agent: http://127.0.0.1:{server.server_port}/#{server.workspace.token}",
        flush=True,
    )
    print(
        f"작업 폴더: {server.workspace.root}\n종료: Ctrl+C (진행 중인 작업은 완료 후 종료)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.workspace.pool.shutdown(wait=True)
