"""Domain persistence for the memory ingestion pipeline."""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.orm import Session

from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.models.memory import (
    EntityModel,
    FactModel,
    FactRefModel,
    TimeAnnotationModel,
)

log = logging.getLogger(__name__)


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


_ENTITY_BRACKET_RE = re.compile(r"\[([^\[\]:]+)\]")


def _extract_bracket_refs(fact_text: str) -> list[str]:
    """Extract [bracketed] entity refs from a fact string, excluding time tokens."""
    return list(dict.fromkeys(_ENTITY_BRACKET_RE.findall(fact_text)))


_TIME_TOKEN_RE = re.compile(r"\[([^\[\]]+?)::([^\[\]]+)\]")


def _parse_time_tokens(fact_text: str) -> list[tuple[str, str]]:
    """Extract [raw::resolved] time tokens from a fact string."""
    return _TIME_TOKEN_RE.findall(fact_text)


def _build_search_text(fact_text: str, canon_refs: list[str]) -> str:
    """Build BM25 search_text from fact_text.

    - Time annotations [raw::resolved] → raw word only (drop resolved date)
    - Entity brackets [Name] → Name (drop brackets)
    - Canonical refs appended at end in brackets for alias matching
    """
    text = _TIME_TOKEN_RE.sub(r"\1", fact_text)
    text = _ENTITY_BRACKET_RE.sub(r"\1", text)
    if canon_refs:
        text += f" [{', '.join(canon_refs)}]"
    return text


def _create_new_entity(
    db: Session,
    task_id: int,
    batch_id: str,
    ref: str,
    dec: dict,
    embed_client: EmbeddingClient,
    ref_to_entity_id: dict[str, str],
) -> str:
    """Create a new entity. Does NOT flush — caller is responsible."""
    eid = _generate_id("ent")
    desc_text = dec.get("updated_desc", "")
    try:
        vec = (
            embed_client.embed_single(f"{dec['canonical_ref']} {desc_text}")
            if desc_text
            else None
        )
    except Exception:
        log.warning(
            "Embedding failed for new entity %s, storing without vector",
            dec["canonical_ref"],
        )
        vec = None

    ent = EntityModel(
        task_id=task_id,
        entity_id=eid,
        canonical_ref=dec["canonical_ref"],
        desc=desc_text,
        desc_embedding=vec,
        batch_id=batch_id,
    )
    db.add(ent)
    ref_to_entity_id[ref] = eid
    ref_to_entity_id[dec["canonical_ref"]] = eid
    return eid


