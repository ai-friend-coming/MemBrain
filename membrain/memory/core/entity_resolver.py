"""Entity resolver: three-layer deduplication before DB write."""

from __future__ import annotations

import json as _json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from hashlib import blake2b
from typing import Any, Literal

from membrain.config import settings

# ── Normalization ──────────────────────────────────────────────────────────────


def _normalize(ref: str) -> str:
    """Lowercase and collapse whitespace."""
    return re.sub(r"\s+", " ", ref.lower()).strip()


def _normalize_fuzzy(ref: str) -> str:
    """Produce alphanumeric-only form for n-gram shingles."""
    cleaned = re.sub(r"[^a-z0-9' ]", " ", _normalize(ref))
    return re.sub(r"\s+", " ", cleaned).strip()


# ── Shingles + MinHash ─────────────────────────────────────────────────────────


def _shingles(fuzzy: str) -> set[str]:
    """3-gram shingles from space-stripped fuzzy form."""
    s = fuzzy.replace(" ", "")
    if not s:
        return set()
    if len(s) < 3:
        return {s}
    return {s[i : i + 3] for i in range(len(s) - 2)}


def _hash_shingle(shingle: str, seed: int) -> int:
    digest = blake2b(f"{seed}:{shingle}".encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big")


def _minhash_signature(shingles: set[str]) -> tuple[int, ...]:
    if not shingles:
        return tuple()
    n = settings.RESOLVER_MINHASH_PERMUTATIONS
    return tuple(min(_hash_shingle(sh, seed) for sh in shingles) for seed in range(n))


def _lsh_bands(sig: tuple[int, ...]) -> list[tuple[int, ...]]:
    band_size = settings.RESOLVER_MINHASH_BAND_SIZE
    sig_list = list(sig)
    bands = []
    for start in range(0, len(sig_list), band_size):
        band = tuple(sig_list[start : start + band_size])
        if len(band) == band_size:
            bands.append(band)
    return bands


# ── Entropy filter ─────────────────────────────────────────────────────────────


def _has_high_entropy(fuzzy: str) -> bool:
    token_count = len(fuzzy.split())
    if (
        len(fuzzy) < settings.RESOLVER_MIN_NAME_LENGTH
        and token_count < settings.RESOLVER_MIN_TOKEN_COUNT
    ):
        return False
    stripped = fuzzy.replace(" ", "")
    if not stripped:
        return False
    counts: dict[str, int] = {}
    for ch in stripped:
        counts[ch] = counts.get(ch, 0) + 1
    total = sum(counts.values())
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values())
    return entropy >= settings.RESOLVER_ENTROPY_THRESHOLD


# ── Jaccard ────────────────────────────────────────────────────────────────────


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


log = logging.getLogger(__name__)


@dataclass
class ResolverIndexes:
    entries: list[Any]  # list[CandidateEntry]
    by_entity_id: dict[str, Any]  # entity_id → EntityModel
    normalized_map: defaultdict[str, list[Any]]  # normalized_name → [CandidateEntry]
    lsh_buckets: defaultdict[tuple, list[str]]  # (band_idx, band) → [entity_id]
    aliases_by_entity: dict[str, list[str]]


@dataclass
class ResolverDecision:
    new_entity_ref: str
    action: Literal["keep", "merge"]
    target_entity_id: str | None = None
    resolved_via: Literal["exact", "minhash", "llm"] | None = None


def build_resolver_indexes(
    entries: list[Any],
    by_entity_id: dict[str, Any],
    aliases_by_entity: dict[str, list[str]],
) -> ResolverIndexes:
    normalized_map: defaultdict[str, list[Any]] = defaultdict(list)
    lsh_buckets: defaultdict[tuple, list[str]] = defaultdict(list)

    for entry in entries:
        norm = _normalize(entry.name)
        normalized_map[norm].append(entry)

        sig = _minhash_signature(entry.shingles)
        for band_idx, band in enumerate(_lsh_bands(sig)):
            lsh_buckets[(band_idx, band)].append(entry.entity_id)

    return ResolverIndexes(
        entries=entries,
        by_entity_id=by_entity_id,
        normalized_map=normalized_map,
        lsh_buckets=lsh_buckets,
        aliases_by_entity=aliases_by_entity,
    )


