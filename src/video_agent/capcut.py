"""Local file-exchange adapter. Never writes CapCut's internal project files."""

import plistlib
import shutil
from pathlib import Path

from .media import UserError
from .pipeline import digest
from .planning import fingerprint, load_approved, plan_data
from .subtitles import read_json, save_new, srt_time

DOCUMENTATION = "https://www.capcut.com/help/how-to-import-subtitles"


def capcut_info(app: Path | None = None) -> dict:
    candidates = (
        [app]
        if app
        else [Path("/Applications/CapCut.app"), Path.home() / "Applications/CapCut.app"]
    )
    result = {
        "installed": False,
        "version": None,
        "build": None,
        "app_path": None,
        "adapter": "local_file_exchange",
        "native_project_generation": False,
        "ui_import_verified": False,
        "documentation": DOCUMENTATION,
    }
    for candidate in candidates:
        plist = candidate / "Contents/Info.plist"
        if plist.is_file():
            with plist.open("rb") as stream:
                data = plistlib.load(stream)
            result.update(
                installed=True,
                version=data.get("CFBundleShortVersionString"),
                build=data.get("CFBundleVersion"),
                app_path=str(candidate.resolve()),
            )
            break
    return result


def expected_srt(execution: dict) -> str:
    body = "\n\n".join(
        f"{c['id']}\n{srt_time(c['start_ms'])} --> {srt_time(c['end_ms'])}\n{c['text']}"
        for c in execution["cues"]
    )
    return body + ("\n" if body else "")


def verify_render(approved: Path, rendered: Path) -> tuple[dict, dict]:
    plan, execution = load_approved(approved)
    payload = {"plan": plan_data(plan), "execution": execution}
    metrics = read_json(rendered / "render_metrics.json")
    manifest = read_json(rendered / "render_manifest.json")
    if (
        not isinstance(metrics, dict)
        or metrics.get("status") != "completed"
        or metrics.get("approved") is not True
        or metrics.get("approval_sha256") != fingerprint(payload)
    ):
        raise UserError("Render is incomplete, unapproved or belongs to another approval")
    if plan_data(plan) != plan_data(type(plan).model_validate(read_json(rendered / "plan.json"))):
        raise UserError("Rendered plan does not match approval")
    if read_json(rendered / "execution.json") != execution:
        raise UserError("Rendered timeline does not match approval")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("completed"), dict):
        raise UserError("Invalid render integrity manifest")
    for name in ("edited", "preview"):
        if digest(rendered / f"{name}.mp4") != manifest["completed"].get(name):
            raise UserError(f"Rendered {name}.mp4 failed integrity verification")
    if (rendered / "subtitles.en.srt").read_text(encoding="utf-8") != expected_srt(execution):
        raise UserError("Subtitles do not match the approved output timeline")
    return payload, metrics


