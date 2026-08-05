#!/usr/bin/env python3
"""Byte-level ELF evidence for the S1 runtime-relocation replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


REFERENCE_ABACUS_SHA256 = (
    "2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba"
)
RELOCATED_ABACUS_SHA256 = (
    "438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1"
)
EXPECTED_BUILD_ID = "305c76bb7144250c2cd8632e3ffb6ae02e7185ba"
EXPECTED_RELOCATED_RUNPATH = "$ORIGIN/../conda_prefix/lib"
EXPECTED_FILE_SIZE = 10_264_504
EXPECTED_DYNSTR_OFFSET = 0x30F8
EXPECTED_DYNSTR_SIZE = 0x2A21
EXPECTED_DIFFERENCE_COUNT = 60
EXPECTED_FIRST_DIFFERENCE_OFFSET = 23_260
EXPECTED_LAST_DIFFERENCE_OFFSET = 23_319

READELF_COMMANDS = {
    "elf_header": ("-hW",),
    "program_headers": ("-lW",),
    "section_headers": ("-SW",),
    "dynamic_section": ("-dW",),
    "notes": ("-nW",),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path, label: str, *, require_elf: bool = False) -> dict:
    path = path.expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    invocation_path = Path(os.path.abspath(path))
    try:
        realpath = invocation_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"missing {label}: {invocation_path}") from error
    if not realpath.is_file() or not realpath.stat().st_mode & 0o111:
        raise ValueError(f"{label} is not an executable regular file: {realpath}")
    if require_elf:
        with realpath.open("rb") as handle:
            if handle.read(4) != b"\x7fELF":
                raise ValueError(f"{label} realpath is not an ELF file: {realpath}")
    return {
        "path": str(invocation_path),
        "realpath": str(realpath),
        "sha256": sha256(realpath),
    }


def versioned_tool_identity(
    path: Path,
    label: str,
    *,
    version_arguments: tuple[str, ...] = ("--version",),
) -> dict:
    identity = file_identity(path, label, require_elf=True)
    completed = subprocess.run(
        [identity["path"], *version_arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise ValueError(
            f"cannot capture {label} version (exit {completed.returncode})"
        )
    output = completed.stdout.decode("utf-8", errors="replace")
    identity.update(
        {
            "version_arguments": list(version_arguments),
            "version_first_line": output.splitlines()[0],
            "version_output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        }
    )
    return identity


def _run_readelf(readelf: dict, binary: dict, arguments: tuple[str, ...]) -> bytes:
    completed = subprocess.run(
        [readelf["path"], *arguments, binary["realpath"]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"readelf {' '.join(arguments)} failed for {binary['realpath']}: {detail}"
        )
    return completed.stdout


def _dynamic_values(text: str, tag: str) -> list[str]:
    pattern = re.compile(
        rf"\({re.escape(tag)}\).*?(?:Shared library|Library rpath|Library runpath): \[(.*?)\]"
    )
    return pattern.findall(text)


def _load_segments(text: str) -> list[dict[str, str]]:
    rows = []
    pattern = re.compile(
        r"^\s*LOAD\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+"
        r"(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+"
        r"([RWE ]+)\s+(0x[0-9a-fA-F]+)\s*$"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        offset, virtual, physical, file_size, memory_size, flags, alignment = (
            match.groups()
        )
        rows.append(
            {
                "offset": offset.lower(),
                "virtual_address": virtual.lower(),
                "physical_address": physical.lower(),
                "file_size": file_size.lower(),
                "memory_size": memory_size.lower(),
                "flags": "".join(flags.split()),
                "alignment": alignment.lower(),
            }
        )
    if not rows:
        raise ValueError("readelf program headers contain no LOAD segments")
    return rows


def _dynstr(text: str) -> dict[str, int]:
    pattern = re.compile(
        r"^\s*\[\s*\d+\]\s+\.dynstr\s+STRTAB\s+"
        r"[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s",
        re.MULTILINE,
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(f"expected one .dynstr section, found {len(matches)}")
    offset, size = matches[0]
    return {"file_offset": int(offset, 16), "size": int(size, 16)}


def _build_id(text: str) -> str:
    matches = re.findall(r"Build ID:\s*([0-9a-fA-F]+)", text)
    if len(matches) != 1:
        raise ValueError(f"expected one GNU Build ID, found {len(matches)}")
    return matches[0].lower()


def _normalized_dynamic(text: str) -> str:
    return re.sub(
        r"((?:Library rpath|Library runpath): \[).*?(\])",
        r"\1<RUNTIME_PATH>\2",
        text,
    )


def readelf_evidence(binary: dict, readelf: dict) -> tuple[dict, dict[str, bytes]]:
    outputs = {
        name: _run_readelf(readelf, binary, arguments)
        for name, arguments in READELF_COMMANDS.items()
    }
    decoded = {
        name: output.decode("utf-8", errors="replace")
        for name, output in outputs.items()
    }
    dynamic = decoded["dynamic_section"]
    evidence = {
        "file_size": Path(binary["realpath"]).stat().st_size,
        "build_id": _build_id(decoded["notes"]),
        "needed": _dynamic_values(dynamic, "NEEDED"),
        "rpath": _dynamic_values(dynamic, "RPATH"),
        "runpath": _dynamic_values(dynamic, "RUNPATH"),
        "load_segments": _load_segments(decoded["program_headers"]),
        "dynstr": _dynstr(decoded["section_headers"]),
        "readelf_output_sha256": {
            name: hashlib.sha256(output).hexdigest()
            for name, output in outputs.items()
        },
        "normalized_dynamic_sha256": hashlib.sha256(
            _normalized_dynamic(dynamic).encode("utf-8")
        ).hexdigest(),
    }
    return evidence, outputs


def relocation_equivalence_evidence(
    reference_path: Path,
    replay_path: Path,
    old_prefix: Path,
    readelf_path: Path,
    chrpath_path: Path,
) -> dict:
    reference = file_identity(reference_path, "reference ABACUS", require_elf=True)
    replay = file_identity(replay_path, "relocated replay ABACUS", require_elf=True)
    readelf = versioned_tool_identity(readelf_path, "readelf")
    chrpath = versioned_tool_identity(chrpath_path, "chrpath")
    reference_elf, reference_outputs = readelf_evidence(reference, readelf)
    replay_elf, replay_outputs = readelf_evidence(replay, readelf)

    errors: list[str] = []
    if reference["sha256"] != REFERENCE_ABACUS_SHA256:
        errors.append("reference ABACUS SHA-256 differs from registered original")
    if replay["sha256"] != RELOCATED_ABACUS_SHA256:
        errors.append("replay ABACUS SHA-256 differs from registered relocated binary")
    for label, evidence in (("reference", reference_elf), ("replay", replay_elf)):
        if evidence["file_size"] != EXPECTED_FILE_SIZE:
            errors.append(f"{label} ELF file size differs from registered value")
        if evidence["build_id"] != EXPECTED_BUILD_ID:
            errors.append(f"{label} ELF Build ID differs from registered value")
        if evidence["dynstr"] != {
            "file_offset": EXPECTED_DYNSTR_OFFSET,
            "size": EXPECTED_DYNSTR_SIZE,
        }:
            errors.append(f"{label} .dynstr layout differs from registered value")
    for name in ("elf_header", "program_headers", "section_headers", "notes"):
        if reference_outputs[name] != replay_outputs[name]:
            errors.append(f"readelf {name} differs outside the dynamic path")
    if reference_elf["load_segments"] != replay_elf["load_segments"]:
        errors.append("ELF LOAD segment layout/size/flags differ")
    if reference_elf["needed"] != replay_elf["needed"]:
        errors.append("ELF NEEDED list differs")
    if reference_elf["normalized_dynamic_sha256"] != replay_elf[
        "normalized_dynamic_sha256"
    ]:
        errors.append("ELF dynamic section differs beyond RPATH/RUNPATH text")

    expected_reference_runpath = str(old_prefix.resolve(strict=False) / "lib")
    if reference_elf["rpath"] or reference_elf["runpath"] != [
        expected_reference_runpath
    ]:
        errors.append("reference ELF does not have the exact registered old RUNPATH")
    if replay_elf["rpath"] or replay_elf["runpath"] != [EXPECTED_RELOCATED_RUNPATH]:
        errors.append("replay ELF does not have the exact clean relocated RUNPATH")
    replay_dynamic_paths = [*replay_elf["rpath"], *replay_elf["runpath"]]
    if any(str(old_prefix.resolve(strict=False)) in value for value in replay_dynamic_paths):
        errors.append("replay ELF RPATH/RUNPATH contains the old prefix")

    reference_bytes = Path(reference["realpath"]).read_bytes()
    replay_bytes = Path(replay["realpath"]).read_bytes()
    if len(reference_bytes) != len(replay_bytes):
        errors.append("reference and replay ELF file sizes differ")
        differences: list[int] = []
    else:
        differences = [
            index
            for index, (left, right) in enumerate(zip(reference_bytes, replay_bytes))
            if left != right
        ]
    dynstr_start = reference_elf["dynstr"]["file_offset"]
    dynstr_end = dynstr_start + reference_elf["dynstr"]["size"]
    old_value = expected_reference_runpath.encode("utf-8") + b"\0"
    new_value = EXPECTED_RELOCATED_RUNPATH.encode("utf-8") + b"\0"
    old_offsets = []
    cursor = dynstr_start
    while True:
        offset = reference_bytes.find(old_value, cursor, dynstr_end)
        if offset < 0:
            break
        old_offsets.append(offset)
        cursor = offset + 1
    if len(old_offsets) != 1:
        errors.append(f"reference RUNPATH has {len(old_offsets)} .dynstr byte matches")
        path_start = dynstr_end
    else:
        path_start = old_offsets[0]
    if path_start + len(old_value) != dynstr_end:
        errors.append("reference RUNPATH is not the final .dynstr string")
    if replay_bytes[path_start : path_start + len(new_value)] != new_value:
        errors.append("replay RUNPATH bytes are not at the reference DT_RUNPATH slot")
    if any(replay_bytes[path_start + len(new_value) : dynstr_end]):
        errors.append("replay RUNPATH tail padding is not all NUL")
    if any(index < path_start or index >= dynstr_end for index in differences):
        errors.append("ELF bytes differ outside the RUNPATH string/padding slot")
    if reference_bytes[:path_start] != replay_bytes[:path_start] or reference_bytes[
        dynstr_end:
    ] != replay_bytes[dynstr_end:]:
        errors.append("loadable code/data differs outside the registered RUNPATH slot")

    comparison = {
        "same_file_size": len(reference_bytes) == len(replay_bytes),
        "same_build_id": reference_elf["build_id"] == replay_elf["build_id"],
        "same_elf_header": reference_outputs["elf_header"]
        == replay_outputs["elf_header"],
        "same_program_headers": reference_outputs["program_headers"]
        == replay_outputs["program_headers"],
        "same_section_headers": reference_outputs["section_headers"]
        == replay_outputs["section_headers"],
        "same_load_segments": reference_elf["load_segments"]
        == replay_elf["load_segments"],
        "same_needed": reference_elf["needed"] == replay_elf["needed"],
        "dynamic_difference_only_runtime_path": reference_elf[
            "normalized_dynamic_sha256"
        ]
        == replay_elf["normalized_dynamic_sha256"],
        "byte_difference_count": len(differences),
        "first_difference_offset_zero_based": differences[0] if differences else None,
        "last_difference_offset_zero_based": differences[-1] if differences else None,
        "allowed_runpath_slot_start_zero_based": path_start,
        "allowed_runpath_slot_end_exclusive_zero_based": dynstr_end,
        "all_differences_within_runpath_slot": bool(differences)
        and all(path_start <= index < dynstr_end for index in differences),
        "outside_runpath_slot_byte_identical": (
            reference_bytes[:path_start] == replay_bytes[:path_start]
            and reference_bytes[dynstr_end:] == replay_bytes[dynstr_end:]
        ),
        "replay_runpath_tail_nul_padded": not any(
            replay_bytes[path_start + len(new_value) : dynstr_end]
        ),
    }
    expected_comparison = {
        "byte_difference_count": EXPECTED_DIFFERENCE_COUNT,
        "first_difference_offset_zero_based": EXPECTED_FIRST_DIFFERENCE_OFFSET,
        "last_difference_offset_zero_based": EXPECTED_LAST_DIFFERENCE_OFFSET,
        "allowed_runpath_slot_start_zero_based": EXPECTED_FIRST_DIFFERENCE_OFFSET,
        "allowed_runpath_slot_end_exclusive_zero_based": (
            EXPECTED_DYNSTR_OFFSET + EXPECTED_DYNSTR_SIZE
        ),
    }
    for key, expected in expected_comparison.items():
        if comparison[key] != expected:
            errors.append(f"ELF relocation comparison {key} differs from {expected}")
    for key in (
        "same_file_size",
        "same_build_id",
        "same_elf_header",
        "same_program_headers",
        "same_section_headers",
        "same_load_segments",
        "same_needed",
        "dynamic_difference_only_runtime_path",
        "all_differences_within_runpath_slot",
        "outside_runpath_slot_byte_identical",
        "replay_runpath_tail_nul_padded",
    ):
        if comparison[key] is not True:
            errors.append(f"ELF relocation comparison gate failed: {key}")
    if errors:
        raise ValueError("runtime relocation ELF gate failed:\n- " + "\n- ".join(errors))

    return {
        "schema_version": 1,
        "reference_binary": reference,
        "replay_binary": replay,
        "readelf_tool": readelf,
        "chrpath_tool": chrpath,
        "reference_elf": reference_elf,
        "replay_elf": replay_elf,
        "comparison": comparison,
        "relocation_recipe": {
            "claim": "registered_reproduction_recipe_not_execution_log",
            "copy_argv": [
                "/usr/bin/cp",
                "--preserve=mode,timestamps",
                reference["path"],
                replay["path"],
            ],
            "chrpath_argv": [
                chrpath["path"],
                "-r",
                EXPECTED_RELOCATED_RUNPATH,
                replay["path"],
            ],
            "validator_authority": "byte_level_elf_gate",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--old-prefix", type=Path, required=True)
    parser.add_argument("--readelf", type=Path, default=Path("/usr/bin/readelf"))
    parser.add_argument("--chrpath", type=Path, default=Path("/usr/bin/chrpath"))
    args = parser.parse_args()
    payload = relocation_equivalence_evidence(
        args.reference,
        args.replay,
        args.old_prefix,
        args.readelf,
        args.chrpath,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
