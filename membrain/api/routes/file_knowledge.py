"""提供与长期记忆接口分离的 File RAG HTTP API。"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Path, UploadFile
from starlette.concurrency import run_in_threadpool

from membrain.api.schemas.file_knowledge import (
    FileDeleteResponse,
    FileIndexResponse,
    FileSearchRequest,
    FileSearchResponse,
    RetrievedFileChunkOut,
)
from membrain.api.trace import finish_trace, start_trace
from membrain.config import settings
from membrain.file_knowledge.parsing import FileParsingError
from membrain.file_knowledge.service import (
    DocumentConflictError,
    EmbeddingResultError,
    FileRAGInputError,
    delete_chat_library,
    delete_document,
    index_document,
    search_documents,
)
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.db import SessionLocal

router = APIRouter(prefix="/api/file-libraries", tags=["file-rag"])

ChatId = Annotated[str, Path(min_length=1, max_length=255)]
DocumentId = Annotated[str, Path(min_length=1, max_length=255)]


@router.put("/{chat_id}/documents/{document_id}", response_model=FileIndexResponse)
async def put_document(
    chat_id: ChatId,
    document_id: DocumentId,
    file: Annotated[UploadFile, File()],
    content_sha256: Annotated[str, Form(min_length=64, max_length=64)],
) -> FileIndexResponse:
    """解析上传文件并同步生成当前 Chat 的独立向量索引。

    Args:
        chat_id: 文件库唯一隔离键。
        document_id: 上游稳定附件 ID。
        file: TXT、Markdown 或文本型 PDF 原文件。
        content_sha256: 上游计算的文件 SHA-256。

    Returns:
        FileIndexResponse: 索引状态、切块统计和 Embedding trace。

    Raises:
        HTTPException: 文件超限、内容冲突、解析失败或 Embedding 服务失败。
    """

    content = await file.read(settings.FILE_RAG_MAX_FILE_BYTES + 1)
    if len(content) > settings.FILE_RAG_MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="文件大小超过 File RAG 配置上限")

    trace, trace_token = start_trace()
    try:

        def run_index():
            # 数据库会话和 HTTP 客户端都在工作线程内创建及关闭，避免跨线程复用。
            with SessionLocal() as db, EmbeddingClient() as embed_client:
                return index_document(
                    db,
                    embed_client,
                    chat_id=chat_id,
                    document_id=document_id,
                    file_name=file.filename or document_id,
                    mime_type=file.content_type or "application/octet-stream",
                    expected_sha256=content_sha256,
                    content=content,
                )

        result = await run_in_threadpool(run_index)
        return FileIndexResponse(**result.__dict__, trace=trace.snapshot())
    except (FileParsingError, FileRAGInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocumentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EmbeddingResultError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        finish_trace(trace_token)


@router.post("/{chat_id}/search", response_model=FileSearchResponse)
async def search_file_library(
    chat_id: ChatId,
    req: FileSearchRequest,
) -> FileSearchResponse:
    """在指定 Chat 的文件 chunk 中执行精确向量检索。

    Args:
        chat_id: 文件库唯一隔离键。
        req: 查询文本、返回数量和上下文 token 预算。

    Returns:
        FileSearchResponse: 结构化命中结果及可临时注入的文件上下文。

    Raises:
        HTTPException: 查询参数或 Embedding 服务响应无效。
    """

    trace, trace_token = start_trace()
    try:

        def run_search():
            # 查询不共享长期记忆的 task schema，只访问 public 文件索引表。
            with SessionLocal() as db, EmbeddingClient() as embed_client:
                return search_documents(
                    db,
                    embed_client,
                    chat_id=chat_id,
                    query=req.query,
                    top_k=req.top_k,
                    max_tokens=req.max_tokens,
                )

        result = await run_in_threadpool(run_search)
        return FileSearchResponse(
            chat_id=chat_id,
            packed_context=result.packed_context,
            packed_token_count=result.packed_token_count,
            chunks=[RetrievedFileChunkOut(**chunk.__dict__) for chunk in result.chunks],
            trace=trace.snapshot(),
        )
    except FileRAGInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (EmbeddingResultError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        finish_trace(trace_token)


@router.delete("/{chat_id}/documents/{document_id}", response_model=FileDeleteResponse)
async def remove_document(
    chat_id: ChatId,
    document_id: DocumentId,
) -> FileDeleteResponse:
    """删除指定 Chat 内一个文档及其全部派生 chunk。

    Args:
        chat_id: 文件库唯一隔离键。
        document_id: 待删除的上游附件 ID。

    Returns:
        FileDeleteResponse: 实际删除的文档和 chunk 数。
    """

    def run_delete():
        with SessionLocal() as db:
            return delete_document(db, chat_id=chat_id, document_id=document_id)

    result = await run_in_threadpool(run_delete)
    return FileDeleteResponse(
        chat_id=chat_id,
        document_id=document_id,
        **result.__dict__,
    )


@router.delete("/{chat_id}", response_model=FileDeleteResponse)
async def remove_file_library(chat_id: ChatId) -> FileDeleteResponse:
    """删除指定 Chat 文件库内的全部文档和派生 chunk。

    Args:
        chat_id: 待清理的文件库隔离键。

    Returns:
        FileDeleteResponse: 实际删除的文档和 chunk 数。
    """

    def run_delete():
        with SessionLocal() as db:
            return delete_chat_library(db, chat_id=chat_id)

    result = await run_in_threadpool(run_delete)
    return FileDeleteResponse(chat_id=chat_id, **result.__dict__)
