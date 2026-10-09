"""Atomic local UI checkpoints; tokens are never persisted."""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .media import UserError


def atomic_json(path, data):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root = root / "sessions"
        self.root.mkdir(exist_ok=True)

    def save(self, state, media):
        if not state.get("project_id"):
            return
        state["updated_at"] = datetime.now(UTC).isoformat()
        atomic_json(
            self.root / f"{state['project_id']}.json",
            {"version": 1, "state": state, "media": {k: str(v) for k, v in media.items()}},
        )
        atomic_json(self.root / "current.json", {"id": state["project_id"]})

    def read(self, key):
        if (
            not isinstance(key, str)
            or len(key) != 32
            or any(c not in "0123456789abcdef" for c in key)
        ):
            raise UserError("잘못된 작업 ID입니다.")
        data = json.loads((self.root / f"{key}.json").read_text())
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("state"), dict)
            or not isinstance(data.get("media"), dict)
            or data["state"].get("project_id") != key
            or not isinstance(data["state"].get("busy"), bool)
        ):
            raise UserError("작업 기록 형식을 확인할 수 없습니다.")
        return data["state"], {k: Path(v) for k, v in data["media"].items()}

    def current(self):
        path = self.root / "current.json"
        return json.loads(path.read_text())["id"] if path.exists() else None

    def listing(self):
        result = []
        for path in self.root.glob("*.json"):
            if path.name == "current.json":
                continue
            try:
                state, _ = self.read(path.stem)
                result.append(
                    {
                        k: state.get(k)
                        for k in ("project_id", "title", "updated_at", "message", "error")
                    }
                )
            except (OSError, ValueError, KeyError, TypeError, UserError):
                result.append({"project_id": path.stem, "title": "손상된 작업 기록", "error": True})
        return sorted(result, key=lambda item: item.get("updated_at") or "", reverse=True)
