"""Reproducible synthetic exchange/render demo. No ASR, agent inference or network calls."""

import argparse
import shutil
from pathlib import Path

from make_fixture import make_fixture

from video_agent.agent_exchange import export_agent, import_agent
from video_agent.pipeline import analyze, digest
from video_agent.planning import compile_plan, load_plan, select_cuts
from video_agent.rendering import render
from video_agent.schema import Config
from video_agent.subtitles import read_json, save_new


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source.mp4"
    make_fixture(source)
    run = analyze(source, output / "runs", Config(transcribe=False), output / "unused-models")
    # Preserve the original analysis; new run explicitly declares human-authored annotations.
    annotated = output / "annotated-run"
    annotated.mkdir()
    manifest = read_json(run / "manifest.json")
    for name in ["metadata", "silence", "scenes"]:
        shutil.copyfile(run / f"{name}.json", annotated / f"{name}.json")
    shutil.copyfile(root / "examples/demo-transcript.json", annotated / "transcript.json")
    manifest["identity"]["annotation_origin"] = "authored synthetic transcript, not ASR"
    manifest["artifacts"]["transcript"] = digest(annotated / "transcript.json")
    save_new(annotated / "manifest.json", manifest)
    exchange = output / "exchange"
    export_agent(annotated, exchange, "Remove the abandoned take", target_seconds=9)
    response = read_json(exchange / "response.template.json")
    response.update(read_json(root / "examples/demo-response-content.json"))
    save_new(exchange / "response.json", response)
    report = import_agent(exchange / "request.json", exchange / "response.json", output / "draft")
    plan = select_cuts(
        load_plan(output / "draft/plan.json"), report["preview_cuts"], report["preview_speeds"]
    )
    render(plan, compile_plan(plan), output / "preview", approved=False)
    print(f"Unapproved synthetic preview: {output / 'preview/preview.mp4'}")
    print(f"{report['source_seconds']:.2f}s -> {report['proposed_seconds']:.2f}s")


if __name__ == "__main__":
    main()
