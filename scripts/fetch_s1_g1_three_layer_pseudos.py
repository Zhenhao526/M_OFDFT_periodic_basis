#!/usr/bin/env python3
"""Fetch exact upstream PseudoDojo bytes into the registered external cache."""

from __future__ import annotations

import argparse
import os
import tempfile
import urllib.request
from pathlib import Path

from s1_g1_three_layer_common import (
    find_project_root,
    load_config,
    require,
    sha256_file,
    validate_pseudo,
)


def fetch_one(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        require(destination.is_file() and not destination.is_symlink(), f"unsafe cache path: {destination}")
        require(sha256_file(destination) == expected_sha256, f"existing cache SHA differs: {destination}")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as handle:
            require(response.status == 200, f"upstream HTTP status {response.status}")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        require(sha256_file(temporary) == expected_sha256, f"download SHA differs: {url}")
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    cache = Path(config["external_pseudo_cache"])
    for material, pseudo in config["pseudodojo"]["materials"].items():
        destination = cache / pseudo["basename"]
        fetch_one(pseudo["url"], destination, pseudo["sha256"])
        identity = validate_pseudo(destination, material, config)
        print(f"{material} {identity['basename']} sha256={identity['sha256']} nproj={identity['number_of_proj']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
