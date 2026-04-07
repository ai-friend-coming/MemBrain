"""
Qwen3-Reranker service using generation-based scoring (same as example.py).
Usage: CUDA_VISIBLE_DEVICES=0 uv run python serve.py
"""

import os

os.environ["CUDA_VISIBLE_DEVICES"] = "1"

import math
from contextlib import asynccontextmanager
from typing import List, Optional

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from transformers import AutoTokenizer
from vllm.distributed.parallel_state import destroy_model_parallel

from vllm import LLM, SamplingParams, TokensPrompt

MODEL_PATH = "/path/to/models/Qwen3-Reranker-4B"
PORT = 9114

# ── globals filled in lifespan ──────────────────────────────────────────────
tokenizer = None
model = None
sampling_params = None
true_token = None
false_token = None
suffix_tokens = None
max_length = 8192


def format_instruction(instruction, query, doc):
    return [
        {
            "role": "system",
            "content": 'Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".',
        },
        {
            "role": "user",
            "content": f"<Instruct>: {instruction}\n\n<Query>: {query}\n\n<Document>: {doc}",
        },
    ]


def process_inputs(pairs, instruction, max_len, sfx_tokens):
    messages = [format_instruction(instruction, q, d) for q, d in pairs]
    token_ids = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=False, enable_thinking=False
    )["input_ids"]
    token_ids = [ids[:max_len] + sfx_tokens for ids in token_ids]
    return [TokensPrompt(prompt_token_ids=ids) for ids in token_ids]


def compute_scores(inputs):
    outputs = model.generate(inputs, sampling_params, use_tqdm=False)
    scores = []
    for out in outputs:
        logprobs = out.outputs[0].logprobs[-1]
        true_logit = logprobs[true_token].logprob if true_token in logprobs else -10
        false_logit = logprobs[false_token].logprob if false_token in logprobs else -10
        tp, fp = math.exp(true_logit), math.exp(false_logit)
        scores.append(tp / (tp + fp))
    return scores


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tokenizer, model, sampling_params, true_token, false_token, suffix_tokens
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    tokenizer.padding_side = "left"
    tokenizer.pad_token = tokenizer.eos_token
    model = LLM(
        model=MODEL_PATH,
        tensor_parallel_size=1,
        max_model_len=10000,
        enable_prefix_caching=True,
        gpu_memory_utilization=0.8,
    )
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    suffix_tokens = tokenizer.encode(suffix, add_special_tokens=False)
    true_token = tokenizer("yes", add_special_tokens=False).input_ids[0]
    false_token = tokenizer("no", add_special_tokens=False).input_ids[0]
    sampling_params = SamplingParams(
        temperature=0,
        max_tokens=1,
        logprobs=20,
        allowed_token_ids=[true_token, false_token],
    )
    yield
    destroy_model_parallel()


app = FastAPI(lifespan=lifespan)


# ── request / response schemas ───────────────────────────────────────────────
class RerankRequest(BaseModel):
    model: str
    query: str
    documents: List[str]
    instruction: Optional[str] = (
        "Given a web search query, retrieve relevant passages that answer the query"
    )
    top_n: Optional[int] = None


class RerankResult(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    model: str
    results: List[RerankResult]


# ── endpoint ─────────────────────────────────────────────────────────────────
@app.post("/v1/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest):
    pairs = [(req.query, doc) for doc in req.documents]
    inputs = process_inputs(
        pairs, req.instruction, max_length - len(suffix_tokens), suffix_tokens
    )
    scores = compute_scores(inputs)

    results = sorted(
        [RerankResult(index=i, relevance_score=s) for i, s in enumerate(scores)],
        key=lambda r: r.relevance_score,
        reverse=True,
    )
    if req.top_n is not None:
        results = results[: req.top_n]
    return RerankResponse(model=req.model, results=results)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
