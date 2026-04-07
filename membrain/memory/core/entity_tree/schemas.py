"""Pydantic schemas and validators for Entity Tree V3 LLM calls.

Covers:
- Audit Across: AuditAcrossResult (PROMOTE / RELOCATE)
- Audit Down:   AuditDownResult (GROUP only)
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext

log = logging.getLogger(__name__)

# ── Slow Path: audit_across ─────────────────────────────


class AcrossAction(BaseModel):
    action: Literal["PROMOTE", "RELOCATE"]
    target_id: str  # child ID (c0, c1, ...)
    destination_id: str | None = None  # sibling ID for RELOCATE, None for PROMOTE


class AuditAcrossResult(BaseModel):
    actions: list[AcrossAction]


# ── Slow Path: audit_down ───────────────────────────────


class DownAction(BaseModel):
    action: Literal["GROUP"]
    target_ids: list[str]
    label: str


class AuditDownResult(BaseModel):
    actions: list[DownAction]


_registered: set[int] = set()


def register_audit_across_validator(agent: Agent) -> None:
    if id(agent) in _registered:
        return
    _registered.add(id(agent))

    @agent.output_validator
    async def validate_across(ctx: RunContext[dict], result) -> object:
        child_ids: set[str] = set(ctx.deps.get("child_ids", []))
        sibling_ids: set[str] = set(ctx.deps.get("sibling_ids", []))
        used: set[str] = set()
        for a in result.actions:
            if a.target_id not in child_ids:
                raise ModelRetry(f"Unknown child {a.target_id}")
            if a.target_id in used:
                raise ModelRetry(f"Duplicate action for {a.target_id}")
            used.add(a.target_id)
            if a.action == "PROMOTE":
                if a.destination_id is not None:
                    raise ModelRetry("PROMOTE must not have destination_id")
            elif a.action == "RELOCATE":
                if not a.destination_id:
                    raise ModelRetry("RELOCATE requires destination_id")
                if a.destination_id not in sibling_ids:
                    raise ModelRetry(f"Unknown sibling {a.destination_id}")
        return result


def register_audit_down_validator(agent: Agent) -> None:
    if id(agent) in _registered:
        return
    _registered.add(id(agent))

    @agent.output_validator
    async def validate_down(ctx: RunContext[dict], result) -> object:
        child_ids: set[str] = set(ctx.deps.get("child_ids", []))
        grouped: set[str] = set()
        for a in result.actions:
            for tid in a.target_ids:
                if tid not in child_ids:
                    raise ModelRetry(f"Unknown target {tid}")
            if a.action == "GROUP":
                if len(a.target_ids) < 2:
                    raise ModelRetry("GROUP requires >= 2 targets")
                if not a.label:
                    raise ModelRetry("GROUP requires a label")
                for tid in a.target_ids:
                    if tid in grouped:
                        raise ModelRetry(f"{tid} in multiple GROUPs")
                    grouped.add(tid)
        return result
