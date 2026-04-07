"""Run simple LLM judge (inline HTTP, no manifest agent) to score QA results.

Usage:
    uv run python -m evaluation.answering.judging.simple --task <task_id> [--dataset NAME] \
        [--model gpt-4.1-mini] [--category CAT] [--limit N] \
        [--workers 5] [--retry-interval 3]
"""

import argparse
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from membrain.config import settings  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output"

_print_lock = threading.Lock()

SYSTEM_PROMPT = (
    "You are an expert grader that determines if answers to questions "
    "match a gold standard answer"
)

USER_PROMPT_TEMPLATE = """\
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {golden_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".\
"""


def _extract_json(content: str) -> str | None:
    """Extract JSON from LLM response that may contain surrounding text or markdown."""
    # Try 1: markdown code block
    m = re.search(r"```(?:json)?\s*(\{[^`]*\})\s*```", content, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try 2: JSON object containing "label"
    m = re.search(r'\{[^{}]*"label"\s*:\s*"[^"]*"[^{}]*\}', content)
    if m:
        return m.group(0)
    # Try 3: raw content
    return content.strip()


def _parse_label(content: str) -> str:
    """Return 'CORRECT' or 'WRONG', raise ValueError if unparseable."""
    json_str = _extract_json(content)
    result = json.loads(json_str)
    label = result.get("label", "").strip().upper()
    if label not in ("CORRECT", "WRONG"):
        raise ValueError(f"Unexpected label: {label!r}")
    return label


class ContentFilterError(Exception):
    """Raised when the model refuses due to content policy (no point retrying)."""


def _call_once(user_msg: str, model: str, http_client: httpx.Client) -> str:
    """Single LLM call, returns parsed label. Raises on any failure."""
    resp = http_client.post(
        f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.0,
            "max_tokens": 128,
        },
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    if choice.get("finish_reason") == "content_filter":
        raise ContentFilterError("content_filter")
    content = (choice["message"]["content"] or "").strip()
    return _parse_label(content), content


def judge_pair_with_retry(
    item: dict,
    model: str,
    num_runs: int,
    retry_interval: float,
    index: int,
    total: int,
    http_client: httpx.Client,
    max_retries: int = 10,
    error_log_fn: Callable[[str], None] | None = None,
) -> dict:
    user_msg = USER_PROMPT_TEMPLATE.format(
        question=item["question"],
        golden_answer=item["gold_answer"],
        generated_answer=item["predicted_answer"],
    )

    def _log_error(msg: str) -> None:
        if error_log_fn is not None:
            error_log_fn(msg)
        else:
            with _print_lock:
                print(msg)

    judgments: list[str] = []
    for run_idx in range(num_runs):
        for attempt in range(1, max_retries + 1):
            try:
                label, _raw = _call_once(user_msg, model, http_client)
                judgments.append(label)
                break
            except ContentFilterError:
                _log_error(
                    f"  [{index}/{total}] {item['question_id']} run {run_idx + 1}: "
                    f"attempt {attempt}/{max_retries} content_filter, retrying..."
                )
                # No sleep: content filter is non-deterministic; retrying immediately
                # has a chance of succeeding without any wait.
            except Exception as e:
                _log_error(
                    f"  [{index}/{total}] {item['question_id']} run {run_idx + 1}: "
                    f"attempt {attempt}/{max_retries} failed ({e}), retrying in {retry_interval}s..."
                )
                time.sleep(retry_interval)
        else:
            _log_error(
                f"  [{index}/{total}] {item['question_id']} run {run_idx + 1}: "
                f"all {max_retries} attempts failed, marking as ERROR"
            )
            judgments.append("ERROR")

    error_votes = judgments.count("ERROR")
    if error_votes == num_runs:
        final_label = "ERROR"
        correct_votes = 0
    else:
        correct_votes = judgments.count("CORRECT")
        final_label = "CORRECT" if correct_votes > num_runs / 2 else "WRONG"

    # Only print final result in standalone mode (no UI)
    if error_log_fn is None:
        with _print_lock:
            print(
                f"  [{index}/{total}] {item['question_id']}: {final_label} ({correct_votes}/{num_runs})"
            )

    return {
        "question_id": item["question_id"],
        "question": item["question"],
        "category": item.get("category"),
        "gold_answer": item["gold_answer"],
        "predicted_answer": item["predicted_answer"],
        "label": final_label,
        "judgments": {f"judgment_{i + 1}": j for i, j in enumerate(judgments)},
        "correct_votes": correct_votes,
        "num_runs": num_runs,
    }


def run_judge(
    *,
    task: str,
    dataset: str,
    input: str | None = None,
    model: str = "gpt-4.1-mini",
    category: str | None = None,
    limit: int | None = None,
    num_runs: int = 3,
    workers: int = 5,
    retry_interval: float = 3.0,
    max_retries: int = 10,
) -> int:
    input_path = Path(input) if input else OUTPUT_DIR / f"qa_{task}.json"
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    with open(input_path, encoding="utf-8") as f:
        qa_data = json.load(f)

    results_in = qa_data["results"]

    if category:
        results_in = [r for r in results_in if str(r.get("category")) == str(category)]
    if limit:
        results_in = results_in[:limit]

    if not results_in:
        print("No QA pairs to judge.")
        return 0

    total = len(results_in)
    print(
        f"Judging {total} pairs for task '{task}' (workers={workers}, runs={num_runs}, model={model})..."
    )

    judged_map: dict[str, dict] = {}
    http_client = httpx.Client(timeout=60.0)
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    judge_pair_with_retry,
                    item,
                    model,
                    num_runs,
                    retry_interval,
                    i + 1,
                    total,
                    http_client,
                    max_retries,
                ): item["question_id"]
                for i, item in enumerate(results_in)
            }
            for future in as_completed(futures):
                result = future.result()
                judged_map[result["question_id"]] = result
    finally:
        http_client.close()

    judged = [judged_map[item["question_id"]] for item in results_in]

    correct = sum(1 for r in judged if r["label"] == "CORRECT")
    accuracy = correct / total if total else 0.0

    by_cat: dict[str, dict[str, int]] = {}
    for r in judged:
        cat = str(r["category"] or "unknown")
        by_cat.setdefault(cat, {"correct": 0, "total": 0})
        by_cat[cat]["total"] += 1
        if r["label"] == "CORRECT":
            by_cat[cat]["correct"] += 1

    print(f"\nOverall accuracy: {correct}/{total} = {accuracy:.1%}")
    print("\nPer-category accuracy:")
    for cat in sorted(by_cat):
        c = by_cat[cat]["correct"]
        t = by_cat[cat]["total"]
        print(f"  Category {cat}: {c}/{t} = {c / t:.1%}")

    output = {
        "task_id": task,
        "dataset": dataset,
        "judge_model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "correct": correct,
            "total": total,
            "accuracy": round(accuracy, 4),
            "by_category": {
                cat: {
                    "correct": v["correct"],
                    "total": v["total"],
                    "accuracy": round(v["correct"] / v["total"], 4),
                }
                for cat, v in by_cat.items()
            },
        },
        "results": judged,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"simple_judge_{task}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n→ Output written to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simple inline LLM judge for QA evaluation"
    )
    parser.add_argument("--task", required=True, help="Task ID")
    parser.add_argument("--dataset", default="locomo", help="Dataset name")
    parser.add_argument("--input", default=None, help="Override input file path")
    parser.add_argument("--model", default="gpt-4.1-mini", help="Judge model")
    parser.add_argument("--category", default=None, help="Filter by category")
    parser.add_argument("--limit", type=int, default=None, help="Max pairs to judge")
    parser.add_argument(
        "--num-runs", type=int, default=3, help="Judge runs per pair (majority vote)"
    )
    parser.add_argument("--workers", type=int, default=5, help="Thread pool size")
    parser.add_argument(
        "--retry-interval", type=float, default=3.0, help="Seconds between retries"
    )
    args = parser.parse_args()
    return run_judge(**vars(args))


if __name__ == "__main__":
    sys.exit(main())
