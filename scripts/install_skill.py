"""Link the same maintained skill into Codex or Claude Code without overwriting files."""

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=["codex", "claude", "both"], default="codex")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "skills/product-demo-editor"
    codex = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
    targets = [codex] if args.host == "codex" else [Path.home() / ".claude/skills"]
    if args.host == "both":
        targets = [codex, Path.home() / ".claude/skills"]
    # Preflight every target; never replace an existing skill or broken unrelated symlink.
    for parent in targets:
        dest = parent / source.name
        if (dest.exists() or dest.is_symlink()) and dest.resolve() != source:
            parser.error(f"Existing skill preserved; choose another location manually: {dest}")
    for parent in targets:
        parent.mkdir(parents=True, exist_ok=True)
        dest = parent / source.name
        if not dest.is_symlink():
            dest.symlink_to(source, target_is_directory=True)
        print(dest)


if __name__ == "__main__":
    main()
