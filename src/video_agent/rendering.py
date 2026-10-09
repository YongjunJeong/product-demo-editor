"""Deterministic rendering of compiled timelines; no editing decisions here."""

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .media import UserError, executable, probe, run
from .pipeline import digest, versions, write_json
from .planning import EditPlan, fingerprint, plan_data
from .subtitles import srt_time


def renderer_binary() -> str:
    choices = [
        executable("ffmpeg"),
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
    ]
    for path in choices:
        if Path(path).is_file() and " subtitles " in run([path, "-hide_banner", "-filters"]):
            return path
    raise UserError(
        "FFmpeg subtitles filter missing. Install FFmpeg with libass (macOS: brew install ffmpeg-full)."
    )


def filter_graph(plan: EditPlan, execution: dict, metadata: dict) -> str:
    spans = execution["kept"]
    n = len(spans)
    fps = execution["fps"]
    graph = [
        f"[0:{metadata['video_stream_index']}]setpts=PTS-STARTPTS,fps={fps},"
        f"tpad=stop_mode=clone:stop_duration=1,split={n}" + "".join(f"[v{i}]" for i in range(n))
    ]
    if metadata["audio_present"]:
        offset = metadata["audio_start_time"] - metadata["video_start_time"]
        normal = "asetpts=PTS-STARTPTS"
        if offset > 0:
            normal += f",adelay={round(offset * 1000)}:all=1"
        elif offset < 0:
            normal += f",atrim=start={-offset},asetpts=PTS-STARTPTS"
        graph.append(
            f"[0:{metadata['audio_stream_index']}]{normal},aresample=48000,apad,asplit={n}"
            + "".join(f"[a{i}]" for i in range(n))
        )
    for i, span in enumerate(spans):
        start, end = span["source_start_frame"], span["source_end_frame"]
        count = span["output_end_frame"] - span["output_start_frame"]
        rate = span.get("effective_rate", 1.0)
        vf = f"trim=start_frame={start}:end_frame={end},setpts=N/({fps}*TB)"
        if rate != 1:
            vf += f",setpts=PTS/{rate:.12g},fps={fps},tpad=stop_mode=clone:stop_duration=1,trim=end_frame={count},setpts=N/({fps}*TB)"
        graph.append(f"[v{i}]{vf}[vt{i}]")
        if metadata["audio_present"]:
            af = (
                f"atrim=start_sample={round(start * 48000 / fps)}:"
                f"end_sample={round(end * 48000 / fps)},asetpts=PTS-STARTPTS"
            )
            if rate != 1:
                tempo = rate
                while tempo > 2:
                    af += ",atempo=2"
                    tempo /= 2
                af += f",atempo={tempo:.12g}"
                if span["audio"] == "mute":
                    af += ",volume=0"
                af += f",apad,atrim=end_sample={round(count * 48000 / fps)},asetpts=PTS-STARTPTS"
            graph.append(f"[a{i}]{af}[at{i}]")
    inputs = "".join(
        f"[vt{i}]" + (f"[at{i}]" if metadata["audio_present"] else "") for i in range(n)
    )
    graph.append(
        inputs
        + f"concat=n={n}:v=1:a={int(metadata['audio_present'])}[vc]"
        + ("[aout]" if metadata["audio_present"] else "")
    )
    graph.append("[vc]pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1[vout]")
    return ";\n".join(graph)


def invoke(binary: str, args: list[str], output: Path, log: Path) -> None:
    result = subprocess.run(
        [binary, "-nostdin", "-hide_banner", "-v", "error", *args],
        cwd=output,
        capture_output=True,
        text=True,
        check=False,
    )
    log.write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise UserError(f"Render failed; see {log}: {result.stderr[-1000:]}")


