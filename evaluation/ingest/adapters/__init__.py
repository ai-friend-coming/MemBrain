from evaluation.ingest.adapters.base import BaseAdapter, convert_evidence
from evaluation.ingest.adapters.knowmebench import KnowMeBenchAdapter
from evaluation.ingest.adapters.locomo import LoCoMoAdapter
from evaluation.ingest.adapters.longmemeval import LongMemEvalAdapter
from evaluation.ingest.adapters.personamemv2 import PersonaMemV2Adapter

REGISTRY: dict[str, type[BaseAdapter]] = {
    LoCoMoAdapter.NAME: LoCoMoAdapter,
    LongMemEvalAdapter.NAME: LongMemEvalAdapter,
    PersonaMemV2Adapter.NAME: PersonaMemV2Adapter,
    KnowMeBenchAdapter.NAME: KnowMeBenchAdapter,
}

__all__ = [
    "BaseAdapter",
    "convert_evidence",
    "KnowMeBenchAdapter",
    "LoCoMoAdapter",
    "LongMemEvalAdapter",
    "PersonaMemV2Adapter",
    "REGISTRY",
]