# ── Layer 1: exact normalized match ───────────────────────────────────────────


# Sentinel: Layer 1 found multiple entities for the same normalized name
LAYER1_AMBIGUOUS = "AMBIGUOUS"


def layer1_exact(
    new_ref: str, indexes: ResolverIndexes
) -> ResolverDecision | str | None:
    """Return ResolverDecision on match, LAYER1_AMBIGUOUS on multi-entity hit, None on miss."""
    norm = _normalize(new_ref)
    matches = indexes.normalized_map.get(norm, [])
    if not matches:
        return None
    unique_eids = {e.entity_id for e in matches}
    if len(unique_eids) > 1:
        return LAYER1_AMBIGUOUS
    return ResolverDecision(
        new_entity_ref=new_ref,
        action="merge",
        target_entity_id=next(iter(unique_eids)),
        resolved_via="exact",
    )


# ── Layer 2: MinHash LSH + Jaccard ────────────────────────────────────────────


def layer2_minhash(new_ref: str, indexes: ResolverIndexes) -> ResolverDecision | None:
    fuzzy = _normalize_fuzzy(new_ref)
    if not _has_high_entropy(fuzzy):
        return None

    new_shingles = _shingles(fuzzy)
    sig = _minhash_signature(new_shingles)

    candidate_eids: set[str] = set()
    for band_idx, band in enumerate(_lsh_bands(sig)):
        candidate_eids.update(indexes.lsh_buckets.get((band_idx, band), []))

    best_eid: str | None = None
    best_score = 0.0
    for entry in indexes.entries:
        if entry.entity_id not in candidate_eids:
            continue
        score = _jaccard(new_shingles, entry.shingles)
        if score > best_score:
            best_score = score
            best_eid = entry.entity_id

    if best_eid is not None and best_score >= settings.RESOLVER_JACCARD_THRESHOLD:
        return ResolverDecision(
            new_entity_ref=new_ref,
            action="merge",
            target_entity_id=best_eid,
            resolved_via="minhash",
        )
    return None


# ── Layer 3: LLM fallback ──────────────────────────────────────────────────────


async def layer3_llm(
    unresolved_refs: list[str],
    unresolved_descs: dict[str, str],
    indexes: ResolverIndexes,
    registry,
    factory,
    profile: str | None = None,
) -> list[ResolverDecision]:
    """Send unresolved new entities + deduplicated candidates to LLM."""
    if not unresolved_refs or not settings.RESOLVER_LLM_ENABLED:
        return [
            ResolverDecision(new_entity_ref=r, action="keep") for r in unresolved_refs
        ]

    # Dedup candidates by entity_id for LLM context
    seen_eids: set[str] = set()
    deduped_candidates: list[dict] = []
    eid_by_candidate_id: dict[int, str] = {}

    for entry in indexes.entries:
        if entry.entity_id in seen_eids:
            continue
        seen_eids.add(entry.entity_id)
        cid = len(deduped_candidates)
        ent_model = indexes.by_entity_id.get(entry.entity_id)
        deduped_candidates.append(
            {
                "id": cid,
                "canonical_ref": ent_model.canonical_ref if ent_model else entry.name,
                "aliases": indexes.aliases_by_entity.get(entry.entity_id, []),
                "desc": ent_model.desc if ent_model else "",
            }
        )
        eid_by_candidate_id[cid] = entry.entity_id

    # Build new entities context
    new_entities_ctx = [
        {"id": i, "ref": ref, "desc": unresolved_descs.get(ref, "")}
        for i, ref in enumerate(unresolved_refs)
    ]

    new_json = _json.dumps(new_entities_ctx, ensure_ascii=False)
    existing_json = _json.dumps(deduped_candidates, ensure_ascii=False)

    try:
        from membrain.agents.retry import run_agent_with_retry

        agent, agent_settings = factory.get_agent("entity-resolver", profile=profile)
        prompts = registry.render_prompts(
            "entity-resolver",
            new_entities_json=new_json,
            existing_entities_json=existing_json,
            profile=profile,
        )
        result = await run_agent_with_retry(
            agent,
            instructions=prompts,
            model_settings=agent_settings,
        )
    except Exception:
        log.warning("entity-resolver LLM failed, keeping all as create", exc_info=True)
        return [
            ResolverDecision(new_entity_ref=r, action="keep") for r in unresolved_refs
        ]

    new_id_to_ref = {i: ref for i, ref in enumerate(unresolved_refs)}
    decisions: list[ResolverDecision] = []
    resolved_new_ids: set[int] = set()

    for res in result.output.resolutions:
        nid = res.new_entity_id
        mid = res.matched_entity_id
        if nid not in new_id_to_ref:
            log.warning("entity-resolver returned invalid new_entity_id=%d", nid)
            continue
        resolved_new_ids.add(nid)
        if mid == -1:
            decisions.append(
                ResolverDecision(new_entity_ref=new_id_to_ref[nid], action="keep")
            )
            continue
        if mid not in eid_by_candidate_id:
            log.warning(
                "entity-resolver returned invalid matched_entity_id=%d for new_entity_id=%d",
                mid,
                nid,
            )
            decisions.append(
                ResolverDecision(new_entity_ref=new_id_to_ref[nid], action="keep")
            )
            continue
        target_eid = eid_by_candidate_id[mid]
        decisions.append(
            ResolverDecision(
                new_entity_ref=new_id_to_ref[nid],
                action="merge",
                target_entity_id=target_eid,
                resolved_via="llm",
            )
        )

    # Fill missing (LLM didn't return resolution)
    for nid, ref in new_id_to_ref.items():
        if nid not in resolved_new_ids:
            log.warning("entity-resolver missing resolution for id=%d (%s)", nid, ref)
            decisions.append(ResolverDecision(new_entity_ref=ref, action="keep"))

    return decisions


