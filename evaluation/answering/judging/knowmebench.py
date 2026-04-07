"""KnowMeBench LLM-as-a-Judge — score-based evaluation (0–5 scale).

Category routing:
  Information Extraction, Adversarial Abstention  →  factual_extraction (0-5)
  Temporal Reasoning, Mnestic Trigger Analysis     →  temporal_numerical_analysis (0/3/5)
  Expert-Annotated Psychoanalysis, Mind-Body       →  psychoanalysis (0-5)

Prompts live in evaluation/answering/judging/prompts/knowmebench/<prompt_key>.md.
Response schemas are defined inline as constants below.
"""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

from membrain.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output"
_PROMPTS_DIR = Path(__file__).parent / "prompts" / "knowmebench"

_print_lock = threading.Lock()

# ── Category → prompt key routing ────────────────────────────────────────────

CATEGORY_TO_PROMPT_KEY: dict[str, str] = {
    "Information Extraction": "factual_extraction",
    "Adversarial Abstention": "factual_extraction",
    "Temporal Reasoning": "temporal_numerical_analysis",
    "Mnestic Trigger Analysis": "temporal_numerical_analysis",
    "Expert-Annotated Psychoanalysis": "psychoanalysis",
    "Mind-Body Interaction": "psychoanalysis",
}

# ── Inline JSON schemas for response_format ───────────────────────────────────
# These are passed verbatim to the LLM API's response_format.json_schema.schema.

_RESPONSE_SCHEMAS: dict[str, dict] = {
    "factual_extraction": {
        "type": "object",
        "required": ["score", "reasoning"],
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 5},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
    "temporal_numerical_analysis": {
        "type": "object",
        "required": ["score", "reasoning"],
        "properties": {
            "score": {"type": "integer", "enum": [0, 3, 5]},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
    "psychoanalysis": {
        "type": "object",
        "required": ["score", "reasoning"],
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 5},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
}

# ── Prompt loading (eager, once at import time) ───────────────────────────────


def _load_prompts() -> dict[str, str]:
    return {
        key: (_PROMPTS_DIR / f"{key}.md").read_text(encoding="utf-8").strip()
        for key in _RESPONSE_SCHEMAS
    }


_PROMPTS: dict[str, str] = _load_prompts()

# ── Pydantic return model ─────────────────────────────────────────────────────


class KnowMeBenchJudgeOutput(BaseModel):
    score: int
    reasoning: str


# ── Core LLM call ─────────────────────────────────────────────────────────────


def _call_once(
    item: dict,
    prompt_key: str,
    model: str,
    http_client: httpx.Client,
) -> KnowMeBenchJudgeOutput:
    user_msg = (
        _PROMPTS[prompt_key]
        .replace("{{question}}", str(item.get("question", "")))
        .replace("{{reference_answer}}", str(item.get("gold_answer", "")))
        .replace("{{model_answer}}", str(item.get("predicted_answer", "")))
    )
    resp = http_client.post(
        f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an impartial judge evaluating AI model outputs based on strict criteria.",
                },
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "evaluation_response",
                    "strict": True,
                    "schema": _RESPONSE_SCHEMAS[prompt_key],
                },
            },
        },
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    if choice.get("finish_reason") == "content_filter":
        from evaluation.answering.judging.simple import ContentFilterError

        raise ContentFilterError("content_filter")
    content = (choice["message"]["content"] or "").strip()
    return KnowMeBenchJudgeOutput.model_validate_json(content)


# ── Per-item judge with retry ─────────────────────────────────────────────────


