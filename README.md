<div align="center">

<img src="assets/teaser.png" width="800" alt="MemBrain teaser" />

# MemBrain: Agent-Native Memory — by Agents, for Agents

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Pydantic AI](https://img.shields.io/badge/Pydantic%20AI-powered-E92063.svg)](https://ai.pydantic.dev/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

[📖 Technical Blog](docs/tech_blog.md) • [🎬 Demo](demo/README.md) • [🤗 HuggingFace](#)

<div align="center"><img src="assets/tech_blog/paradigm.png" width="800" alt="Memory paradigms" /></div>

</div>

---

## News

> We are still actively polishing the repository — stay tuned!

- **[Coming Soon]** Feature roadmap coming soon.
- **[2026-04-08]** MemBrain 1.5 is now open source!

---

## Table of Contents

- [Quick Start](#quick-start)
- [Basic Usage](#basic-usage)
- [Demo](#demo)
- [Viewer](#viewer)
- [Evaluation](#evaluation)
- [Citation](#citation)

---

## Quick Start

### Prerequisites

Python 3.11+ · Docker 20.10+ · [uv](https://docs.astral.sh/uv/getting-started/installation/)

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/FeelingAI/MemBrain.git
cd MemBrain
```

**2. Configure environment variables**

```bash
cp .env.example.demo .env
```

Edit `.env` with your settings:

```dotenv
# LLM service (for memory extraction and reasoning)
LLM_API_URL=http://localhost:4000/v1
LLM_API_KEY=sk-1234

# PostgreSQL database
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWD=MemBrain
DB_NAME=MemBrain-Demo

# Backend server
BACKEND_PORT=9574
BACKEND_MODE=demo

# Embedding service (local vLLM or online alternatives like OpenAI)
EMBED_SERVICE_URL=http://localhost:9113/v1/embeddings
EMBED_MODEL=qwen3-embed
EMBED_DIM=2560

# Rerank service (local vLLM or online alternatives like Cohere)
RERANK_SERVICE_URL=http://localhost:9114/v1/rerank
RERANK_MODEL=qwen3-rerank
```

**3. (Optional) Start local embedding and rerank services**

> Skip this step if you are using online services (e.g. OpenAI embeddings, Cohere rerank). Update the corresponding URLs in `.env` accordingly.

Edit the model paths in `vllm/compose.yml` and `vllm/serve.py` to point to your local model weights, then:

```bash
# Embedding service (port 9113)
docker compose -f vllm/compose.yml up -d

# Rerank service (port 9114)
uv run python vllm/serve.py
```

**4. Start the database**

```bash
docker compose up -d
```

**5. Install dependencies and start the server**

```bash
uv sync
uv run backend
```

**6. Verify**

```bash
curl http://localhost:9574/health
# {"status": "healthy"}
```

---

## Basic Usage

Store and retrieve memories via the HTTP API:

```bash
# 1. Store a conversation memory
curl -X POST "http://localhost:9574/api/memory" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "messages": [
      {
        "speaker": "Okabe",
        "content": "I ended up buying Dr. Pepper again today. Even though it tastes like carbonated cough syrup, I cannot seem to stop drinking it while debugging.",
        "message_time": "2025-12-05T14:00:00+00:00"
      },
      {
        "speaker": "Amadeus",
        "content": "Well, obviously. It is the intellectual drink for the chosen ones. Not that I would expect someone with your lacking taste buds to truly appreciate its complex flavor profile. Hmph. But at least you are staying hydrated... vaguely.",
        "message_time": "2025-12-05T14:02:00+00:00"
      }
    ],
    "store": true,
    "digest": true
  }'

# 2. Search for relevant memories
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "What is Okabe'\''s preferred drink while debugging, and what does he think it tastes like?"
  }'
```

---

## Demo

A streaming roleplay chat demo built on MemBrain — Vue 3 frontend + FastAPI backend, with long-term memory via the MemBrain API.

![Demo](assets/demo.png)

See [demo/README.md](demo/README.md) for full setup instructions.

---

## Viewer

A web-based tool for inspecting datasets and conversations alongside the memories MemBrain builds from them — covering evaluation experiments and demo sessions.

![Viewer 1](assets/viewer/viewer_1.gif)

![Viewer 2](assets/viewer/viewer_2.gif)

```bash
cd viewer && npm install && npm run dev
```

---

## Evaluation

### Evaluation Results

🏆 **Achieved SOTA performance on multiple benchmarks**

![LoCoMo](assets/experiment/locomo.png)

![LongMemEval](assets/experiment/longmemeval.png)

![PersonaMem](assets/experiment/personamem.png)

![KnowMe-Bench](assets/experiment/knowme-bench.png)

### Supported Benchmarks

- **[LoCoMo](https://github.com/snap-research/locomo)** - Long-context memory benchmark with single/multi-hop reasoning
- **[LongMemEval](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)** - Multi-session conversation evaluation
- **[PersonaMem](https://huggingface.co/datasets/bowen-upenn/PersonaMem)** - Persona-based memory evaluation
- **[KnowMe-Bench](https://github.com/QuantaAlpha/KnowMeBench)** - Benchmarking Person Understanding for Lifelong Digital Companions

### Quick Start

```bash
# 1. Import a dataset
uv run dataset add locomo

# 2. Run memory ingestion
uv run exp run locomo --run-tag myrun

# 3. Run QA evaluation
uv run exp evaluate --run-tag myrun

# 4. View results
cat evaluation/exps/myrun/qa_logs/eval_<timestamp>.json
```

📊 [Full Evaluation Guide](evaluation/README.md)

---

## Acknowledgments

We would like to thank the following projects and contributors:

- **[EverMemOS](https://github.com/EverMind-AI/EverOS)** - We referenced their open-source codebase during implementation, particularly their evaluation pipeline and benchmark integration
- **[Graphiti](https://github.com/getzep/graphiti/tree/main)** - We referenced their open-source codebase, particularly their entity resolution algorithms
- **[Nieta Art](https://app.nieta.art/character/discover)** - A creator community that provided character assets for our interactive demos

Special thanks to the open-source memory framework community for their continuous innovation and collaboration.

---

## Citation

If you use MemBrain in your research, please cite:

```bibtex
@software{membrain2026,
  title = {MemBrain: Agent-Native Memory for AI Agents},
  author = {FeelingAI Team},
  year = {2026},
  url = {https://github.com/FeelingAI/MemBrain}
}
```

---

## License

This project is licensed under the [Apache 2.0](LICENSE) license.