def render(
    plan: EditPlan,
    execution: dict,
    output: Path,
    approved: bool,
    resume: bool = False,
    progress=None,
) -> dict:
    started = time.perf_counter()
    source = Path(plan.source)
    if digest(source) != plan.source_sha256:
        raise UserError("Source media changed. Create a new analysis/plan.")
    metadata = probe(source)
    if abs(metadata["duration"] * 1000 - plan.source_duration_ms) > 2:
        raise UserError("Plan duration does not match source media")
    binary = renderer_binary()
    tool_version = run([binary, "-version"]).splitlines()[0]
    identity = fingerprint(
        {
            "plan": plan_data(plan),
            "execution": execution,
            "approved": approved,
            "versions": versions(),
            "renderer": tool_version,
        }
    )
    output = output.resolve()
    if resume:
        manifest = json.loads((output / "render_manifest.json").read_text())
        if manifest["identity"] != identity:
            raise UserError("Render resume requires identical plan, approval mode and tools")
    else:
        output.mkdir(parents=True, exist_ok=False)
        manifest = {"identity": identity, "completed": {}}
        write_json(output / "render_manifest.json", manifest)
    metrics = {
        "status": "running",
        "approved": approved,
        "stages": {},
        "external_requests": 0,
        "source_seconds": metadata["duration"],
        "output_seconds": execution["output_duration_seconds"],
        "removed_frames": sum(
            c.end_frame - c.start_frame for c in plan.cuts if c.decision == "accepted"
        ),
        "speed_saved_frames": sum(
            (s["source_end_frame"] - s["source_start_frame"])
            - (s["output_end_frame"] - s["output_start_frame"])
            for s in execution["kept"]
        ),
        "discarded_tail_ms": plan.source_duration_ms - plan.total_frames * 1000 / plan.fps,
        "renderer": tool_version,
        "source_sha256": plan.source_sha256,
        "approval_sha256": fingerprint({"plan": plan_data(plan), "execution": execution})
        if approved
        else None,
    }
    try:
        write_json(output / "execution.json", execution)
        write_json(output / "plan.json", plan_data(plan))
        text = "\n\n".join(
            f"{c['id']}\n{srt_time(c['start_ms'])} --> {srt_time(c['end_ms'])}\n{c['text']}"
            for c in execution["cues"]
        )
        (output / "subtitles.en.srt").write_text(text + ("\n" if text else ""), encoding="utf-8")
        graph = filter_graph(plan, execution, metadata)
        (output / "render.ffgraph").write_text(graph, encoding="utf-8")

        def stage(name: str, args: list[str]) -> None:
            if progress:
                progress(name)
            then = time.perf_counter()
            destination = output / f"{name}.mp4"
            cached = destination.exists() and manifest["completed"].get(name) == digest(destination)
            print(
                json.dumps({"stage": name, "status": "cached" if cached else "rendering"}),
                file=sys.stderr,
                flush=True,
            )
            if not cached:
                partial = output / f"{name}.partial.mp4"
                partial.unlink(missing_ok=True)
                invoke(binary, ["-n", *args, partial.name], output, output / f"{name}.log")
                actual = probe(partial)
                if (
                    abs(actual["duration"] - execution["output_duration_seconds"])
                    > 1 / plan.fps + 0.005
                ):
                    raise UserError(f"{name} output duration differs from compiled timeline")
                if metadata["audio_present"]:
                    audio = next((s for s in actual["streams"] if s["codec_type"] == "audio"), None)
                    if (
                        audio is None
                        or abs(
                            float(audio.get("duration", 0)) - execution["output_duration_seconds"]
                        )
                        > 0.05
                    ):
                        raise UserError(f"{name} audio duration differs from compiled timeline")
                partial.replace(destination)
                manifest["completed"][name] = digest(destination)
                write_json(output / "render_manifest.json", manifest)
            metrics["stages"][name] = {"cached": cached, "seconds": time.perf_counter() - then}

        args = ["-i", str(source), "-filter_complex", graph, "-map", "[vout]"]
        if metadata["audio_present"]:
            args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "cfr",
            "-r",
            str(plan.fps),
            "-movflags",
            "+faststart",
        ]
        stage("edited", args)
        vf = "scale=w='min(1280,iw)':h=-2"
        if execution["cues"]:
            vf += ",subtitles=subtitles.en.srt:force_style='FontName=Arial,FontSize=14,Outline=1,Shadow=0,MarginV=20'"
        stage(
            "preview",
            [
                "-i",
                "edited.mp4",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
            ],
        )
        metrics["status"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        metrics["status"] = "failed"
        metrics["error"] = str(exc)
        raise
    finally:
        metrics["processing_seconds"] = time.perf_counter() - started
        metrics_path = output / "render_metrics.json"
        if metrics_path.exists():
            write_json(
                output / f"metrics_previous_{uuid.uuid4().hex[:8]}.json",
                json.loads(metrics_path.read_text()),
            )
        write_json(metrics_path, metrics)
    return metrics