# ── Top-level entry point ──────────────────────────────────────────────────────


async def resolve_entities(
    decisions: list[dict],
    db,
    task_id: int,
    embed_client,
    registry,
    factory,
    profile: str | None = None,
) -> list[dict]:
    """Run three-layer resolution on create decisions. Returns updated decisions."""
    from membrain.infra.retrieval.candidate_retrieval import retrieve_candidate_pool

    create_decisions = [d for d in decisions if d["action"] == "create"]
    if not create_decisions:
        return decisions

    entity_names = [d["canonical_ref"] for d in create_decisions]
    entries, by_entity_id, aliases_by_entity = retrieve_candidate_pool(
        entity_names, task_id, db, embed_client
    )

    if not entries:
        return decisions

    indexes = build_resolver_indexes(entries, by_entity_id, aliases_by_entity)

    # Layer 1 + 2 pass
    unresolved_refs: list[str] = []
    resolver_map: dict[str, ResolverDecision] = {}

    for d in create_decisions:
        ref = d["canonical_ref"]
        dec = layer1_exact(ref, indexes)
        if dec is LAYER1_AMBIGUOUS:
            # Ambiguous exact match → skip Layer 2, go straight to Layer 3
            unresolved_refs.append(ref)
        elif dec is not None:
            resolver_map[ref] = dec
        else:
            # No exact match → try Layer 2
            dec = layer2_minhash(ref, indexes)
            if dec is not None:
                resolver_map[ref] = dec
            else:
                unresolved_refs.append(ref)

    # Layer 3
    if unresolved_refs:
        desc_map = {
            d["canonical_ref"]: d.get("updated_desc", "") for d in create_decisions
        }
        llm_decisions = await layer3_llm(
            unresolved_refs, desc_map, indexes, registry, factory, profile=profile
        )
        for dec in llm_decisions:
            resolver_map[dec.new_entity_ref] = dec

    # Apply resolver decisions back onto original decisions list
    result: list[dict] = []
    for d in decisions:
        if d["action"] != "create":
            result.append(d)
            continue
        ref = d["canonical_ref"]
        rdec = resolver_map.get(ref)
        if rdec is None or rdec.action == "keep":
            result.append(d)
            continue
        # Convert create → merge
        target = by_entity_id.get(rdec.target_entity_id)
        result.append(
            {
                "batch_ref": d["batch_ref"],
                "action": "merge",
                "target_entity_id": rdec.target_entity_id,
                "target_ref": target.canonical_ref if target else ref,
                "canonical_ref": target.canonical_ref if target else ref,
                "updated_desc": d.get("updated_desc", ""),
                "old_description": target.desc if target else "",
                "old_canonical_ref": target.canonical_ref if target else "",
            }
        )

    return result
