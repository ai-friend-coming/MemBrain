"""Configuration management using Pydantic Settings."""

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM
    LLM_API_URL: str = "http://localhost:4000/v1"
    LLM_API_KEY: str = "sk-1234"

    # Database
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWD: str = "MemBrain"
    DB_NAME: str = "MemBrain"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 60
    DB_POOL_RECYCLE: int = 3600
    DB_STATEMENT_TIMEOUT: int = 300000  # ms (5 minutes)
    DB_WORKER_POOL_SIZE: int = 1
    DB_WORKER_MAX_OVERFLOW: int = 0

    # Server
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 8094
    # dev        — 1 worker, reload enabled (file-watch hot reload)
    # evaluation — 1 worker, no reload (stable, for exp run / QA pipelines)
    # demo       — BACKEND_WORKERS workers, no reload (for conversation demo)
    BACKEND_MODE: str = "dev"
    BACKEND_WORKERS: int = 2  # only used in demo mode
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Paths
    EXPS_DIR: str = "evaluation/exps"

    # Embedding service
    EMBED_SERVICE_URL: str = "http://localhost:9113/v1/embeddings"
    EMBED_API_KEY: str = ""
    EMBED_MODEL: str = "qwen3-embed"
    EMBED_DIM: int = 2560  # must match model; if changed, recreate DB

    # Rerank service
    RERANK_SERVICE_URL: str = "http://localhost:9114/v1/rerank"
    RERANK_API_KEY: str = ""
    RERANK_MODEL: str = "qwen3-rerank"

    # Fact extraction batching
    EXTRACT_BATCH_MAX_MESSAGES: int = 10
    EXTRACT_BATCH_MAX_CHARS: int = 1500
    EXTRACT_CONTEXT_TAIL_SIZE: int = 10

    # Entity resolution
    RESOLVER_CANDIDATE_TOP_K: int = 10
    RESOLVER_JACCARD_THRESHOLD: float = 0.9
    RESOLVER_ENTROPY_THRESHOLD: float = 1.5
    RESOLVER_MIN_NAME_LENGTH: int = 6
    RESOLVER_MIN_TOKEN_COUNT: int = 2
    RESOLVER_MINHASH_PERMUTATIONS: int = 32
    RESOLVER_MINHASH_BAND_SIZE: int = 4
    RESOLVER_LLM_ENABLED: bool = True

    # Two-pass extraction context
    EXTRACTION_CONTEXT_TOP_K: int = 20
    EXTRACTION_CONTEXT_PER_QUERY: int = 5

    # Entity canonicalizer
    CANONICALIZER_ENABLED: bool = True

    # Entity tree (hierarchical clustering)
    TREE_MAX_CHILDREN: int = 15
    TREE_MERGE_THRESHOLD: int = 8  # below this, GROUP flattens instead of wrapping
    AUDIT_DOWN_WARMUP_MIN: int = (
        5  # skip audit_down when root has only leaves <= this count
    )
    AUDIT_MIN_UNCERTAINTY: float = 10.0  # per-batch audit threshold
    AUDIT_MAX_K: int = 5  # max nodes to audit per entity tree per batch

    # Entity tree structural improvements
    DEPTH_D0: int = 2  # D_max formula constant
    DEPTH_C: float = 1.3  # D_max formula log coefficient
    MIN_FRESH_FOR_PROPAGATE: int = 3  # min fresh_count for upward propagation
    ALPHA_DESC: float = 0.5  # routing weight: description vs centroid
    W_SOFT_BASE: float = 3  # expected width baseline
    W_SOFT_LOG: float = 1.5  # expected width log coefficient
    W_WIDTH: float = 3.0  # width pressure weight in debt
    W_DEPTH: float = 5.0  # depth pressure weight in debt

    # PersonaMEM v2 virtual session splitting
    PERSONAMEM_VIRTUAL_SESSION_SIZE: int = 50

    # Session summary
    SUMMARY_SESSION_MAX_CHARS: int = 8000
    MSG_COMPRESS_THRESHOLD: int = (
        4000  # pre-compress messages longer than this (longmemeval)
    )

    # QA retrieval
    QA_BM25_FACT_TOP_N: int = 20
    QA_EMBED_FACT_TOP_N: int = 20
    QA_ENTITY_TOP_N: int = 5
    QA_TREE_BEAM_WIDTH: int = 3
    QA_TREE_FACT_TOP_N: int = 20
    QA_BUDGET_MAX_TOKENS: int = 2000
    QA_MAX_PER_LEAF_ASPECT: int = 3
    QA_MAX_PER_MID_ASPECT: int = 8
    QA_RERANK_TOP_K: int = 12
    QA_MULTI_QUERY_ENABLED: bool = True
    QA_SESSION_BM25_TOP_N: int = 10
    QA_SESSION_FINAL_TOP_N: int = 5
    QA_BM25_MSG_TOP_N: int = 5
    QA_LLM_MODEL: str = "gpt-4.1-mini"
    QA_LLM_COT_MAX_TOKENS: int = 1024
    QA_SEARCH_POOL_SIZE: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @cached_property
    def exps_dir_path(self) -> Path:
        """Resolve EXPS_DIR to an absolute path anchored at the project root."""
        p = Path(self.EXPS_DIR)
        return p if p.is_absolute() else _PROJECT_ROOT / p

    @cached_property
    def DATABASE_URL(self) -> str:
        """PostgreSQL connection URL."""
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
