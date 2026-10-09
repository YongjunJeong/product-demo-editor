import json
import plistlib
import shutil
import subprocess
import sys

import pytest

from video_agent import capcut
from video_agent.media import UserError
from video_agent.pipeline import digest
from video_agent.planning import Cue, EditPlan, approve, load_approved
from video_agent.rendering import render


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = tmp_path_factory.mktemp("capcut")
    source = root / "원본 영상.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=25:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    plan = EditPlan(
        source=str(source),
        source_sha256=digest(source),
        source_duration_ms=2000,
        fps=25,
        evidence={},
        speech_protection_available=False,
        protected_speech=[],
        cues=[Cue(id=1, source_segment_ids=[1], start_ms=400, end_ms=1400, text="Hello demo")],
        cuts=[],
    )
    draft = root / "draft.json"
    draft.write_text(plan.model_dump_json())
    approved = root / "approved.json"
    approve(draft, [], approved)
    selected, execution = load_approved(approved)
    output = root / "render"
    render(selected, execution, output, approved=True)
    return approved, output, source


def test_export_portable_copy_without_capcut(rendered, tmp_path, monkeypatch):
    approved, output, source = rendered
    missing = capcut_info_missing(tmp_path)
    monkeypatch.setattr(capcut, "capcut_info", lambda: missing)
    bundle = tmp_path / "교환 폴더"
    manifest = capcut.export_capcut(approved, output, bundle, True, True)
    assert not manifest["capcut"]["installed"]
    assert not manifest["native_project"]
    assert not manifest["capcut"]["ui_import_verified"]
    assert manifest["subtitle_cues"] == 1
    assert manifest["output_duration_seconds"] == 2
    assert digest(bundle / "edited.mp4") == digest(output / "edited.mp4")
    assert digest(bundle / "source.mp4") == digest(source)
    assert digest(bundle / "preview.mp4") == digest(output / "preview.mp4")
    moved = tmp_path / "relocated"
    shutil.move(bundle, moved)
    assert capcut.verify_bundle(moved)["status"] == "verified"
    with pytest.raises(FileExistsError):
        capcut.export_capcut(approved, output, moved)


def capcut_info_missing(root):
    return capcut.capcut_info(root / "missing.app")


def test_install_detection_is_not_compatibility_verification(tmp_path):
    app = tmp_path / "CapCut.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleShortVersionString": "8.7.0",
                "CFBundleVersion": "123",
            }
        )
    )
    info = capcut.capcut_info(app)
    assert info["installed"] and info["version"] == "8.7.0"
    assert not info["ui_import_verified"]
    assert not capcut_info_missing(tmp_path)["installed"]


@pytest.mark.parametrize(
    "damage",
    [
        "unapproved",
        "incomplete",
        "wrong-approval",
        "srt",
        "video",
        "execution",
        "metrics-type",
        "manifest-type",
    ],
)
def test_invalid_render_rejected_before_export(rendered, tmp_path, damage):
    approved, output, _ = rendered
    local = tmp_path / "render"
    shutil.copytree(output, local)
    metrics_path = local / "render_metrics.json"
    metrics = json.loads(metrics_path.read_text())
    if damage == "unapproved":
        metrics["approved"] = False
    elif damage == "incomplete":
        metrics["status"] = "failed"
    elif damage == "wrong-approval":
        metrics["approval_sha256"] = "wrong"
    elif damage == "metrics-type":
        metrics = []
    elif damage == "manifest-type":
        (local / "render_manifest.json").write_text('{"completed": []}')
    elif damage == "srt":
        (local / "subtitles.en.srt").write_text("wrong times")
    elif damage == "video":
        (local / "edited.mp4").write_bytes(b"changed")
    elif damage == "execution":
        (local / "execution.json").write_text("{}")
    metrics_path.write_text(json.dumps(metrics))
    with pytest.raises(UserError):
        capcut.export_capcut(approved, local, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("damage", ["tamper", "missing", "traversal", "symlink"])
def test_bundle_corruption_rejected(rendered, tmp_path, damage):
    approved, output, _ = rendered
    folder = tmp_path / "bundle"
    capcut.export_capcut(approved, output, folder)
    assert not (folder / "preview.mp4").exists()
    assert not (folder / "source.mp4").exists()
    manifest_path = folder / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    if damage == "tamper":
        (folder / "edited.mp4").write_bytes(b"changed")
    elif damage == "missing":
        (folder / "execution.json").unlink()
    elif damage == "traversal":
        manifest["files"]["../outside"] = "dummy"
    else:
        (folder / "edited.mp4").unlink()
        (folder / "edited.mp4").symlink_to(output / "edited.mp4")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(UserError):
        capcut.verify_bundle(folder)


def test_changed_original_cannot_be_included(rendered, tmp_path):
    approved, output, source = rendered
    original = source.read_bytes()
    try:
        source.write_bytes(b"changed")
        with pytest.raises(UserError, match="Original source changed"):
            capcut.export_capcut(approved, output, tmp_path / "bundle", include_source=True)
        assert not (tmp_path / "bundle").exists()
    finally:
        source.write_bytes(original)


def test_malformed_bundle_cli_error(tmp_path):
    (tmp_path / "bundle.json").write_text("[]")
    result = subprocess.run(
        [sys.executable, "-m", "video_agent.cli", "verify-bundle", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
