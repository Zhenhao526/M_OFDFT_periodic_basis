#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from s2_g2_architecture_common_r1 import load_config, registered_artifacts, require


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    config = load_config(root)
    artifacts = registered_artifacts(config)
    for relative, data in artifacts.items():
        path = root / relative
        if args.check:
            require(path.is_file() and path.read_bytes() == data, f"artifact differs: {relative}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print({"status": "accepted_check" if args.check else "generated", "registered_cases": 12, "artifacts": len(artifacts)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
