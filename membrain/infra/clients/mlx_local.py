"""提供 Mac 本机 MLX embedding 与 rerank 推理封装。"""

from __future__ import annotations

from functools import lru_cache

import mlx.core as mx
from mlx_embeddings import generate as generate_embeddings
from mlx_embeddings import load as load_embedding_model
from mlx_lm import load as load_lm_model

from membrain.config import settings

_RERANK_SYSTEM = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
    'Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n"
    "<|im_start|>user\n"
)
_RERANK_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


@lru_cache(maxsize=1)
def _load_embedder():
    """加载 MLX embedding 模型，并在进程内复用权重。"""
    return load_embedding_model(settings.MLX_EMBED_MODEL, lazy=True)


@lru_cache(maxsize=1)
def _load_reranker():
    """加载 MLX rerank 模型，并缓存 yes/no token 位置。"""
    model, tokenizer = load_lm_model(settings.MLX_RERANK_MODEL, lazy=True)
    yes_id = tokenizer.encode("yes", add_special_tokens=False)[0]
    no_id = tokenizer.encode("no", add_special_tokens=False)[0]
    return model, tokenizer, yes_id, no_id


def mlx_embed(texts: list[str]) -> list[list[float]]:
    """计算文本向量，返回可写入 halfvec 的 Python float 列表。"""
    model, tokenizer = _load_embedder()
    output = generate_embeddings(
        model,
        tokenizer,
        texts,
        max_length=settings.MLX_EMBED_MAX_LENGTH,
        padding=True,
        truncation=True,
    )
    # Qwen3 embedding 模型输出已归一化的 text_embeds，维度由 EMBED_DIM 约束。
    embeddings = output.text_embeds
    mx.eval(embeddings)
    return embeddings.astype(mx.float32).tolist()


def _format_rerank_pair(query: str, document: str) -> str:
    """按 Qwen3 reranker 官方提示格式拼接 query-document 对。"""
    body = (
        f"<Instruct>: {settings.MLX_RERANK_INSTRUCTION}\n"
        f"<Query>: {query}\n"
        f"<Document>: {document}"
    )
    return _RERANK_SYSTEM + body + _RERANK_SUFFIX


def mlx_rerank(query: str, documents: list[str], top_n: int) -> list[dict]:
    """使用 yes/no token 概率对候选文档排序。"""
    if not documents:
        return []

    model, tokenizer, yes_id, no_id = _load_reranker()
    scores: list[tuple[int, float]] = []
    for index, document in enumerate(documents):
        prompt = _format_rerank_pair(query, document)
        token_ids = tokenizer.encode(prompt, add_special_tokens=False)
        token_ids = token_ids[-settings.MLX_RERANK_MAX_LENGTH :]
        logits = model(mx.array([token_ids]))
        last_logits = logits[0, -1, :]
        pair_logits = mx.stack([last_logits[no_id], last_logits[yes_id]])
        score = mx.softmax(pair_logits, axis=0)[1]
        mx.eval(score)
        scores.append((index, float(score.item())))

    scores.sort(key=lambda item: item[1], reverse=True)
    return [
        {"index": index, "relevance_score": score} for index, score in scores[:top_n]
    ]
