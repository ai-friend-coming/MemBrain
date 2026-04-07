"""Eval profile registry — maps dataset names to profile classes."""

from __future__ import annotations

from evaluation.answering.profiles.base import BaseEvalProfile
from evaluation.answering.profiles.knowmebench import KnowMeBenchProfile
from evaluation.answering.profiles.locomo import LoCoMoEvalProfile
from evaluation.answering.profiles.longmemeval import LongMemEvalProfile
from evaluation.answering.profiles.personamemv2 import PersonaMemV2Profile

_PROFILE_REGISTRY: dict[str, type[BaseEvalProfile]] = {
    "locomo": LoCoMoEvalProfile,
    "longmemeval": LongMemEvalProfile,
    "personamemv2": PersonaMemV2Profile,
    "knowmebench": KnowMeBenchProfile,
}


def get_profile(name: str, ranker: str = "rrf") -> BaseEvalProfile:
    """Instantiate the eval profile for the given dataset.

    Falls back to BaseEvalProfile for unknown names.
    """
    cls = _PROFILE_REGISTRY.get(name, BaseEvalProfile)
    return cls(ranker=ranker)
