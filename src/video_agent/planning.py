"""Reviewable plans, explicit approvals and a compiled frame-exact cut timeline."""

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .media import UserError
from .pipeline import digest
from .schema import StrictModel, Transcript
from .subtitles import read_json, save_new


class Cue(StrictModel):
    id: int = Field(ge=1)
    source_segment_ids: list[int] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self):
        if self.end_ms <= self.start_ms or not self.text.strip():
            raise ValueError("Cue must have positive duration and text")
        lines = self.text.splitlines()
        if len(lines) > 2 or any(not line.strip() or len(line) > 80 for line in lines):
            raise ValueError("Cue must have 1–2 non-empty lines, at most 80 characters each")
        if any(ord(c) < 32 and c != "\n" for c in self.text):
            raise ValueError("Unsupported control character in caption")
        return self


class SubtitleDocument(StrictModel):
    schema_version: Literal["1.0"]
    language: Literal["en"]
    time_base: Literal["source_milliseconds"]
    status: Literal["draft"]
    request_id: str
    source_sha256: str
    request_sha256: str
    response_sha256: str
    cues: list[Cue]


class ProtectedRange(StrictModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def valid(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Invalid protected range")
        return self


class Cut(StrictModel):
    id: int = Field(ge=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    reason: Literal["silence_candidate", "semantic_candidate"] = "silence_candidate"
    decision: Literal["pending", "accepted", "rejected"] = "pending"

    @model_validator(mode="after")
    def valid(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("Cut must have positive duration")
        return self


class Speed(StrictModel):
    id: int = Field(ge=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    rate: float = Field(gt=1, le=4)
    audio: Literal["mute", "tempo"] = "mute"
    reason: Literal["user_selected_wait"] = "user_selected_wait"
    decision: Literal["pending", "accepted", "rejected"] = "pending"

    @model_validator(mode="after")
    def valid(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("Speed interval must have positive duration")
        return self


class SpeechEdit(StrictModel):
    """An explicit exception for one reviewable, whole-segment speech cut."""

    cut_id: int = Field(ge=1)
    segment_ids: list[int] = Field(min_length=1)
    protected_ranges: list[ProtectedRange] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class EditPlan(StrictModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.0"
    source: str
    source_sha256: str
    source_duration_ms: int = Field(gt=0)
    fps: Literal[25, 30, 60] = 30
    timeline: Literal["source_cfr_frames"] = "source_cfr_frames"
    evidence: dict[str, str]
    speech_protection_available: bool
    protected_speech: list[ProtectedRange]
    cues: list[Cue]
    cuts: list[Cut]
    speeds: list[Speed] = Field(default_factory=list)
    speech_edits: list[SpeechEdit] = Field(default_factory=list)
    agent_notes: dict[str, str] = Field(default_factory=dict)

    @property
    def total_frames(self) -> int:
        return self.source_duration_ms * self.fps // 1000

    @model_validator(mode="after")
    def valid(self):
        if self.total_frames < 1:
            raise ValueError("Video is shorter than one output frame")
        semantic = {c.id for c in self.cuts if c.reason == "semantic_candidate"}
        if (semantic or self.speech_edits or self.agent_notes) and self.schema_version != "1.2":
            raise ValueError("Agent metadata and speech edits require plan schema 1.2")
        if semantic != {e.cut_id for e in self.speech_edits} or len(semantic) != len(
            self.speech_edits
        ):
            raise ValueError("Every semantic cut requires exactly one explicit speech edit")
        removed_ids = []
        for edit in self.speech_edits:
            cut = next(c for c in self.cuts if c.id == edit.cut_id)
            removed_ids.extend(edit.segment_ids)
            if len(edit.segment_ids) != len(edit.protected_ranges):
                raise ValueError("Speech edits require one protected range per segment")
            for region in edit.protected_ranges:
                if region not in self.protected_speech or not self.contains(cut, region):
                    raise ValueError("Speech edit must fully contain existing protected ranges")
        if len(removed_ids) != len(set(removed_ids)):
            raise ValueError("A speech segment cannot belong to multiple edits")
        previous = 0
        ids = set()
        for cue in self.cues:
            if cue.id in ids or cue.start_ms < previous or cue.end_ms > self.source_duration_ms:
                raise ValueError("Cues must have unique IDs, ordered non-overlapping source times")
            ids.add(cue.id)
            previous = cue.end_ms
        previous = 0
        ids = set()
        for cut in self.cuts:
            if cut.id in ids or cut.start_frame < previous or cut.end_frame > self.total_frames:
                raise ValueError("Cuts must be ordered, non-overlapping and within the source")
            previous = cut.end_frame
            ids.add(cut.id)
            if cut.decision == "accepted" and self.overlaps_protected(cut):
                raise ValueError("Accepted cut overlaps speech or a caption")
        if self.speeds and self.schema_version == "1.0":
            raise ValueError("Speed operations require plan schema 1.1 or later")
        previous = 0
        ids = set()
        for speed in self.speeds:
            if (
                speed.id in ids
                or speed.start_frame < previous
                or speed.end_frame > self.total_frames
            ):
                raise ValueError(
                    "Speed intervals must be unique, ordered, non-overlapping and within source"
                )
            ids.add(speed.id)
            previous = speed.end_frame
            if speed.decision == "accepted":
                if self.overlaps_protected(speed):
                    raise ValueError("Accepted speed overlaps speech or a caption")
                if any(
                    c.decision == "accepted"
                    and c.start_frame < speed.end_frame
                    and c.end_frame > speed.start_frame
                    for c in self.cuts
                ):
                    raise ValueError("Accepted cut and speed overlap")
        if any(r.end_ms > self.source_duration_ms for r in self.protected_speech):
            raise ValueError("Protected speech exceeds source duration")
        return self

    def overlaps_protected(self, cut: Cut | Speed) -> bool:
        edit = next(
            (
                e
                for e in self.speech_edits
                if isinstance(cut, Cut)
                and e.cut_id == cut.id
                and cut.reason == "semantic_candidate"
            ),
            None,
        )
        for region in self.protected_speech:
            if edit and region in edit.protected_ranges and self.contains(cut, region):
                continue
            if self.overlaps(cut, region):
                return True
        for cue in self.cues:
            if (
                edit
                and set(cue.source_segment_ids) <= set(edit.segment_ids)
                and self.contains(cut, cue)
            ):
                continue
            if self.overlaps(cut, cue):
                return True
        return False

    def contains(self, op, region) -> bool:
        return (
            op.start_frame * 1000 <= region.start_ms * self.fps
            and op.end_frame * 1000 >= region.end_ms * self.fps
        )

    def overlaps(self, op, region) -> bool:
        return (
            op.start_frame * 1000 < region.end_ms * self.fps
            and op.end_frame * 1000 > region.start_ms * self.fps
        )


def silence_candidates(plan: EditPlan, regions: list[dict]) -> list[Cut]:
    """Subtract protected intervals instead of discarding an entire boundary-overlapping wait."""
    cuts = []
    protected = sorted([*plan.protected_speech, *plan.cues], key=lambda r: r.start_ms)
    for region in regions:
        start = max(0, math.ceil((region["start"] + 0.2) * plan.fps))
        end = min(plan.total_frames, math.floor((region["end"] - 0.2) * plan.fps))
        spans = [(start, end)]
        for guard in protected:
            left = math.floor(guard.start_ms * plan.fps / 1000)
            right = math.ceil(guard.end_ms * plan.fps / 1000)
            spans = [
                (a, b)
                for lo, hi in spans
                for a, b in [(lo, min(hi, left)), (max(lo, right), hi)]
                if b > a
            ]
        for lo, hi in spans:
            if hi - lo >= math.ceil(0.7 * plan.fps):
                cuts.append(Cut(id=1, start_frame=lo, end_frame=hi))
    cuts.sort(key=lambda c: c.start_frame)
    # Analysis normally supplies disjoint intervals; merge overlaps defensively.
    merged = []
    for cut in cuts:
        if merged and cut.start_frame <= merged[-1].end_frame:
            merged[-1].end_frame = max(merged[-1].end_frame, cut.end_frame)
        else:
            cut.id = len(merged) + 1
            merged.append(cut)
    return merged


def fingerprint(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def load_plan(path: Path) -> EditPlan:
    return EditPlan.model_validate(read_json(path))


def create_plan(run_dir: Path, subtitles: Path | None, output: Path, fps: int = 30) -> None:
    manifest = read_json(run_dir / "manifest.json")
    for name in ("metadata", "silence", "transcript"):
        if digest(run_dir / f"{name}.json") != manifest["artifacts"].get(name):
            raise UserError(f"Analysis artifact changed: {name}. Use a verified analysis run.")
    metadata = read_json(run_dir / "metadata.json")
    transcript = Transcript.model_validate(read_json(run_dir / "transcript.json"))
    duration = round(metadata["duration"] * 1000)
    source = Path(manifest["identity"]["source"])
    if digest(source) != manifest["identity"]["sha256"]:
        raise UserError("Source media changed after analysis")
    cues = []
    evidence = {name: manifest["artifacts"][name] for name in ("metadata", "silence", "transcript")}
    if subtitles:
        document = SubtitleDocument.model_validate(read_json(subtitles))
        if document.source_sha256 != evidence["transcript"]:
            raise UserError("Subtitles belong to a different transcript")
        known_ids = {s.id for s in transcript.segments}
        if any(not set(c.source_segment_ids) <= known_ids for c in document.cues):
            raise UserError("Subtitle source segment IDs do not match the transcript")
        cues = document.cues
        evidence["subtitles"] = digest(subtitles)
    speech = [
        ProtectedRange(
            start_ms=max(0, math.floor(s.start * 1000) - 150),
            end_ms=min(duration, math.ceil(s.end * 1000) + 150),
        )
        for s in transcript.segments
    ]
    plan = EditPlan(
        source=str(source),
        source_sha256=manifest["identity"]["sha256"],
        source_duration_ms=duration,
        fps=fps,
        evidence=evidence,
        speech_protection_available=transcript.status == "completed",
        protected_speech=speech,
        cues=cues,
        cuts=[],
    )
    plan.cuts = silence_candidates(plan, read_json(run_dir / "silence.json")["regions"])
    save_new(output, EditPlan.model_validate(plan.model_dump()).model_dump())


def plan_data(plan: EditPlan) -> dict:
    # Preserve the exact v1.0 payload shape for existing approval files.
    excluded = {"speech_edits", "agent_notes"} if plan.schema_version != "1.2" else set()
    if plan.schema_version == "1.0":
        excluded.add("speeds")
    return plan.model_dump(exclude=excluded)


def add_speed(path: Path, start: float, end: float, rate: float, audio: str, output: Path) -> None:
    plan = load_plan(path)
    if (
        not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end * 1000 > plan.source_duration_ms
    ):
        raise UserError("Speed range must be finite and within source duration")
    speed = Speed(
        id=max((s.id for s in plan.speeds), default=0) + 1,
        start_frame=math.ceil(start * plan.fps),
        end_frame=math.floor(end * plan.fps),
        rate=rate,
        audio=audio,
    )
    if plan.overlaps_protected(speed):
        raise UserError("Speed interval overlaps speech or a caption")
    if (
        max(1, math.floor((speed.end_frame - speed.start_frame) / speed.rate + 0.5))
        >= speed.end_frame - speed.start_frame
    ):
        raise UserError("Speed interval is too short to save a frame")
    data = plan.model_dump()
    data["schema_version"] = "1.2" if plan.schema_version == "1.2" else "1.1"
    data["speeds"].append(speed.model_dump())
    data["speeds"].sort(key=lambda entry: entry["start_frame"])
    save_new(output, EditPlan.model_validate(data).model_dump())


def select_cuts(plan: EditPlan, selected: list[int], speeds: list[int] | None = None) -> EditPlan:
    if len(selected) != len(set(selected)) or not set(selected) <= {c.id for c in plan.cuts}:
        raise UserError("Selected cut IDs must be unique and present in the plan")
    speeds = speeds or []
    if len(speeds) != len(set(speeds)) or not set(speeds) <= {s.id for s in plan.speeds}:
        raise UserError("Selected speed IDs must be unique and present in the plan")
    data = plan.model_dump()
    for speed in data["speeds"]:
        speed["decision"] = "accepted" if speed["id"] in speeds else "rejected"
    for cut in data["cuts"]:
        cut["decision"] = "accepted" if cut["id"] in selected else "rejected"
    return EditPlan.model_validate(data)


def compile_plan(plan: EditPlan) -> dict:
    if any(c.decision == "pending" for c in [*plan.cuts, *plan.speeds]):
        raise UserError("Resolve all cut/speed decisions before compiling")
    accepted = [c for c in plan.cuts if c.decision == "accepted"]
    speed_ops = [s for s in plan.speeds if s.decision == "accepted"]
    operations = sorted([*accepted, *speed_ops], key=lambda op: op.start_frame)
    kept, cursor, output_frame = [], 0, 0

    def retain(start: int, end: int, speed: Speed | None = None) -> None:
        nonlocal output_frame
        if end <= start:
            return
        count = end - start
        output_count = max(1, math.floor(count / speed.rate + 0.5)) if speed else count
        if speed and output_count >= count:
            raise UserError("Speed interval is too short to save a frame")
        span = {
            "source_start_frame": start,
            "source_end_frame": end,
            "output_start_frame": output_frame,
            "output_end_frame": output_frame + output_count,
        }
        if plan.schema_version != "1.0":
            span.update(
                requested_rate=speed.rate if speed else 1.0,
                effective_rate=count / output_count,
                audio=speed.audio if speed else "original",
            )
        kept.append(span)
        output_frame += output_count

    for operation in operations:
        retain(cursor, operation.start_frame)
        if isinstance(operation, Speed):
            retain(operation.start_frame, operation.end_frame, operation)
        cursor = operation.end_frame
    retain(cursor, plan.total_frames)
    if not kept:
        raise UserError("Plan removes the entire video")
    cues = []
    for cue in plan.cues:
        for span in kept:
            start = max(cue.start_ms, round(span["source_start_frame"] * 1000 / plan.fps))
            end = min(cue.end_ms, round(span["source_end_frame"] * 1000 / plan.fps))
            if start < end:
                offset = round(
                    (span["source_start_frame"] - span["output_start_frame"]) * 1000 / plan.fps
                )
                data = cue.model_dump()
                data.update(id=len(cues) + 1, start_ms=start - offset, end_ms=end - offset)
                cues.append(data)
    return {
        "schema_version": plan.schema_version,
        "fps": plan.fps,
        "kept": kept,
        "output_frames": output_frame,
        "output_duration_seconds": output_frame / plan.fps,
        "cues": cues,
    }


def summary(plan: EditPlan, selected: list[int], speeds: list[int] | None = None) -> dict:
    execution = compile_plan(select_cuts(plan, selected, speeds))
    return {
        "source": plan.source,
        "source_seconds": plan.source_duration_ms / 1000,
        "output_seconds": execution["output_duration_seconds"],
        "subtitle_cues": len(execution["cues"]),
        "cues": [c.model_dump() for c in plan.cues],
        "caption_warnings": [
            {"cue_id": c.id, "message": "Check duration, line length and reading speed"}
            for c in plan.cues
            if not 1000 <= c.end_ms - c.start_ms <= 7000
            or any(len(line) > 42 for line in c.text.splitlines())
            or len(c.text.replace("\n", " ")) * 1000 / (c.end_ms - c.start_ms) > 20
        ],
        "speech_protection_available": plan.speech_protection_available,
        "cuts": [
            {
                **c.model_dump(),
                "start_seconds": c.start_frame / plan.fps,
                "end_seconds": c.end_frame / plan.fps,
                "selected": c.id in selected,
            }
            for c in plan.cuts
        ],
        "speeds": [
            {
                **s.model_dump(),
                "start_seconds": s.start_frame / plan.fps,
                "end_seconds": s.end_frame / plan.fps,
                "selected": s.id in (speeds or []),
            }
            for s in plan.speeds
        ],
        "execution_spans": execution["kept"],
        "warnings": [
            "Speed ranges are user-selected; loading detection is not automatic. Check audio policy.",
            "Silence may contain important UI actions. Review every selected cut.",
            "Readability, translation and subtitle placement still require review.",
        ],
    }


def approve(
    plan_path: Path, selected: list[int], output: Path, speeds: list[int] | None = None
) -> None:
    plan = select_cuts(load_plan(plan_path), selected, speeds)
    payload = {"plan": plan_data(plan), "execution": compile_plan(plan)}
    save_new(
        output,
        {
            **payload,
            "approval_sha256": fingerprint(payload),
            "approved_at": datetime.now(UTC).isoformat(),
        },
    )


class ApprovedPlan(StrictModel):
    plan: EditPlan
    execution: dict
    approval_sha256: str
    approved_at: str


def load_approved(path: Path) -> tuple[EditPlan, dict]:
    data = read_json(path)
    ApprovedPlan.model_validate(data)
    payload = {"plan": data["plan"], "execution": data["execution"]}
    if fingerprint(payload) != data.get("approval_sha256"):
        raise UserError("Approved content changed. Review and approve again.")
    plan = EditPlan.model_validate(data["plan"])
    if data["execution"] != compile_plan(plan):
        raise UserError("Compiled timeline does not match the approved plan")
    return plan, data["execution"]
