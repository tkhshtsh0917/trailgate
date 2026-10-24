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

    report = commands.add_parser("report", help="Print a decision-level trainability report.")
    report.add_argument("archive", type=Path)

    inspect = commands.add_parser("inspect", help="Inspect observation structure for one trace.")
    inspect.add_argument("archive", type=Path)
    inspect.add_argument("--trace", required=True)

    export = commands.add_parser("export", help="Export diagnostic steps or strict SFT examples.")
    export.add_argument("view", choices=["steps", "sft"])
    export.add_argument("archive", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--rights-file", type=Path)
    export.add_argument("--use", choices=["internal_training", "external_training"])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            parsed = read_snapshot(args.input)
            result = {
                "valid": True,
                "observations": len(parsed["observations"]),
                "scores": len(parsed["scores"]),
                "declared_complete": True,
                "unknown_observation_fields": parsed["unknown_observation_fields"],
                "unknown_score_fields": parsed["unknown_score_fields"],
            }
        elif args.command == "ingest":
            result = ingest_snapshot(args.input, args.out)
        elif args.command == "report":
            result = public_report(args.archive)
        elif args.command == "inspect":
            archive = read_archive(args.archive)
            observations = [
                {
                    "id": item["id"],
                    "parent_observation_id": item["parent_observation_id"],
                    "type": item["type"],
                    "start_time": item["start_time"],
                }
                for item in archive["observations"]
                if item["trace_id"] == args.trace
            ]
            if not observations:
                raise SnapshotError(f"trace not found: {args.trace}")
            result = {"trace_id": args.trace, "observations": observations}
        elif args.command == "export":
            if args.view == "steps":
                if args.rights_file or args.use:
                    parser.error("--rights-file and --use apply only to sft")
                result = {"steps": export_steps(args.archive, args.out), "path": str(args.out)}
            else:
                if not args.rights_file or not args.use:
                    parser.error("sft requires --rights-file and --use")
                result = {
                    "examples": export_sft(args.archive, args.rights_file, args.use, args.out),
                    "path": str(args.out),
                }
        else:
            parser.error("unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (SnapshotError, OSError, ValueError, PermissionError) as exc:
        print(f"trailgate: {exc}", file=sys.stderr)
        return 2
