"""CLI for local product-demo analysis and manual subtitle exchange."""

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .agent_exchange import export_agent, import_agent
from .capcut import capcut_info, export_capcut, verify_bundle
from .media import UserError
from .pipeline import analyze, versions
from .planning import (
    add_speed,
    approve,
    compile_plan,
    create_plan,
    load_approved,
    load_plan,
    select_cuts,
    summary,
)
from .rendering import render, renderer_binary
from .schema import Config
from .subtitles import export_corrections, export_translation, import_translation
from .transcription import prepare_model


def main() -> int:
    parser = argparse.ArgumentParser(prog="video-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local tools and installed packages")
    prepare = commands.add_parser(
        "prepare-model", help="Download model weights only; no media sent"
    )
    prepare.add_argument("--model", default="small")
    prepare.add_argument(
        "--model-dir", type=Path, default=Path.home() / ".cache/video-agent/models"
    )
    task = commands.add_parser("analyze", help="Analyze a local video without remote inference")
    task.add_argument("source", type=Path)
    task.add_argument("--preset", choices=["product-demo"], default="product-demo")
    task.add_argument("--config", type=Path)
    task.add_argument("--runs-dir", type=Path, default=Path("runs"))
    task.add_argument("--model-dir", type=Path, default=Path.home() / ".cache/video-agent/models")
    task.add_argument("--model")
    task.add_argument("--skip-transcription", action="store_true")
    task.add_argument("--resume", type=Path, help="Reuse verified artifacts from this run")
    correction = commands.add_parser(
        "transcript-edit", help="Export a text-only correction template"
    )
    correction.add_argument("source", type=Path)
    correction.add_argument("--output", type=Path, required=True)
    export = commands.add_parser(
        "translation-export", help="Prepare local manual translation files"
    )
    export.add_argument("source", type=Path)
    export.add_argument("--corrections", type=Path)
    export.add_argument("--glossary", type=Path)
    export.add_argument("--settings", type=Path)
    export.add_argument("--output", type=Path, required=True)
    imported = commands.add_parser(
        "translation-import", help="Validate translation and create draft SRT"
    )
    imported.add_argument("request", type=Path)
    imported.add_argument("response", type=Path)
    imported.add_argument("--output", type=Path, required=True)
    plan_cmd = commands.add_parser(
        "plan", help="Create a subtitle/cut review plan from an analysis run"
    )
    plan_cmd.add_argument("run", type=Path)
    plan_cmd.add_argument("--subtitles", type=Path)
    plan_cmd.add_argument("--fps", type=int, choices=[25, 30, 60], default=30)
    plan_cmd.add_argument("--output", type=Path, required=True)
    agent_export = commands.add_parser("agent-export", help="Prepare text-only host-agent context")
    agent_export.add_argument("run", type=Path)
    agent_export.add_argument("--goal", required=True)
    agent_export.add_argument("--target-seconds", type=float)
    agent_export.add_argument("--glossary", type=Path)
    agent_export.add_argument("--output", type=Path, required=True)
    agent_import = commands.add_parser(
        "agent-import", help="Validate agent subtitles and edit proposals"
    )
    agent_import.add_argument("request", type=Path)
    agent_import.add_argument("response", type=Path)
    agent_import.add_argument("--output", type=Path, required=True)
    speed_cmd = commands.add_parser(
        "speed-add", help="Add a pending user-selected waiting interval"
    )
    speed_cmd.add_argument("plan", type=Path)
    speed_cmd.add_argument("--start", type=float, required=True, help="Source seconds")
    speed_cmd.add_argument("--end", type=float, required=True, help="Source seconds")
    speed_cmd.add_argument("--rate", type=float, required=True, help="Greater than 1, at most 4")
    speed_cmd.add_argument("--audio", choices=["mute", "tempo"], default="mute")
    speed_cmd.add_argument("--output", type=Path, required=True)
    for name in ("review", "approve", "preview"):
        command = commands.add_parser(name)
        command.add_argument("plan", type=Path)
        command.add_argument(
            "--cuts",
            nargs="*",
            type=int,
            default=[],
            help="IDs explicitly selected for removal; default none",
        )
        command.add_argument(
            "--speeds",
            nargs="*",
            type=int,
            default=[],
            help="Explicitly selected speed IDs; default none",
        )
        if name != "review":
            command.add_argument("--output", type=Path, required=True)
        if name == "preview":
            command.add_argument("--resume", action="store_true")
    render_cmd = commands.add_parser("render", help="Render an approved plan only")
    render_cmd.add_argument("approved_plan", type=Path)
    render_cmd.add_argument("--output", type=Path, required=True)
    render_cmd.add_argument("--resume", action="store_true")
    commands.add_parser("capcut-info", help="Detect installation without touching projects")
    capcut_cmd = commands.add_parser(
        "export-capcut", help="Export verified local media/subtitle bundle"
    )
    capcut_cmd.add_argument("approved_plan", type=Path)
    capcut_cmd.add_argument("rendered", type=Path)
    capcut_cmd.add_argument("--output", type=Path, required=True)
    capcut_cmd.add_argument("--include-source", action="store_true")
    capcut_cmd.add_argument("--include-preview", action="store_true")
    verify_cmd = commands.add_parser("verify-bundle")
    verify_cmd.add_argument("folder", type=Path)
    ui = commands.add_parser("ui", help="Open a loopback-only local review server")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--work-dir", type=Path, default=Path("work/ui"))
    args = parser.parse_args()
    try:
        if args.command == "ui":
            from .web import serve

            serve(args.work_dir, args.port)
        elif args.command == "doctor":
            report = versions()
            try:
                report.update(renderer=renderer_binary(), render_ready=True)
            except UserError as exc:
                report.update(render_ready=False, error=str(exc))
                print(json.dumps(report, indent=2))
                return 2
            print(json.dumps(report, indent=2))
        elif args.command == "prepare-model":
            print(prepare_model(args.model, args.model_dir.expanduser()))
        elif args.command == "transcript-edit":
            export_corrections(args.source, args.output)
            print(args.output.resolve())
        elif args.command == "translation-export":
            export_translation(
                args.source, args.output, args.corrections, args.glossary, args.settings
            )
            print(args.output.resolve())
        elif args.command == "translation-import":
            import_translation(args.request, args.response, args.output)
            print(f"Draft subtitles (review required): {args.output.resolve()}")
        elif args.command == "plan":
            create_plan(args.run, args.subtitles, args.output, args.fps)
            print(args.output.resolve())
        elif args.command == "agent-export":
            export_agent(args.run, args.output, args.goal, args.target_seconds, args.glossary)
            print(args.output.resolve())
        elif args.command == "agent-import":
            print(
                json.dumps(
                    import_agent(args.request, args.response, args.output),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "speed-add":
            add_speed(args.plan, args.start, args.end, args.rate, args.audio, args.output)
            print(args.output.resolve())
        elif args.command == "review":
            print(
                json.dumps(
                    summary(load_plan(args.plan), args.cuts, args.speeds),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "approve":
            approve(args.plan, args.cuts, args.output, args.speeds)
            print(f"Approved exact plan: {args.output.resolve()}")
        elif args.command == "preview":
            plan = select_cuts(load_plan(args.plan), args.cuts, args.speeds)
            print(
                json.dumps(
                    render(plan, compile_plan(plan), args.output, False, args.resume), indent=2
                )
            )
        elif args.command == "render":
            plan, execution = load_approved(args.approved_plan)
            print(json.dumps(render(plan, execution, args.output, True, args.resume), indent=2))
        elif args.command == "capcut-info":
            print(json.dumps(capcut_info(), ensure_ascii=False, indent=2))
        elif args.command == "export-capcut":
            result = export_capcut(
                args.approved_plan,
                args.rendered,
                args.output,
                args.include_source,
                args.include_preview,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "verify-bundle":
            print(json.dumps(verify_bundle(args.folder), indent=2))
        else:
            raw = yaml.safe_load(args.config.read_text()) if args.config else {}
            if not isinstance(raw, dict):
                raise UserError("Config must be a YAML mapping.")
            raw["preset"] = args.preset
            if args.model:
                raw["model"] = args.model
            if args.skip_transcription:
                raw["transcribe"] = False
            directory = analyze(
                args.source,
                args.runs_dir,
                Config.model_validate(raw),
                args.model_dir.expanduser(),
                args.resume,
            )
            print(directory)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Completed artifacts can be resumed.", file=sys.stderr)
        return 130
    except (UserError, OSError, ValueError, KeyError, ValidationError, yaml.YAMLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
