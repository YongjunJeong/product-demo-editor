"""Validated source-relative analysis contracts (seconds, half-open intervals)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Config(StrictModel):
    preset: Literal["product-demo"] = "product-demo"
    silence_threshold_db: float = Field(default=-35, ge=-100, le=0)
    silence_min_duration: float = Field(default=0.7, gt=0)
    scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    scene_width: int = Field(default=640, ge=64, le=1920)
    transcribe: bool = True
    language: str = Field(default="ko", min_length=2, max_length=8)
    model: str = "small"
    cpu_threads: int = Field(default=4, ge=1, le=32)


class Interval(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class Word(Interval):
    text: str
    probability: float = Field(ge=0, le=1)


class Segment(Interval):
    id: int = Field(ge=1)
    text: str
    words: list[Word] = Field(default_factory=list)


class Transcript(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    time_base: Literal["source_seconds"] = "source_seconds"
    status: Literal["completed", "skipped_no_audio", "skipped_by_user"]
    language: str
    model_snapshot: str | None = None
    segments: list[Segment] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered(self):
        previous = 0.0
        ids = set()
        for segment in self.segments:
            if segment.start < previous or segment.id in ids:
                raise ValueError("segments must be ordered and IDs unique")
            if any(w.start < segment.start or w.end > segment.end for w in segment.words):
                raise ValueError("word timestamps must remain within the segment")
            previous = segment.end
            ids.add(segment.id)
        if self.status != "completed" and self.segments:
            raise ValueError("skipped transcripts must be empty")
        return self