def _judge_one(
    item: dict,
    model: str,
    retry_interval: float,
    index: int,
    total: int,
    http_client: httpx.Client,
    max_retries: int = 10,
) -> dict:
    category = str(item.get("category") or "unknown")
    prompt_key = CATEGORY_TO_PROMPT_KEY.get(category)
    if prompt_key is None:
        with _print_lock:
            print(
                f"  [{index}/{total}] {item['question_id']}: "
                f"skipped (no prompt for category '{category}')"
            )
        return _make_result(item, score=None, reasoning=None, status="skipped")

    for attempt in range(1, max_retries + 1):
        try:
            output = _call_once(item, prompt_key, model, http_client)
            with _print_lock:
                print(
                    f"  [{index}/{total}] {item['question_id']} "
                    f"(cat={category}): score={output.score}"
                )
            return _make_result(
                item, score=output.score, reasoning=output.reasoning, status="success"
            )
        except Exception as e:
            from evaluation.answering.judging.simple import ContentFilterError

            if isinstance(e, ContentFilterError):
                with _print_lock:
                    print(
                        f"  [{index}/{total}] {item['question_id']}: "
                        f"attempt {attempt}/{max_retries} content_filter, retrying..."
                    )
                continue  # no sleep; retry immediately
            with _print_lock:
                print(
                    f"  [{index}/{total}] {item['question_id']}: "
                    f"attempt {attempt}/{max_retries} failed ({e}), "
                    f"retrying in {retry_interval}s..."
                )
            time.sleep(retry_interval)

    with _print_lock:
        print(
            f"  [{index}/{total}] {item['question_id']}: "
            f"all {max_retries} attempts failed, marking as ERROR"
        )
    return _make_result(item, score=0, reasoning=None, status="error")


def _make_result(
    item: dict,
    score: int | None,
    reasoning: str | None,
    status: str,
) -> dict:
    return {
        "question_id": item["question_id"],
        "question": item["question"],
        "category": item.get("category"),
        "gold_answer": item["gold_answer"],
        "predicted_answer": item["predicted_answer"],
        "score": score,
        "reasoning": reasoning,
        "status": status,
    }


# ── Public entry point ────────────────────────────────────────────────────────


def run_knowmebench_judge(
    *,
    task: str,
    dataset: str,
    input: str | None = None,
    model: str = "gpt-4.1-mini",
    category: str | None = None,
    limit: int | None = None,
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

    results_in: list[dict] = qa_data["results"]
    if category:
        results_in = [r for r in results_in if str(r.get("category")) == str(category)]
    if limit:
        results_in = results_in[:limit]

    if not results_in:
        print("No QA pairs to judge.")
        return 0

    total = len(results_in)
    print(
        f"Judging {total} KnowMeBench pairs for task '{task}' (workers={workers}, model={model})..."
    )

    judged_map: dict[str, dict] = {}
    http_client = httpx.Client(timeout=60.0)
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _judge_one,
                    item,
                    model,
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

    valid = [
        r for r in judged if r.get("status") == "success" and r["score"] is not None
    ]
    avg_score = sum(r["score"] for r in valid) / len(valid) if valid else 0.0

    by_cat: dict[str, dict] = {}
    for r in judged:
        cat = str(r["category"] or "unknown")
        by_cat.setdefault(cat, {"total": 0, "score_sum": 0.0, "evaluated": 0})
        by_cat[cat]["total"] += 1
        if r.get("status") == "success" and r["score"] is not None:
            by_cat[cat]["score_sum"] += r["score"]
            by_cat[cat]["evaluated"] += 1

    print(
        f"\nOverall average score: {avg_score:.3f} / 5.0  ({len(valid)}/{total} evaluated)"
    )
    print("\nPer-category average:")
    for cat in sorted(by_cat):
        ev = by_cat[cat]["evaluated"]
        t = by_cat[cat]["total"]
        avg = by_cat[cat]["score_sum"] / ev if ev else 0.0
        print(f"  {cat}: {avg:.3f}/5.0  ({ev}/{t})")

    output = {
        "task_id": task,
        "dataset": dataset,
        "judge_model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total,
            "evaluated": len(valid),
            "average_score": round(avg_score, 4),
            "by_category": {
                cat: {
                    "total": v["total"],
                    "evaluated": v["evaluated"],
                    "average_score": (
                        round(v["score_sum"] / v["evaluated"], 4)
                        if v["evaluated"]
                        else None
                    ),
                }
                for cat, v in by_cat.items()
            },
        },
        "results": judged,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"knowmebench_judge_{task}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n→ Output written to {out_path}")
    return 0
