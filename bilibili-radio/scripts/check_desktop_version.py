"""Reject releases whose tag, Tauri version, Cargo package and lock disagree."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tomllib


VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.(?:0|[1-9]\d*))?")


def check_version(tauri_dir: Path, tag: str | None = None) -> str:
    config = json.loads((tauri_dir / "tauri.conf.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((tauri_dir / "Cargo.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((tauri_dir / "Cargo.lock").read_text(encoding="utf-8"))
    version = config["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("Desktop version must be X.Y.Z or X.Y.Z-alpha/beta/rc.N")
    locked = [p["version"] for p in lock["package"] if p["name"] == cargo["package"]["name"]]
    if cargo["package"]["version"] != version or locked != [version]:
        raise ValueError("tauri.conf.json, Cargo.toml and Cargo.lock versions must match")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Release tag {tag!r} must equal v{version}")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    version = check_version(Path(__file__).resolve().parents[1] / "bilibili-player/src-tauri", args.tag)
    print(f"Desktop version: {version}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"version={version}\nprerelease={str('-' in version).lower()}\n")


if __name__ == "__main__":
    main()
