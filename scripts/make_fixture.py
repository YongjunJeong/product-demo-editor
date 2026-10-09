"""Generate a known 10s fixture: cut at 5s, silence at 0–2 and 8–10s."""

import argparse
import subprocess
from pathlib import Path


def make_fixture(path: Path, no_audio: bool = False) -> None:
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=320x180:r=25:d=5",
        "-f",
        "lavfi",
        "-i",
        "color=white:s=320x180:r=25:d=5",
    ]
    if not no_audio:
        command += [
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=if(between(t\\,2\\,8)\\,0.2*sin(2*PI*440*t)\\,0):s=16000:d=10",
        ]
    command += ["-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]"]
    if not no_audio:
        command += ["-map", "2:a", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    make_fixture(args.output)
