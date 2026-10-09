"""Release checks: actionable setup diagnostics and source-only archives without Git."""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from video_agent import cli
from video_agent.media import UserError

spec = importlib.util.spec_from_file_location(
    "package_portfolio", Path(__file__).parents[1] / "scripts/package_portfolio.py"
)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.mark.parametrize("ready", [False, True])
def test_doctor_checks_actual_renderer(monkeypatch, capsys, ready):
    monkeypatch.setattr(sys, "argv", ["video-agent", "doctor"])
    monkeypatch.setattr(cli, "versions", lambda: {"ffmpeg": "test", "faster-whisper": None})

    def renderer():
        if not ready:
            raise UserError("FFmpeg subtitles filter missing; install ffmpeg-full")
        return "/test/ffmpeg"

    monkeypatch.setattr(cli, "renderer_binary", renderer)
    assert cli.main() == (0 if ready else 2)
    report = json.loads(capsys.readouterr().out)
    assert report["render_ready"] is ready
    if not ready:
        assert "ffmpeg-full" in report["error"]


def public_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("# Synthetic project")
    (root / "pyproject.toml").write_text('[project]\nname = "test"')
    (root / "LICENSE").write_text("Test license")
    (root / "src").mkdir()
    (root / "src/main.py").write_text("print('demo')")
    (root / "work").mkdir()
    (root / "work/private.json").write_text('{"private": true}')
    (root / "src/__pycache__").mkdir()
    (root / "src/__pycache__/main.pyc").write_bytes(b"cache")
    return root


def test_archive_without_git_includes_license_and_excludes_work(tmp_path):
    root = public_source(tmp_path)
    output = tmp_path / "public.zip"
    assert packager.package(root, output) == 4
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "product-demo-editor/LICENSE" in archive.namelist()
        assert not any("private" in name or "__pycache__" in name for name in archive.namelist())
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        packager.package(root, output)
    assert output.read_bytes() == original


@pytest.mark.parametrize("kind", ["media", "secret", "env", "symlink"])
def test_archive_rejects_non_public_content_before_writing(tmp_path, kind):
    root = public_source(tmp_path)
    if kind == "media":
        (root / "src/private.mp4").write_bytes(b"not public")
    elif kind == "secret":
        (root / "src/config.json").write_text('"' + "ghp_" + "a" * 36 + '"')
    elif kind == "env":
        (root / "src/.env").write_text("PASSWORD=private")
    else:
        (root / "src/link.py").symlink_to(root / "work/private.json")
    output = tmp_path / "rejected.zip"
    with pytest.raises(ValueError):
        packager.package(root, output)
    assert not output.exists()
