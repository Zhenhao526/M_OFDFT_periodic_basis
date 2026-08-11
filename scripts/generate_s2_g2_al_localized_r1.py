#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from s2_g2_al_localized_common_r1 import INPUT_NAMES, canonical_json, load_config, registered_input_payloads, require, sha256_bytes, validate_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    config = load_config(root)
    validate_config(config)
    payloads = registered_input_payloads(root, config)
    require(tuple(payloads) == INPUT_NAMES, "input denominator differs")
    target = root / config["execution"]["input_root"]
    if args.check:
        require(target.is_dir() and not target.is_symlink(), "input root missing")
        require(sorted(path.name for path in target.iterdir()) == sorted(INPUT_NAMES), "input file denominator differs")
        for name, data in payloads.items():
            require((target / name).read_bytes() == data, f"registered input differs: {name}")
        mode = "check"
    else:
        require(not target.exists(), "input root already exists")
        target.mkdir(parents=True)
        for name, data in payloads.items():
            (target / name).write_bytes(data)
        mode = "write"
    print(json.dumps({
        "status": "accepted", "mode": mode, "experiment_id": config["reference"]["experiment_id"],
        "files": {name: sha256_bytes(data) for name, data in payloads.items()},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
