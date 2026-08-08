"""定义独立 File RAG HTTP API 的请求与响应结构。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from membrain.api.schemas.common import TraceOut
from membrain.config import settings


class FileIndexResponse(BaseModel):
    """返回一个文件完成解析和向量索引后的事实。"""

    status: str
    chat_id: str
    document_id: str
    content_sha256: str
    file_name: str
    mime_type: str
    chunk_count: int
    extracted_tokens: int
    trace: TraceOut


class FileSearchRequest(BaseModel):
    """接收一个 Chat 文件库的向量检索参数。"""

    query: str = Field(min_length=1)
    top_k: int = Field(
        default=settings.FILE_RAG_TOP_K,
        ge=1,
        le=settings.FILE_RAG_MAX_TOP_K,
    )
    max_tokens: int = Field(
        default=settings.FILE_RAG_MAX_CONTEXT_TOKENS,
        ge=1,
        le=settings.FILE_RAG_MAX_CONTEXT_TOKENS,
    )


class RetrievedFileChunkOut(BaseModel):
    """返回命中的文件 chunk、来源元数据和向量分数。"""

    chunk_id: int
    document_id: str
    file_name: str
    chunk_index: int
    page_number: int | None = None
    token_count: int
    score: float
    content: str


class FileSearchResponse(BaseModel):
    """返回当前 Chat 文件库的结构化召回和可注入上下文。"""

    chat_id: str
    packed_context: str
    packed_token_count: int
    chunks: list[RetrievedFileChunkOut]
    trace: TraceOut


class FileDeleteResponse(BaseModel):
    """返回文件库删除操作实际移除的文档和 chunk 数。"""

    chat_id: str
    document_id: str | None = None
    deleted_documents: int
    deleted_chunks: int
