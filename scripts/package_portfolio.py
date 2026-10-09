"""Package reviewed public source and showcase images, including outside Git checkouts."""

import argparse
import re
import zipfile
from pathlib import Path

TOP_FILES = {
    "README.md",
    "AGENTS.md",
    ".gitignore",
    "pyproject.toml",
    "LICENSE",
    "NOTICE",
}
DIRECTORIES = {"src", "tests", "scripts", "skills", "config", "docs", "examples", ".github"}
CACHES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git"}
SOURCE_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".html", ".css", ".js"}
SHOWCASE = {"docs/assets/demo-before-after.gif", "docs/assets/review-ui.png"}
SECRET_PATTERN = re.compile(
    r"/Users/[A-Za-z0-9_-]+/|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}|\bAKIA[0-9A-Z]{16}\b"
)


def package(root: Path, output: Path) -> int:
    root = root.resolve()
    paths = [
        root / name for name in TOP_FILES if (root / name).exists() or (root / name).is_symlink()
    ]
    for directory in sorted(DIRECTORIES):
        folder = root / directory
        if folder.is_symlink():
            raise ValueError(f"Unsupported source directory: {directory}")
        if folder.exists():
            paths.extend(folder.rglob("*"))
    public = []
    for path in sorted(set(paths)):
        relative = path.relative_to(root).as_posix()
        parts = path.relative_to(root).parts
        if any(part in CACHES or part.endswith(".egg-info") for part in parts):
            continue
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError(f"Unsupported source entry: {relative}")
        if path.is_dir():
            continue
        if path.name.startswith(".env") or (
            relative not in TOP_FILES | SHOWCASE and path.suffix.lower() not in SOURCE_SUFFIXES
        ):
            raise ValueError(f"Non-public artifact found: {relative}")
        if relative not in SHOWCASE and SECRET_PATTERN.search(path.read_text(encoding="utf-8")):
            raise ValueError(f"Potential secret or personal path found: {relative}")
        public.append(path)
    if not (root / "README.md").is_file() or not (root / "pyproject.toml").is_file():
        raise ValueError("README.md and pyproject.toml are required")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in public:
            archive.write(path, "product-demo-editor/" + path.relative_to(root).as_posix())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed")
    return len(public)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = package(Path(__file__).resolve().parents[1], args.output.resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Verified public archive: {args.output.resolve()} ({count} files)")
    print("No raw media, run outputs, environments or Git history. Review secrets manually too.")


if __name__ == "__main__":
    main()
