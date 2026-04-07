"""MemBrain evaluation CLI.

Entry points (configured in pyproject.toml):
  dataset  →  dataset_main()   (add / delete)
  exp      →  exp_main()       (run / resume / ls / delete / evaluate)
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import text  # noqa: E402

from evaluation.ingest.adapters import REGISTRY  # noqa: E402
from evaluation.ingest.importer import import_dataset  # noqa: E402
from membrain.infra.db import SessionLocal, init_db  # noqa: E402
from membrain.infra.models import DatasetModel  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _clear_memory_for_tasks(db, task_ids: list[int], desc: str = "Deleting") -> None:
    if not task_ids:
        return
    from tqdm import tqdm

    patterns = [f"task_{tid}\_\\_%" for tid in task_ids]
    rows = db.execute(
        text(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE "
            + " OR ".join(f"schema_name LIKE :p{i}" for i in range(len(patterns)))
        ),
        {f"p{i}": p for i, p in enumerate(patterns)},
    ).fetchall()
    for (schema,) in tqdm(rows, desc=desc, unit="schema"):
        db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _validate_run_tag(run_tag: str, parser) -> None:
    if "-" in run_tag:
        parser.error(f"Run tag must not contain hyphens: '{run_tag}'")


# ---------------------------------------------------------------------------
# dataset sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_dataset_add(args) -> None:
    adapter = REGISTRY[args.name]()
    init_db()
    db = SessionLocal()
    try:
        ds_id = import_dataset(args.name, adapter, db)
        print(f"Imported dataset '{args.name}' (id={ds_id})")
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        db.close()


def _cmd_dataset_ls() -> None:
    db = SessionLocal()
    try:
        datasets = db.query(DatasetModel).order_by(DatasetModel.name).all()
        if not datasets:
            print("No datasets found.")
            return
        print(f"{'Name':<30} {'ID':>4} {'Tasks':>6}")
        print("-" * 43)
        for ds in datasets:
            print(f"{ds.name:<30} {ds.id:>4} {len(ds.tasks):>6}")
    finally:
        db.close()


def _cmd_dataset_delete(args) -> None:
    db = SessionLocal()
    try:
        if args.delete_all:
            datasets = db.query(DatasetModel).all()
            if not datasets:
                print("No datasets found.")
                return
            print(f"Datasets to delete ({len(datasets)}):")
            for ds in datasets:
                print(f"  - {ds.name} ({len(ds.tasks)} task(s))")
            if not args.yes:
                ans = (
                    input("\nDelete ALL datasets and all data? [y/N] ").strip().lower()
                )
                if ans != "y":
                    print("Aborted.")
                    sys.exit(1)
            from tqdm import tqdm

            for ds in tqdm(datasets, desc="Deleting datasets", unit="dataset"):
                task_ids = [t.id for t in ds.tasks]
                _clear_memory_for_tasks(db, task_ids, desc=f"  {ds.name}")
                db.delete(ds)
            db.commit()
            print("\nAll datasets deleted.")
        else:
            ds = db.query(DatasetModel).filter_by(name=args.dataset).first()
            if ds is None:
                print(f"Dataset '{args.dataset}' not found.")
                sys.exit(1)
            task_ids = [t.id for t in ds.tasks]
            print(f"Dataset: {args.dataset} (id={ds.id}, {len(task_ids)} task(s))")
            if not args.yes:
                ans = (
                    input(f"\nDelete dataset '{args.dataset}' and all its data? [y/N] ")
                    .strip()
                    .lower()
                )
                if ans != "y":
                    print("Aborted.")
                    sys.exit(1)
            _clear_memory_for_tasks(db, task_ids, desc=f"Deleting {args.dataset}")
            db.delete(ds)
            db.commit()
            print(f"Dataset '{args.dataset}' deleted.")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# exp sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_exp_delete_run(run_tag: str) -> None:
    """Delete a specific run by run_tag."""
    from evaluation.memory.pipeline import _delete_run_local

    count = _delete_run_local(run_tag)
    print(f"Deleted {count} task(s) for run '{run_tag}'.")


def _cmd_exp_delete_dataset(dataset_name: str, yes: bool) -> None:
    """Delete all memory runs for a dataset, keeping conversations and QA."""
    db = SessionLocal()
    try:
        ds = db.query(DatasetModel).filter_by(name=dataset_name).first()
        if ds is None:
            print(f"Dataset '{dataset_name}' not found.")
            sys.exit(1)
        task_ids = [t.id for t in ds.tasks]
        print(f"Will delete all memory for '{dataset_name}' ({len(task_ids)} task(s)).")
        if not yes:
            ans = input("\nProceed? [y/N] ").strip().lower()
            if ans != "y":
                print("Aborted.")
                sys.exit(1)
        _clear_memory_for_tasks(db, task_ids)
        db.commit()
        print(
            f"\nAll memory for '{dataset_name}' deleted. Conversations and QA preserved."
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Parser builders
# ---------------------------------------------------------------------------


def _build_dataset_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="dataset", description="MemBrain dataset operations"
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("ls", help="List all datasets")

    add_p = sub.add_parser("add", help="Import a dataset into the database")
    add_p.add_argument("name", choices=list(REGISTRY), help="Dataset name")

    del_p = sub.add_parser("delete", help="Delete a dataset and all its data")
    del_p.add_argument(
        "dataset", nargs="?", default=None, help="Dataset name (omit with --all)"
    )
    del_p.add_argument(
        "--all", action="store_true", dest="delete_all", help="Delete ALL datasets"
    )
    del_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    return parser


def _build_exp_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="exp", description="MemBrain experiment (memory pipeline) operations"
    )
    sub = parser.add_subparsers(dest="action", required=True)

    run_p = sub.add_parser("run", help="Start a new memory ingestion run")
    run_p.add_argument("dataset", help="Dataset name (e.g. locomo)")
    run_p.add_argument(
        "--tasks",
        default=None,
        metavar="SPEC",
        help="Task indexes or ranges, 1-based (e.g. 1-5,8). Default: all tasks.",
    )
    run_p.add_argument("--run-tag", default=None, metavar="TAG")
    run_p.add_argument("--max-sessions", type=int, default=None, metavar="N")
    run_p.add_argument("--start-session", type=int, default=1, metavar="N")
    run_p.add_argument("--max-workers", type=int, default=1, metavar="N")
    run_p.add_argument("--yes", "-y", action="store_true", help="Auto-confirm prompts")
    run_p.add_argument(
        "--summary-only", action="store_true", help="Run only Pass 1 (summarization)"
    )
    run_p.add_argument(
        "--regen-summary",
        action="store_true",
        help="Clear existing summaries before Pass 1",
    )
    run_p.add_argument(
        "--regen-ingestion",
        action="store_true",
        help=(
            "Re-run Pass 2 from scratch, preserving Pass 1 summaries. "
            "If Pass 1 is complete, wipes all ingestion data and restarts Pass 2. "
            "If Pass 1 is still in progress, resumes Pass 1 then starts Pass 2 fresh."
        ),
    )

    ls_p = sub.add_parser("ls", help="List runs")
    ls_p.add_argument("--dataset", default=None)

    del_p = sub.add_parser("delete", help="Delete a run or all runs for a dataset")
    del_p.add_argument("run_tag", nargs="?", default=None, help="Run tag to delete")
    del_p.add_argument(
        "--dataset", default=None, help="Delete all runs for this dataset"
    )
    del_p.add_argument("--yes", "-y", action="store_true")

    evaluate_p = sub.add_parser("evaluate", help="Run QA evaluation pipeline")
    evaluate_p.add_argument(
        "--run-tag", required=True, metavar="TAG", help="Run tag to evaluate"
    )
    evaluate_p.add_argument("--top-k", type=int, default=None, help="Search top K")
    evaluate_p.add_argument("--model", default=None, help="LLM model override")
    evaluate_p.add_argument("--category", default=None, help="Filter QA category")
    evaluate_p.add_argument("--workers", type=int, default=5, help="Parallel workers")
    evaluate_p.add_argument(
        "--ranker",
        choices=["rrf", "rerank"],
        default="rrf",
        help="Ranking strategy (default: rrf)",
    )
    evaluate_p.add_argument(
        "--judge-model",
        default="gpt-4.1-mini",
        help="LLM used for judging (default: gpt-4.1-mini)",
    )
    evaluate_p.add_argument(
        "--num-judge-runs",
        type=int,
        default=3,
        metavar="N",
        help="Number of judge votes per question (default: 3)",
    )
    evaluate_p.add_argument(
        "--resume",
        default=None,
        metavar="TIMESTAMP",
        help="Resume from existing JSONL log",
    )

    cp_p = sub.add_parser("cp", help="Copy a run to a new run tag")
    cp_p.add_argument("run_tag", help="Source run tag")
    cp_p.add_argument("new_run_tag", metavar="NEW_TAG", help="Destination run tag")

    return parser


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def dataset_main() -> None:
    parser = _build_dataset_parser()
    args = parser.parse_args()

    if args.action == "ls":
        _cmd_dataset_ls()
    elif args.action == "add":
        _cmd_dataset_add(args)
    elif args.action == "delete":
        if args.dataset and args.delete_all:
            parser.error("Cannot specify both a dataset name and --all")
        if not args.dataset and not args.delete_all:
            parser.error("Provide a dataset name or --all")
        _cmd_dataset_delete(args)


def exp_main() -> None:
    parser = _build_exp_parser()
    args = parser.parse_args()

    if args.action == "delete":
        if args.run_tag and args.dataset:
            parser.error("Specify either a run_tag or --dataset, not both")
        if not args.run_tag and not args.dataset:
            parser.error("Provide a run_tag or --dataset")
        if args.dataset:
            _cmd_exp_delete_dataset(args.dataset, args.yes)
        else:
            _validate_run_tag(args.run_tag, parser)
            _cmd_exp_delete_run(args.run_tag)
        return

    if args.action == "run":
        from datetime import datetime

        from evaluation.memory.pipeline import (
            _run_tag_exists,
            get_all_task_ids,
            get_task_pk,
            run_pipeline,
        )
        from evaluation.utils.tasks import resolve_task_spec
        from membrain.infra.db import init_memory_db

        dataset = args.dataset
        if args.tasks:
            pairs = resolve_task_spec(args.tasks, dataset)
            task_ids = [p[1] for p in pairs]
            task_pks = {p[1]: p[0] for p in pairs}
            spec_str = args.tasks
        else:
            all_ids = get_all_task_ids(dataset)
            if not all_ids:
                print(f"No tasks found in dataset '{dataset}'")
                sys.exit(1)
            task_ids = all_ids
            task_pks = {tid: get_task_pk(dataset, tid) for tid in all_ids}
            spec_str = "all"

        run_tag = args.run_tag
        if run_tag is not None:
            _validate_run_tag(run_tag, parser)
        if run_tag is None:
            sanitized = spec_str.replace(",", "_").replace("-", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            run_tag = f"{dataset}_{sanitized}_{timestamp}"

        resume = False
        if _run_tag_exists(run_tag):
            if args.yes:
                print(f"Run tag '{run_tag}' already exists. Auto-resuming (-y).")
                resume = True
            else:
                if args.regen_ingestion:
                    prompt = f"\nRun tag '{run_tag}' already exists. Re-run ingestion (Pass 2) from scratch? [y/N] "
                elif args.regen_summary:
                    prompt = f"\nRun tag '{run_tag}' already exists. Regenerate summaries from scratch? [y/N] "
                else:
                    prompt = f"\nRun tag '{run_tag}' already exists. Resume? [y/N] "
                ans = input(prompt).strip().lower()
                if ans != "y":
                    print("Aborted.")
                    sys.exit(1)
                if not (args.regen_ingestion or args.regen_summary):
                    resume = True

        if not resume:
            init_memory_db()

        sys.exit(
            run_pipeline(
                dataset=dataset,
                task_ids=task_ids,
                task_pks=task_pks,
                run_tag=run_tag,
                max_sessions=args.max_sessions,
                start_session=args.start_session,
                max_workers=args.max_workers,
                resume=resume,
                summary_only=args.summary_only,
                regen_summary=args.regen_summary,
                regen_ingestion=args.regen_ingestion,
            )
        )

    elif args.action == "ls":
        from evaluation.memory.pipeline import _list_runs_local

        runs = _list_runs_local()
        if not runs:
            print("No runs found.")
            return
        print(f"{'Run Tag':<30} {'Tasks':>6} {'Done':>6} {'Incomplete':>10}")
        print("-" * 55)
        for tag, counts in runs:
            print(
                f"{tag:<30} {counts['task_count']:>6} {counts['completed']:>6}"
                f" {counts['incomplete']:>10}"
            )

    elif args.action == "evaluate":
        _validate_run_tag(args.run_tag, parser)
        from evaluation.answering.pipeline import run_qa_pipeline

        sys.exit(
            run_qa_pipeline(
                run_tag=args.run_tag,
                top_k=args.top_k,
                model=args.model,
                category=args.category,
                workers=args.workers,
                resume=args.resume,
                judge_model=args.judge_model,
                num_judge_runs=args.num_judge_runs,
                ranker=args.ranker,
            )
        )

    elif args.action == "cp":
        from membrain.infra.checkpoint import copy_run

        _validate_run_tag(args.run_tag, parser)
        _validate_run_tag(args.new_run_tag, parser)
        try:
            successes, errors = copy_run(args.run_tag, args.new_run_tag)
        except ValueError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        for e in errors:
            print(f"  ERROR: {e}")
        print(
            f"\nCopied '{args.run_tag}' → '{args.new_run_tag}' ({successes} task(s))."
        )
        if errors:
            print(f"{len(errors)} task(s) failed.")
            sys.exit(1)
