"""Trailgate command-line interface; only explicit pull uses the network."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .archive import iter_archive_rows
from .audit import export_graph, export_sft, export_steps, public_report
from .bulk_archive import ingest_blob_export
from .bulk_report import public_bulk_summary
from .capture_recovery import recover_capture
from .dataset_assessment import evaluate_dataset
from .dataset_export import export_dataset
from .diagnostics import summarize_unknown_fields
from .pull import PullSpec, preview_pull, pull_snapshot
from .snapshot import SnapshotError, ingest_snapshot, read_snapshot
from .snapshot_input import _read_json


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
    report.add_argument("--max-decisions", type=int, default=100)

    inspect = commands.add_parser("inspect", help="Inspect observation structure for one trace.")
    inspect.add_argument("archive", type=Path)
    inspect.add_argument("--trace", required=True)
    inspect.add_argument("--max-observations", type=int, default=1000)

    evaluate = commands.add_parser(
        "evaluate", help="Preview dataset gates without exporting content."
    )
    evaluate.add_argument("view", choices=["dataset"])
    evaluate.add_argument("archive", type=Path)
    evaluate.add_argument("--rights-file", type=Path, required=True)
    evaluate.add_argument("--policy-file", type=Path, required=True)
    evaluate.add_argument(
        "--use", choices=["internal_training", "external_training"], required=True
    )
    evaluate.add_argument("--max-decisions", type=int, default=100)

    export = commands.add_parser("export", help="Export private diagnostics or gated datasets.")
    export.add_argument("view", choices=["steps", "graph", "sft", "dataset"])
    export.add_argument("archive", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--rights-file", type=Path)
    export.add_argument("--policy-file", type=Path)
    export.add_argument("--use", choices=["internal_training", "external_training"])

    recovery = commands.add_parser(
        "capture-recover", help="Copy complete events from an interrupted private capture."
    )
    recovery.add_argument("input", type=Path)
    recovery.add_argument("--out", type=Path, required=True)

    pull = commands.add_parser("pull", help="Collect bounded Langfuse API pages, read only.")
    pull.add_argument("source", choices=["langfuse"])
    pull.add_argument("--base-url", required=True)
    pull.add_argument("--project-id", required=True)
    pull.add_argument("--from-time", required=True)
    pull.add_argument("--to-time", required=True)
    pull.add_argument("--score-from-time")
    pull.add_argument("--score-to-time")
    pull.add_argument("--trace-id")
    pull.add_argument("--observation-fields", default="core,basic,io,metadata,model")
    pull.add_argument("--score-fields", default="details,subject")
    pull.add_argument("--limit", type=int, default=100)
    pull.add_argument("--max-pages", type=int, default=1000)
    pull.add_argument("--out", type=Path, required=True)
    pull.add_argument("--resume", action="store_true")
    pull.add_argument("--previous-archive", type=Path)
    pull.add_argument("--dry-run", action="store_true")

    blob = commands.add_parser("ingest-blob", help="Import one local enriched blob export run.")
    blob.add_argument("--object-root", type=Path, required=True)
    blob.add_argument("--manifest", type=Path, required=True)
    blob.add_argument("--out", type=Path, required=True)
    blob.add_argument("--resume", action="store_true")
    return parser


def _inspect_trace(archive_path: Path, trace_id: str, max_observations: int) -> dict:
    observations = []
    total = 0
    for item in iter_archive_rows(archive_path, "observations.jsonl"):
        if item["trace_id"] != trace_id:
            continue
        total += 1
        if len(observations) < max_observations:
            observations.append(
                {
                    "id": item["id"],
                    "parent_observation_id": item["parent_observation_id"],
                    "type": item["type"],
                    "start_time": item["start_time"],
                }
            )
    if not total:
        raise SnapshotError(f"trace not found: {trace_id}")
    return {
        "trace_id": trace_id,
        "observations": observations,
        "total_observations": total,
        "omitted_observations": total - len(observations),
    }


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
        if not 0 <= args.max_decisions <= 1000:
            parser.error("--max-decisions must be between 0 and 1000")
        if _read_json(args.archive / "index.json").get("schema_version") == "trailgate.archive.v2":
            return public_bulk_summary(args.archive)
        report = public_report(args.archive)
        report["omitted_decisions"] = max(0, len(report["decisions"]) - args.max_decisions)
        report["decisions"] = report["decisions"][: args.max_decisions]
        return report
    if args.command == "inspect":
        if not 0 <= args.max_observations <= 10_000:
            parser.error("--max-observations must be between 0 and 10000")
        return _inspect_trace(args.archive, args.trace, args.max_observations)
    if args.command == "evaluate":
        return evaluate_dataset(
            args.archive,
            args.rights_file,
            args.policy_file,
            args.use,
            max_decisions=args.max_decisions,
        )
    if args.command == "capture-recover":
        return recover_capture(args.input, args.out)
    if args.command == "pull":
        spec = PullSpec(
            base_url=args.base_url,
            project_id=args.project_id,
            from_time=args.from_time,
            to_time=args.to_time,
            score_from_time=args.score_from_time,
            score_to_time=args.score_to_time,
            observation_fields=tuple(args.observation_fields.split(",")),
            score_fields=tuple(args.score_fields.split(",")),
            trace_id=args.trace_id,
            limit=args.limit,
            max_pages=args.max_pages,
        )
        if args.dry_run:
            return preview_pull(spec)
        credentials = (os.getenv("LANGFUSE_PUBLIC_KEY", ""), os.getenv("LANGFUSE_SECRET_KEY", ""))
        return pull_snapshot(
            spec,
            args.out,
            credentials,
            resume=args.resume,
            previous_archive=args.previous_archive,
        )
    if args.command == "ingest-blob":
        return ingest_blob_export(args.object_root, args.manifest, args.out, resume=args.resume)

    if args.command == "export" and args.view == "steps":
        if args.rights_file or args.use or args.policy_file:
            parser.error("rights, use, and policy options apply only to sft or dataset")
        return {"steps": export_steps(args.archive, args.out), "path": str(args.out)}
    if args.command == "export" and args.view == "graph":
        if args.rights_file or args.use or args.policy_file:
            parser.error("rights, use, and policy options apply only to sft or dataset")
        return {**export_graph(args.archive, args.out), "path": str(args.out)}
    if args.command == "export" and args.view == "sft":
        if not args.rights_file or not args.use or args.policy_file:
            parser.error("candidate sft requires --rights-file and --use only")
        return {
            "candidate_examples": export_sft(args.archive, args.rights_file, args.use, args.out),
            "path": str(args.out),
            "eligibility": "candidate_only",
        }
    if args.command == "export" and args.view == "dataset":
        if not args.rights_file or not args.policy_file or not args.use:
            parser.error("dataset requires --rights-file, --policy-file, and --use")
        return {
            **export_dataset(args.archive, args.rights_file, args.policy_file, args.use, args.out),
            "path": str(args.out),
        }

    parser.error("unknown command")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        result = _run_command(args, parser)
        print(
            json.dumps(
                summarize_unknown_fields(result), ensure_ascii=False, indent=2, sort_keys=True
            )
        )
        return 0
    except (SnapshotError, OSError, ValueError, PermissionError) as exc:
        print(f"trailgate: {exc}", file=sys.stderr)
        return 2
