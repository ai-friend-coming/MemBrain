"""解析文件文本并按 token 窗口生成可向量化 chunk。"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

import tiktoken
from markitdown import (
    FileConversionException,
    MarkItDown,
    StreamInfo,
    UnsupportedFormatException,
)
from pypdf import PdfReader


class FileParsingError(ValueError):
    """表示文件格式、编码或可提取内容不满足 File RAG 要求。"""


@dataclass(frozen=True)
class ParsedSection:
    """描述解析器输出的一段连续文本及其可选 PDF 页码。"""

    text: str
    page_number: int | None = None


@dataclass(frozen=True)
class FileTextChunk:
    """描述一个保持来源页码的固定 token 窗口。"""

    index: int
    content: str
    token_count: int
    page_number: int | None = None


_ENCODER = tiktoken.get_encoding("cl100k_base")
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
_TEXT_MIME_TYPES = {"text/plain", "text/markdown"}
_MARKITDOWN_EXTENSIONS = {
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".csv",
    ".json",
    ".html",
    ".htm",
}
_MARKITDOWN_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/excel",
    "text/csv",
    "application/csv",
    "application/json",
    "text/json",
    "text/html",
    "application/xhtml+xml",
}
_MARKITDOWN = MarkItDown(enable_plugins=False)


def count_tokens(text: str) -> int:
    """计算 File RAG 切块和上下文预算使用的 token 数。"""

    return len(_ENCODER.encode(text))


def parse_file(file_name: str, mime_type: str, content: bytes) -> list[ParsedSection]:
    """解析 File RAG 白名单内的文本、文档、表格或 PDF 文件。

    Args:
        file_name: 用户上传时的原始文件名。
        mime_type: 上游提供的 MIME 类型。
        content: 原始文件字节。

    Returns:
        list[ParsedSection]: 非空文本段及其页码。

    Raises:
        FileParsingError: 文件格式不支持、编码无效或没有可提取文字。
    """

    suffix = PurePath(file_name).suffix.lower()
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if suffix in _TEXT_EXTENSIONS or normalized_mime in _TEXT_MIME_TYPES:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise FileParsingError("TXT/Markdown 文件必须使用 UTF-8 编码") from exc
        text = text.strip()
        if not text:
            raise FileParsingError("文件没有可索引文字")
        return [ParsedSection(text=text)]

    if suffix == ".pdf" or normalized_mime == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise FileParsingError("暂不支持加密 PDF")
            sections = [
                ParsedSection(text=text, page_number=index + 1)
                for index, page in enumerate(reader.pages)
                if (text := (page.extract_text() or "").strip())
            ]
        except FileParsingError:
            raise
        except Exception as exc:
            raise FileParsingError("PDF 解析失败") from exc
        if not sections:
            raise FileParsingError("PDF 没有可提取文字，扫描件暂不支持 OCR")
        return sections

    if suffix in _MARKITDOWN_EXTENSIONS or normalized_mime in _MARKITDOWN_MIME_TYPES:
        try:
            result = _MARKITDOWN.convert_stream(
                io.BytesIO(content),
                stream_info=StreamInfo(
                    filename=file_name,
                    extension=suffix,
                    mimetype=normalized_mime,
                ),
            )
            text = result.markdown.strip()
        except (UnsupportedFormatException, FileConversionException) as exc:
            raise FileParsingError("文件转换为 Markdown 失败") from exc
        if not text:
            raise FileParsingError("文件没有可索引文字")
        return [ParsedSection(text=text)]

    raise FileParsingError(
        "只支持 TXT、Markdown、JSON、CSV、HTML、DOCX、PPTX、XLSX、XLS 和文本型 PDF"
    )


def split_sections(
    sections: list[ParsedSection],
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[FileTextChunk]:
    """按固定 token 窗口切分解析结果，并保留相邻窗口 overlap。

    Args:
        sections: 文件解析产生的连续文本段。
        chunk_tokens: 单个 chunk 的最大 token 数。
        overlap_tokens: 相邻 chunk 重复的 token 数。

    Returns:
        list[FileTextChunk]: 按原文件顺序编号的非空 chunk。

    Raises:
        ValueError: 窗口或 overlap 配置无效。
    """

    if chunk_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("File RAG chunk token 配置无效")

    chunks: list[FileTextChunk] = []
    for section in sections:
        tokens = _ENCODER.encode(section.text)
        start = 0
        while start < len(tokens):
            end = min(start + chunk_tokens, len(tokens))
            content = _ENCODER.decode(tokens[start:end]).strip()
            if content:
                chunks.append(
                    FileTextChunk(
                        index=len(chunks),
                        content=content,
                        token_count=count_tokens(content),
                        page_number=section.page_number,
                    )
                )
            if end == len(tokens):
                break
            start = end - overlap_tokens
    return chunks
