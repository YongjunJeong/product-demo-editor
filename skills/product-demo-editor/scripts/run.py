"""Run the repository CLI from an installed skill link; no nested agent or model call."""

import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[3]
    python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        print(
            f"Prepare the repository virtual environment first: {root / 'README.md'}",
            file=sys.stderr,
        )
        return 2
    return subprocess.run(
        [str(python), "-m", "video_agent.cli", *sys.argv[1:]], cwd=root, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