def export_capcut(
    approved: Path,
    rendered: Path,
    output: Path,
    include_source: bool = False,
    include_preview: bool = False,
) -> dict:
    payload, metrics = verify_render(approved, rendered)
    plan, execution = payload["plan"], payload["execution"]
    source = Path(plan["source"])
    if include_source and digest(source) != plan["source_sha256"]:
        raise UserError("Original source changed; cannot include it")
    output.mkdir(parents=True, exist_ok=False)
    copies = {
        "edited.mp4": rendered / "edited.mp4",
        "subtitles.en.srt": rendered / "subtitles.en.srt",
        "approved-plan.json": approved,
    }
    if include_preview:
        copies["preview.mp4"] = rendered / "preview.mp4"
    if include_source:
        copies["source" + source.suffix.lower()] = source
    for name, src in copies.items():
        expected = digest(src)
        shutil.copyfile(src, output / name)
        if digest(output / name) != expected:
            raise UserError(f"Copy verification failed: {name}; export is incomplete")
    save_new(output / "execution.json", execution)
    save_new(
        output / "markers.json",
        {
            "schema_version": "1.0",
            "format": "video-agent-reference-only",
            "note": "Reference metadata; this is not a CapCut marker import format.",
            "fps": execution["fps"],
            "spans": [
                {
                    **span,
                    "source_start_seconds": span["source_start_frame"] / execution["fps"],
                    "source_end_seconds": span["source_end_frame"] / execution["fps"],
                    "output_start_seconds": span["output_start_frame"] / execution["fps"],
                    "output_end_seconds": span["output_end_frame"] / execution["fps"],
                }
                for span in execution["kept"]
            ],
            "accepted_cuts": [c for c in plan["cuts"] if c["decision"] == "accepted"],
            "accepted_speeds": [s for s in plan.get("speeds", []) if s["decision"] == "accepted"],
        },
    )
    info = capcut_info()
    (output / "IMPORT.md").write_text(
        "# CapCut local import\n\n"
        "1. Create a new local CapCut project.\n"
        "2. Import edited.mp4 and add it to the timeline at 00:00.\n"
        "3. With the playhead at 00:00, choose Captions > Add captions > Import file, "
        "select subtitles.en.srt if it has cues, then click the asset’s lower-right add control.\n"
        "4. Check caption times, wording, placement and contrast. SRT contains no styling. "
        "Save the project locally.\n\n"
        "Keep the media bundle in a stable location: CapCut may reference these files.\n"
        "Do not use preview.mp4 as the editable-caption source; its captions are burned in.\n\n"
        "Subtitles are separate editable text. Cuts and speed changes are baked into edited.mp4; "
        "this adapter does NOT create independently editable original timeline clips. "
        "approved-plan.json, execution.json and markers.json are reference/reconstruction data, "
        "not CapCut project files. No cloud sync or upload is required by this adapter.\n\n"
        f"Official subtitle import instructions: {DOCUMENTATION}\n"
        f"Detected CapCut version: {info['version'] or 'not installed'}. "
        "Detection alone does not verify UI compatibility.\n",
        encoding="utf-8",
    )
    # Completion manifest is written last. No media processing or inference occurs here.
    files = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    result = {
        "schema_version": "1.0",
        "adapter": "capcut-file-exchange",
        "status": "completed",
        "files": files,
        "approval_sha256": metrics["approval_sha256"],
        "output_duration_seconds": execution["output_duration_seconds"],
        "subtitle_cues": len(execution["cues"]),
        "source_included": include_source,
        "preview_included": include_preview,
        "native_project": False,
        "capcut": info,
        "external_requests": 0,
    }
    save_new(output / "bundle.json", result)
    verify_bundle(output)
    return result


def verify_bundle(folder: Path) -> dict:
    bundle = read_json(folder / "bundle.json")
    if (
        not isinstance(bundle, dict)
        or bundle.get("schema_version") != "1.0"
        or bundle.get("adapter") != "capcut-file-exchange"
        or bundle.get("status") != "completed"
        or not isinstance(bundle.get("files"), dict)
    ):
        raise UserError("Unsupported or incomplete bundle")
    required = {
        "edited.mp4",
        "subtitles.en.srt",
        "approved-plan.json",
        "execution.json",
        "markers.json",
        "IMPORT.md",
    }
    if not required <= set(bundle["files"]):
        raise UserError("Bundle is missing required files")
    for name, expected in bundle["files"].items():
        if Path(name).name != name or name in {".", ".."} or "/" in name or "\\" in name:
            raise UserError("Unsafe filename in bundle manifest")
        path = folder / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise UserError(f"Bundle integrity check failed: {name}")
    plan, execution = load_approved(folder / "approved-plan.json")
    if fingerprint({"plan": plan_data(plan), "execution": execution}) != bundle["approval_sha256"]:
        raise UserError("Bundle approval mismatch")
    if read_json(folder / "execution.json") != execution:
        raise UserError("Bundle timeline mismatch")
    if (folder / "subtitles.en.srt").read_text(encoding="utf-8") != expected_srt(execution):
        raise UserError("Bundle subtitle timeline mismatch")
    return {
        "status": "verified",
        "files_checked": len(bundle["files"]),
        "native_project": False,
        "ui_import_verified": False,
    }
