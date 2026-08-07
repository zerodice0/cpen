#!/usr/bin/env python3
"""Install cpen's Python entrypoints without a packaging framework."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def main() -> int:
    parser = argparse.ArgumentParser(description="Install cpen command line entrypoints")
    parser.add_argument(
        "--prefix",
        type=Path,
        default=Path.home() / ".local",
        help="installation prefix (default: ~/.local)",
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / "bin"
    target = args.prefix.expanduser().resolve() / "bin"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("cpen_cli.py", "cpen", "cpen-focus", "cpen-save", "cpen-guard"):
        destination = target / name
        shutil.copy2(source / name, destination)
        destination.chmod(0o644 if name.endswith(".py") else 0o755)
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
