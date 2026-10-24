"""Trailgate command-line interface. No network or model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import export_sft, export_steps, public_report
from .snapshot import SnapshotError, ingest_snapshot, read_archive, read_snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trailgate", description="Audit agent traces for training use."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Validate a saved Langfuse API snapshot.")
    doctor.add_argument("--source", choices=["langfuse"], required=True)
    doctor.add_argument("--input", type=Path, required=True)

    ingest = commands.add_parser(
        "ingest", help="Import a saved API snapshot into a private archive."
    )
    ingest.add_argument("format", choices=["langfuse-api-snapshot"])
    ingest.add_argument("input", type=Path)
    ingest.add_argument("--out", type=Path, required=True)

    report = commands.add_parser("report", help="Print a decision-level capture audit.")
    report.add_argument("archive", type=Path)

    inspect = commands.add_parser("inspect", help="Inspect observation structure for one trace.")
    inspect.add_argument("archive", type=Path)
    inspect.add_argument("--trace", required=True)

    export = commands.add_parser("export", help="Export diagnostic steps or Chat SFT candidates.")
    export.add_argument("view", choices=["steps", "sft"])
    export.add_argument("archive", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--rights-file", type=Path)
    export.add_argument("--use", choices=["internal_training", "external_training"])
    return parser


def _inspect_trace(archive_path: Path, trace_id: str) -> dict:
    archive = read_archive(archive_path)
    observations = [
        {
            "id": item["id"],
            "parent_observation_id": item["parent_observation_id"],
            "type": item["type"],
            "start_time": item["start_time"],
        }
        for item in archive["observations"]
        if item["trace_id"] == trace_id
    ]
    if not observations:
        raise SnapshotError(f"trace not found: {trace_id}")
    return {"trace_id": trace_id, "observations": observations}


def _run_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    if args.command == "doctor":
        parsed = read_snapshot(args.input)
        return {
            "valid": True,
            "observations": len(parsed["observations"]),
            "scores": len(parsed["scores"]),
            "declared_complete": True,
            "unknown_observation_fields": parsed["unknown_observation_fields"],
            "unknown_score_fields": parsed["unknown_score_fields"],
        }
    if args.command == "ingest":
        return ingest_snapshot(args.input, args.out)
    if args.command == "report":
        return public_report(args.archive)
    if args.command == "inspect":
        return _inspect_trace(args.archive, args.trace)

    if args.command == "export" and args.view == "steps":
        if args.rights_file or args.use:
            parser.error("--rights-file and --use apply only to sft")
        return {"steps": export_steps(args.archive, args.out), "path": str(args.out)}
    if args.command == "export" and args.view == "sft":
        if not args.rights_file or not args.use:
            parser.error("sft requires --rights-file and --use")
        return {
            "examples": export_sft(args.archive, args.rights_file, args.use, args.out),
            "path": str(args.out),
        }

    parser.error("unknown command")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        result = _run_command(args, parser)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (SnapshotError, OSError, ValueError, PermissionError) as exc:
        print(f"trailgate: {exc}", file=sys.stderr)
        return 2
