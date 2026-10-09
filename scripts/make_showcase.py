"""Make a labeled before/after GIF from the authored synthetic demo; never use private media."""

import argparse
import subprocess
from pathlib import Path

from video_agent.rendering import renderer_binary
from video_agent.subtitles import read_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    demo = args.demo.resolve()
    response = read_json(demo / "draft/agent-response.json")
    if response["engine"] != "authored-synthetic-example (no LLM call)":
        parser.error("Only the repository's authored synthetic demo can be showcased")
    report = read_json(demo / "draft/agent-report.json")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        "[0:v]scale=480:270,subtitles=draft/subtitles/subtitles.en.srt:"
        "force_style='FontName=Arial,FontSize=24,Outline=1,Shadow=0',"
        "pad=480:330:0:60:color=0x19201b,"
        "drawtext=text='SOURCE / 10.00s':fontcolor=0xe7ebe8:fontsize=22:x=20:y=20[left];"
        "[1:v]scale=480:270,subtitles=preview/subtitles.en.srt:"
        "force_style='FontName=Arial,FontSize=24,Outline=1,Shadow=0',"
        "pad=480:330:0:60:color=0x19201b,"
        f"drawtext=text='DRAFT / {report['proposed_seconds']:.2f}s':"
        "fontcolor=0xb8ed83:fontsize=22:x=20:y=20,tpad=stop_mode=clone:stop_duration=2[right];"
        "[left][right]hstack=inputs=2,pad=960:370:0:0:color=0x101512,"
        "drawtext=text='Synthetic fixture / Authored response / No ASR or LLM call':"
        "fontcolor=0xa1afa5:fontsize=16:x=20:y=343,"
        "fps=8,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer[out]"
    )
    subprocess.run(
        [
            renderer_binary(),
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            "source.mp4",
            "-i",
            "preview/edited.mp4",
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-an",
            "-t",
            "10",
            "-loop",
            "0",
            str(output),
        ],
        cwd=demo,
        check=True,
    )
    print(output)


if __name__ == "__main__":
    main()
