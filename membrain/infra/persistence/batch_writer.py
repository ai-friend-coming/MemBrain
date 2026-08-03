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
    FactSourceModel,
    FactStatus,
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
    """原子写入抽取结果、实体引用与事实来源。

    Args:
        db: 当前任务 schema 的数据库会话。
        task_id: 事实所属任务主键。
        batch_id: 当前抽取批次 ID。
        facts: 本批次抽取出的事实。
        decisions: 实体创建或合并决策。
        embed_client: 事实和实体使用的向量客户端。
        ref_to_entity_id: 实体引用到内部实体 ID 的映射。
        batch_index: 当前批次序号。
        session_number: 事实来源的内部会话序号。

    Returns:
        dict[str, str]: 更新后的实体引用映射。
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

    # 每个 API 写入批次先落为内部 session，事实来源关系复用该稳定主键。
    source_session_id: int | None = None
    if session_number is not None:
        from membrain.infra.models.dataset import ChatSessionModel

        source_session_id = (
            db.query(ChatSessionModel.id)
            .filter(
                ChatSessionModel.task_id == task_id,
                ChatSessionModel.session_number == session_number,
            )
            .scalar()
        )
        if source_session_id is None:
            raise RuntimeError(
                f"未找到事实来源会话: task_id={task_id}, session_number={session_number}"
            )

    # fact_ts 是来源消息时间；事实的语义时间已编码在文本的时间标记中。
    fact_keys = list(dict.fromkeys(fact["text"] for fact in facts))
    equivalent_fact_ids = list(
        dict.fromkeys(
            fact["_equivalent_fact_id"]
            for fact in facts
            if fact.get("_equivalent_fact_id") is not None
        )
    )
    equivalent_by_id: dict[int, FactModel] = {}
    if equivalent_fact_ids:
        equivalent_by_id = {
            row.id: row
            for row in db.query(FactModel)
            .filter(
                FactModel.task_id == task_id,
                FactModel.status == FactStatus.ACTIVE,
                FactModel.id.in_(equivalent_fact_ids),
            )
            .all()
        }

    # 复用 ID 已由上游同时校验文本语义和稳定实体身份。
    existing_by_key: dict[str, FactModel] = {}
    for fact in facts:
        matched = equivalent_by_id.get(fact.get("_equivalent_fact_id"))
        if matched is not None:
            existing_by_key.setdefault(fact["text"], matched)

    new_fact_items: list[tuple[str, dict]] = []
    new_keys: set[str] = set()
    for fact_dict in facts:
        key = fact_dict["text"]
        if key not in existing_by_key and key not in new_keys:
            new_keys.add(key)
            new_fact_items.append((key, fact_dict))

    new_fact_texts = [fact["text"] for _, fact in new_fact_items]
    try:
        fact_vecs = embed_client.embed(new_fact_texts) if new_fact_texts else []
    except Exception:
        log.warning(
            "Embedding failed for %d facts, storing without vectors",
            len(new_fact_texts),
        )
        fact_vecs = []

    # ── Phase 2: 新事实写入，已存在的事实只追加来源关系 ──
    fact_models: list[FactModel] = []
    new_by_key: dict[str, FactModel] = {}
    for index, (key, fact_dict) in enumerate(new_fact_items):
        fact_model = FactModel(
            task_id=task_id,
            text=fact_dict["text"],
            text_embedding=fact_vecs[index] if index < len(fact_vecs) else None,
            batch_id=batch_id,
            session_number=session_number,
            batch_index=batch_index,
            fact_ts=fact_dict.get("fact_ts"),
        )
        new_by_key[key] = fact_model
        fact_models.append(fact_model)

    if fact_models:
        db.add_all(fact_models)
        db.flush()  # 新事实先取得主键，随后建立来源关系。

    target_by_key = {**new_by_key, **existing_by_key}
    source_targets: list[FactModel] = []
    if source_session_id is not None:
        for key in fact_keys:
            source_targets.append(target_by_key[key])

    # ── Phase 3: FactRefs + search_text + TimeAnnotations → 1 flush ──
    fact_ref_keys = list(
        dict.fromkeys(
            (target_by_key[fact["text"]].id, ref_to_entity_id[ref], ref)
            for fact in facts
            for ref in _extract_bracket_refs(fact["text"])
            if ref in ref_to_entity_id
        )
    )
    existing_fact_refs: set[tuple[int, str, str]] = set()
    if fact_ref_keys:
        target_fact_ids = [fact_id for fact_id, _, _ in fact_ref_keys]
        existing_fact_refs = {
            (fact_id, entity_id, alias_text)
            for fact_id, entity_id, alias_text in db.query(
                FactRefModel.fact_id,
                FactRefModel.entity_id,
                FactRefModel.alias_text,
            )
            .filter(FactRefModel.fact_id.in_(target_fact_ids))
            .all()
        }
    fact_ref_models = [
        FactRefModel(fact_id=fact_id, entity_id=entity_id, alias_text=alias_text)
        for fact_id, entity_id, alias_text in fact_ref_keys
        if (fact_id, entity_id, alias_text) not in existing_fact_refs
    ]

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

    source_pairs = (
        list(
            dict.fromkeys(
                (
                    target.id,
                    source_session_id,
                )
                for target in source_targets
            )
        )
        if source_session_id is not None
        else []
    )
    existing_source_pairs: set[tuple[int, int]] = set()
    if source_pairs:
        source_fact_ids = [fact_id for fact_id, _ in source_pairs]
        existing_source_pairs = {
            (fact_id, session_id)
            for fact_id, session_id in db.query(
                FactSourceModel.fact_id,
                FactSourceModel.session_id,
            )
            .filter(
                FactSourceModel.fact_id.in_(source_fact_ids),
                FactSourceModel.session_id == source_session_id,
            )
            .all()
        }
    fact_source_models = [
        FactSourceModel(fact_id=fact_id, session_id=session_id)
        for fact_id, session_id in source_pairs
        if (fact_id, session_id) not in existing_source_pairs
    ]

    db.add_all(fact_ref_models + time_models + fact_source_models)
    db.flush()  # 来源关系必须与事实一起提交，避免出现无法溯源的记忆。

    return ref_to_entity_id
