# MemBrain Evaluation

MemBrain ships with a self-contained evaluation harness for reproducing and extending our benchmark results. It covers dataset management, memory ingestion runs, and QA scoring — all driven by two CLI entry points: `dataset` and `exp`.

We are excited to share this neat evaluation tool with the Agent Memory research community! Building a reliable evaluation benchmark involves rigorous data cleaning and validation, and there are still areas we are actively refining. Our goal is to provide a stable, highly configurable testing template for related research, featuring:

- **Quality Assurance**: Thorough cleaning and quality validation for each dataset.
- **Reproducibility**: Stable, reproducible configurations for all supported datasets.
- **Visualization**: Tools for dataset and experimental result visualization.
- **Resilience**: Checkpoint recovery to seamlessly resume runs (mitigating LLM API instability issues).
- **Robustness**: Strict data validation using Pydantic.

> The entire dataset cleaning and alignment process is quite heavy, and we will continue to optimize it as quickly as possible. Complete intermediate results (ingested memories, QA logs, scores) will similarly be uploaded to Hugging Face soon — stay tuned! We will also provide more detailed analyses of each memory benchmark, including preparation guides, and expand support for additional datasets in the future.

---

## Supported Benchmarks

| Dataset | Source |
|---------|--------|
| LoCoMo | [GitHub](https://github.com/snap-research/locomo) |
| LongMemEval | [HuggingFace](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) |
| PersonaMem | [HuggingFace](https://huggingface.co/datasets/bowen-upenn/PersonaMem) |
| KnowMe Bench | [GitHub](https://github.com/QuantaAlpha/KnowMeBench) |

---

## Prerequisites

Follow the [Quick Start](../README.md#quick-start) in the root README to bring up the backend (PostgreSQL + MemBrain server) before running any evaluation commands.

Set `EXPS_DIR` in `.env` to override the default output directory (default: `evaluation/exps`).

---

## Quick Reference

```bash
uv run dataset --help
uv run exp --help
```

---

## `dataset` — Dataset Management

```bash
# Import a dataset
uv run dataset add <name>

# List imported datasets
uv run dataset ls

# Delete a dataset and all its data
uv run dataset delete <name>
uv run dataset delete --all        # delete everything
uv run dataset delete <name> -y    # skip confirmation
```

Available dataset names: `locomo`, `longmemeval`, `personamemv2`, `knowmebench`

---

## `exp` — Experiment Runs

### Run memory ingestion

```bash
uv run exp run <dataset> [options]
```

| Option | Description |
|--------|-------------|
| `--run-tag TAG` | Custom run identifier (no hyphens). Auto-generated if omitted. |
| `--tasks SPEC` | Task subset, 1-based (e.g. `1-5,8`). Default: all. |
| `--max-workers N` | Parallel ingestion workers (default: 1). |
| `--max-sessions N` | Limit sessions per task. |
| `--start-session N` | Start from session N (default: 1). |
| `--summary-only` | Run Pass 1 (summarization) only. |
| `--regen-summary` | Clear existing summaries and redo Pass 1. |
| `--regen-ingestion` | Redo Pass 2 from scratch, keeping Pass 1 summaries. |
| `-y` | Auto-confirm prompts. |

### List and manage runs

```bash
# List all runs (or filter by dataset)
uv run exp ls
uv run exp ls --dataset locomo

# Copy a run to a new tag
uv run exp cp <run_tag> <new_tag>

# Delete a run by tag
uv run exp delete <run_tag>

# Delete all runs for a dataset (memory only; conversations preserved)
uv run exp delete --dataset <name>
```

### QA evaluation

```bash
uv run exp evaluate --run-tag <tag> [options]
```

| Option | Description |
|--------|-------------|
| `--run-tag TAG` | **(required)** Run tag to evaluate. |
| `--top-k N` | Number of memories retrieved per query. |
| `--ranker` | `rrf` (default) or `rerank`. |
| `--workers N` | Parallel QA workers (default: 5). |
| `--model MODEL` | LLM override for answer generation. |
| `--category CAT` | Filter by QA category. |
| `--judge-model MODEL` | LLM for judging (default: `gpt-4.1-mini`). |
| `--num-judge-runs N` | Judge votes per question (default: 3). |
| `--resume TIMESTAMP` | Resume from an existing JSONL log. |

### Results

Results are written to `$EXPS_DIR/<run_tag>/qa_logs/`:

```
evaluation/exps/<run_tag>/
  qa_logs/
    eval_<timestamp>.jsonl       # raw per-question log
    eval_<timestamp>.json        # summary + detailed results
    eval_<timestamp>_wrong.json  # incorrect answers only
  logs/
    eval.log                     # pipeline log
```
