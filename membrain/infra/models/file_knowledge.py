"""定义与长期记忆表完全分离的文件知识库持久化模型。"""

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from membrain.config import settings
from membrain.infra.db import Base


class FileDocumentModel(Base):
    """保存一个 Chat 内完成向量化的文件索引事实。"""

    __tablename__ = "file_documents"
    __table_args__ = (
        UniqueConstraint("chat_id", "document_id", name="uq_file_document_chat_id"),
        Index("ix_file_documents_chat_id", "chat_id"),
    )

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(255), nullable=False)
    document_id = Column(String(255), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    file_name = Column(String(512), nullable=False)
    mime_type = Column(String(255), nullable=False)
    chunk_count = Column(Integer, nullable=False)
    extracted_tokens = Column(Integer, nullable=False)
    index_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    chunks = relationship(
        "FileChunkModel",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FileChunkModel(Base):
    """保存一个文件切块及其 Embedding，不参与 memory fact 检索。"""

    __tablename__ = "file_chunks"
    __table_args__ = (
        UniqueConstraint("document_pk", "chunk_index", name="uq_file_chunk_index"),
        Index("ix_file_chunks_document_pk", "document_pk"),
    )

    id = Column(Integer, primary_key=True)
    document_pk = Column(
        Integer,
        ForeignKey("file_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    context_prefix = Column(Text, nullable=False)
    retrieval_text = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=True)
    embedding = Column(HALFVEC(settings.EMBED_DIM), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    document = relationship("FileDocumentModel", back_populates="chunks")
