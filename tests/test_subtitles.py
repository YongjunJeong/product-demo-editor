import json
import subprocess
import sys
from itertools import pairwise

import pytest

from video_agent.media import UserError
from video_agent.subtitles import (
    export_corrections,
    export_translation,
    hash_file,
    import_translation,
    srt_time,
)


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read(path):
    return json.loads(path.read_text())


@pytest.fixture
def exchange(tmp_path):
    source = tmp_path / "한국어 전사.json"
    write(
        source,
        {
            "status": "completed",
            "language": "ko",
            "segments": [
                {
                    "id": 1,
                    "start": 0.2,
                    "end": 4.8,
                    "text": "새 고객 세그먼트를 만듭니다.",
                    "words": [{"start": 0.2, "end": 1.0, "text": "새", "probability": 0.9}],
                },
                {"id": 2, "start": 5.0, "end": 9.0, "text": "저장 버튼을 누릅니다."},
            ],
        },
    )
    bundle = tmp_path / "translation"
    export_translation(source, bundle)
    response = read(bundle / "response.template.json")
    response["segments"] = [
        {"id": 1, "text": "Create a new customer segment."},
        {"id": 2, "text": "Click Save."},
    ]
    reply = tmp_path / "response.json"
    write(reply, response)
    return source, bundle / "request.json", reply


def test_full_cli_flow_and_nonoverwrite(exchange, tmp_path):
    source, request, reply = exchange
    source_hash = hash_file(source)
    output = tmp_path / "자막 출력"
    args = [
        sys.executable,
        "-m",
        "video_agent.cli",
        "translation-import",
        str(request),
        str(reply),
        "--output",
        str(output),
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    srt = (output / "subtitles.en.srt").read_text()
    assert "00:00:00,200 --> 00:00:04,800" in srt
    assert "Create a new customer segment." in srt
    cues = read(output / "subtitles.en.json")["cues"]
    assert cues[1]["source_segment_ids"] == [2]
    assert read(output / "review_report.json")["status"] == "needs_review"
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert hash_file(source) == source_hash
    assert (output / "subtitles.en.srt").read_text() == srt


def test_corrections_preserve_time_clear_stale_alignment(exchange, tmp_path):
    source, _, _ = exchange
    correction = tmp_path / "corrections.json"
    export_corrections(source, correction)
    data = read(correction)
    data["segments"][0]["text"] = "새로운 고객 세그먼트를 생성합니다."
    write(correction, data)
    output = tmp_path / "corrected"
    export_translation(source, output, corrections=correction)
    corrected = read(output / "corrected-transcript.json")
    assert corrected["segments"][0]["words"] == []
    assert corrected["segments"][0]["start"] == 0.2
    assert read(source)["segments"][0]["words"]
    with pytest.raises(FileExistsError):
        export_corrections(source, correction)
    data["source_sha256"] = "wrong"
    write(correction, data)
    with pytest.raises(UserError, match="different"):
        export_translation(source, tmp_path / "bad", corrections=correction)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize(
    "kind", ["missing", "duplicate", "extra", "empty", "wrong_request", "changed_request"]
)
def test_invalid_exchange_rejected_without_output(exchange, tmp_path, kind):
    _, request, reply = exchange
    response = read(reply)
    if kind == "missing":
        response["segments"].pop()
    elif kind == "duplicate":
        response["segments"][1]["id"] = 1
    elif kind == "extra":
        response["segments"].append({"id": 3, "text": "Extra"})
    elif kind == "empty":
        response["segments"][0]["text"] = "  "
    elif kind == "wrong_request":
        response["request_id"] = "other"
    else:
        request.write_text(request.read_text() + "\n")
    write(reply, response)
    with pytest.raises(UserError):
        import_translation(request, reply, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_split_timing_and_review_warnings(exchange, tmp_path):
    source, _, _ = exchange
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("terms:\n  - source: 세그먼트\n    target: segment\n")
    bundle = tmp_path / "long"
    export_translation(source, bundle, glossary_path=glossary)
    response = read(bundle / "response.template.json")
    response["segments"][0]["text"] = (
        "Create a new audience by selecting customers and adding the conditions "
        "you need for this demonstration before saving your changes. " * 2
    )
    response["segments"][1]["text"] = "Click Save."
    reply = tmp_path / "long-response.json"
    write(reply, response)
    output = tmp_path / "long-output"
    import_translation(bundle / "request.json", reply, output)
    cues = read(output / "subtitles.en.json")["cues"]
    first = [cue for cue in cues if cue["source_segment_ids"] == [1]]
    assert len(first) > 1
    assert first[0]["start_ms"] == 200
    assert first[-1]["end_ms"] == 4800
    for a, b in pairwise(first):
        assert a["end_ms"] == b["start_ms"]
    for cue in cues:
        assert cue["start_ms"] < cue["end_ms"]
        assert len(cue["text"].splitlines()) <= 2
        assert all(len(line) <= 42 for line in cue["text"].splitlines())
    codes = {w["code"] for w in read(output / "review_report.json")["warnings"]}
    assert "reading_speed" in codes
    assert "glossary_missing" in codes


def test_reordered_reply_uses_source_order(exchange, tmp_path):
    _, request, reply = exchange
    response = read(reply)
    response["segments"].reverse()
    write(reply, response)
    output = tmp_path / "reordered"
    import_translation(request, reply, output)
    assert read(output / "subtitles.en.json")["cues"][0]["source_segment_ids"] == [1]


def test_single_long_identifier_does_not_get_broken(exchange, tmp_path):
    _, request, reply = exchange
    response = read(reply)
    response["segments"][0]["text"] = "x" * 43
    write(reply, response)
    with pytest.raises(UserError, match="longer than"):
        import_translation(request, reply, tmp_path / "long-word")


def test_no_speech_and_skipped_transcript(tmp_path):
    source = tmp_path / "empty.json"
    for status in ("completed", "skipped_no_audio"):
        write(source, {"status": status, "language": "ko", "segments": []})
        with pytest.raises(UserError, match="non-empty"):
            export_translation(source, tmp_path / status)


def test_timestamp_hour_and_rounding():
    assert srt_time(3_600_001) == "01:00:00,001"


def test_saved_provenance_retains_exact_input_bytes(exchange, tmp_path):
    _, request, reply = exchange
    output = tmp_path / "provenance"
    import_translation(request, reply, output)
    assert hash_file(output / "request.json") == hash_file(request)
    assert hash_file(output / "response.json") == hash_file(reply)