def write_batch_results(
    db: Session,
    task_id: int,
    batch_id: str,
    facts: list[dict],
    decisions: list[dict],
    embed_client: EmbeddingClient,
    ref_to_entity_id: dict[str, str],
    batch_index: int | None = None,
    session_number: int | None = None,
) -> dict[str, str]:
    """Write memory pipeline results atomically (3 flushes).

    Returns updated ref_to_entity_id.
    """
    entity_id_to_canonical: dict[str, str] = {}

    # ── Phase 1: Entity decisions → 1 flush ──

    # Pre-fetch all merge targets in a single query
    merge_target_eids = [
        dec.get("target_entity_id")
        for dec in decisions
        if dec.get("action") == "merge" and dec.get("target_entity_id")
    ]
    from membrain.infra.queries import entities as entity_queries

    existing_entities: dict[str, EntityModel] = entity_queries.find_merge_targets(
        db, task_id, merge_target_eids
    )

    # Pre-fetch aliases for all merge targets (needed for alias-aware embedding)
    from membrain.infra.retrieval.candidate_retrieval import _fetch_aliases_by_entity

    aliases_for_merge: dict[str, list[str]] = _fetch_aliases_by_entity(
        db, set(merge_target_eids)
    )

    for dec in decisions:
        ref = dec["batch_ref"]
        action = dec["action"]

        if action == "create":
            eid = _create_new_entity(
                db,
                task_id,
                batch_id,
                ref,
                dec,
                embed_client,
                ref_to_entity_id,
            )
            entity_id_to_canonical[eid] = dec["canonical_ref"]

        elif action == "merge":
            target_eid = dec.get("target_entity_id")
            old_ent = existing_entities.get(target_eid) if target_eid else None

            if not old_ent:
                eid = _create_new_entity(
                    db,
                    task_id,
                    batch_id,
                    ref,
                    dec,
                    embed_client,
                    ref_to_entity_id,
                )
                entity_id_to_canonical[eid] = dec["canonical_ref"]
                continue

            desc_text = dec.get("updated_desc", old_ent.desc)
            canonical = dec.get("canonical_ref", old_ent.canonical_ref)
            batch_ref = dec.get("batch_ref", "")
            existing_aliases = aliases_for_merge.get(target_eid, [])
            unique_aliases = list(
                dict.fromkeys(existing_aliases + ([batch_ref] if batch_ref else []))
            )
            embed_parts = [canonical, " ".join(unique_aliases), desc_text]
            embed_text = " ".join(p for p in embed_parts if p).strip()
            try:
                vec = (
                    embed_client.embed_single(embed_text)
                    if embed_text
                    else old_ent.desc_embedding
                )
            except Exception:
                log.warning(
                    "Embedding failed for merged entity %s, keeping old vector",
                    dec["canonical_ref"],
                )
                vec = old_ent.desc_embedding

            # In-place update: no new row, keep original batch_id for checkpoint safety
            old_ent.canonical_ref = dec.get("canonical_ref", old_ent.canonical_ref)
            old_ent.desc = desc_text
            old_ent.desc_embedding = vec

            ref_to_entity_id[ref] = old_ent.entity_id
            entity_id_to_canonical[old_ent.entity_id] = dec.get(
                "canonical_ref", old_ent.canonical_ref
            )

    db.flush()  # flush 1: all entities

    # Resolve canonical refs for entity_ids from previous batches
    fact_texts = [f["text"] for f in facts]
    all_bracket_refs: set[str] = set()
    for fact_text in fact_texts:
        all_bracket_refs.update(_extract_bracket_refs(fact_text))

    missing_eids: set[str] = set()
    for ref_text in all_bracket_refs:
        eid = ref_to_entity_id.get(ref_text)
        if eid and eid not in entity_id_to_canonical:
            missing_eids.add(eid)
    if missing_eids:
        targets = entity_queries.find_merge_targets(db, task_id, list(missing_eids))
        for eid, ent in targets.items():
            entity_id_to_canonical[eid] = ent.canonical_ref

    try:
        fact_vecs = embed_client.embed(fact_texts) if fact_texts else []
    except Exception:
        log.warning(
            "Embedding failed for %d facts, storing without vectors", len(fact_texts)
        )
        fact_vecs = []

    # ── Phase 2: Facts → 1 flush ──
    fact_models: list[FactModel] = []
    for i, fact_dict in enumerate(facts):
        fm = FactModel(
            task_id=task_id,
            text=fact_dict["text"],
            text_embedding=fact_vecs[i] if i < len(fact_vecs) else None,
            batch_id=batch_id,
            session_number=session_number,
            batch_index=batch_index,
            fact_ts=fact_dict.get("fact_ts"),
        )
        fact_models.append(fm)
    if fact_models:
        db.add_all(fact_models)
        db.flush()  # flush 2: all fact IDs

    # ── Phase 3: FactRefs + search_text + TimeAnnotations → 1 flush ──
    fact_ref_models: list[FactRefModel] = []
    for fm in fact_models:
        bracket_refs = _extract_bracket_refs(fm.text)
        for br in bracket_refs:
            eid = ref_to_entity_id.get(br)
            if eid:
                fact_ref_models.append(
                    FactRefModel(fact_id=fm.id, entity_id=eid, alias_text=br)
                )

    # Populate search_text with canonical entity refs (in-memory)
    for fm in fact_models:
        bracket_refs = _extract_bracket_refs(fm.text)
        canon_refs: list[str] = []
        for br in bracket_refs:
            eid = ref_to_entity_id.get(br)
            if eid:
                cr = entity_id_to_canonical.get(eid)
                if cr and cr not in canon_refs:
                    canon_refs.append(cr)
        fm.search_text = _build_search_text(fm.text, canon_refs)

    # Time annotations
    time_models: list[TimeAnnotationModel] = []
    for fm in fact_models:
        tokens = _parse_time_tokens(fm.text)
        for raw, resolved in tokens:
            time_models.append(
                TimeAnnotationModel(
                    fact_id=fm.id,
                    time_raw=raw,
                    time_resolved=resolved,
                )
            )

    db.add_all(fact_ref_models + time_models)
    db.flush()  # flush 3: fact_refs + search_text + time_annotations

    return ref_to_entity_id
