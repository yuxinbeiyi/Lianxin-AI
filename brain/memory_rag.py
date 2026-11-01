"""
memory_rag.py — 记忆向量检索 (RAG)
sentence-transformers 本地 embedding → 语义搜索 → 注入聊天气泡
"""

import logging
import math
import re
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

import os as _os
_os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


_metrics_lock = threading.Lock()
_retrieval_metrics = {
    "queries": 0,
    "total_ms": 0.0,
    "last_ms": 0.0,
    "recent_ms": [],
    "ann_used": 0,
    "sqlite_fallbacks": 0,
    "vector_candidates": 0,
    "keyword_candidates": 0,
    "final_results": 0,
    "fts_fallbacks": 0,
}


def get_retrieval_metrics(*, reset: bool = False) -> dict:
    """Return lightweight in-process RAG timing metrics for diagnostics."""
    with _metrics_lock:
        recent = list(_retrieval_metrics["recent_ms"])
        queries = int(_retrieval_metrics["queries"])
        total_ms = float(_retrieval_metrics["total_ms"])
        result = {
            "queries": queries,
            "total_ms": round(total_ms, 3),
            "last_ms": round(float(_retrieval_metrics["last_ms"]), 3),
            "average_ms": round(total_ms / queries, 3) if queries else 0.0,
            "p95_ms": round(sorted(recent)[max(0, int(len(recent) * 0.95) - 1)], 3)
            if recent else 0.0,
            "ann_used": int(_retrieval_metrics["ann_used"]),
            "sqlite_fallbacks": int(_retrieval_metrics["sqlite_fallbacks"]),
            "average_vector_candidates": round(
                float(_retrieval_metrics["vector_candidates"]) / queries, 3
            ) if queries else 0.0,
            "average_keyword_candidates": round(
                float(_retrieval_metrics["keyword_candidates"]) / queries, 3
            ) if queries else 0.0,
            "average_final_results": round(
                float(_retrieval_metrics["final_results"]) / queries, 3
            ) if queries else 0.0,
            "fts_fallbacks": int(_retrieval_metrics["fts_fallbacks"]),
        }
        if reset:
            _retrieval_metrics["queries"] = 0
            _retrieval_metrics["total_ms"] = 0.0
            _retrieval_metrics["last_ms"] = 0.0
            _retrieval_metrics["recent_ms"] = []
            _retrieval_metrics["ann_used"] = 0
            _retrieval_metrics["sqlite_fallbacks"] = 0
            _retrieval_metrics["vector_candidates"] = 0
            _retrieval_metrics["keyword_candidates"] = 0
            _retrieval_metrics["final_results"] = 0
            _retrieval_metrics["fts_fallbacks"] = 0
        return result


def _record_retrieval_time(elapsed_ms: float, trace: dict | None = None) -> None:
    try:
        enabled = True
        from config import get_rag_config
        enabled = bool(get_rag_config().get("rag_metrics_enabled", True))
        if not enabled:
            return
    except Exception:
        pass
    with _metrics_lock:
        _retrieval_metrics["queries"] += 1
        _retrieval_metrics["total_ms"] += elapsed_ms
        _retrieval_metrics["last_ms"] = elapsed_ms
        recent = _retrieval_metrics["recent_ms"]
        recent.append(elapsed_ms)
        del recent[:-200]
        trace = trace or {}
        if trace.get("ann_used"):
            _retrieval_metrics["ann_used"] += 1
        elif trace.get("semantic_attempted"):
            _retrieval_metrics["sqlite_fallbacks"] += 1
        if trace.get("fts_fallback"):
            _retrieval_metrics["fts_fallbacks"] += 1
        _retrieval_metrics["vector_candidates"] += int(trace.get("vector_candidates", 0) or 0)
        _retrieval_metrics["keyword_candidates"] += int(trace.get("keyword_candidates", 0) or 0)
        _retrieval_metrics["final_results"] += int(trace.get("final_results", 0) or 0)


def _get_rag_config() -> dict:
    try:
        from config import get_rag_config
        return get_rag_config()
    except Exception:
        return {
            "rag_vector_candidate_k": 200,
            "rag_keyword_candidate_k": 100,
            "rag_final_top_k": 3,
            "rag_rrf_k": 60,
            "rag_metrics_enabled": True,
        }


def _ensure_torch_fsdp_compat():
    """torch 2.5.1 移除了 torch.distributed.fsdp，sentence-transformers 依赖它。
    要求在主线程先调用 _preload_torch() 完成 torch 初始化，避免子线程 access violation。"""
    import sys as _sys
    import types as _types

    _fsdp = _types.ModuleType("torch.distributed.fsdp")
    _fsdp.FullyShardedDataParallel = type("FullyShardedDataParallel", (), {})
    _fsdp.ShardingStrategy = type("ShardingStrategy", (), {
        "FULL_SHARD": 1, "SHARD_GRAD_OP": 2, "NO_SHARD": 3, "HYBRID_SHARD": 4, "_HYBRID_SHARD_ZERO2": 5
    })
    _fsdp.StateDictType = type("StateDictType", (), {
        "FULL_STATE_DICT": 1, "LOCAL_STATE_DICT": 2, "SHARDED_STATE_DICT": 3
    })

    if "torch.distributed" in _sys.modules:
        _dist = _sys.modules["torch.distributed"]
        if not hasattr(_dist, "fsdp"):
            _dist.fsdp = _fsdp
    else:
        _dist = _types.ModuleType("torch.distributed")
        _dist.fsdp = _fsdp
        _dist.is_available = lambda: False
        _dist.is_initialized = lambda: False
        _dist.init_process_group = lambda **kw: None
        _dist.get_rank = lambda: -1
        _dist.get_world_size = lambda: 1
        _sys.modules["torch.distributed"] = _dist
        _sys.modules["torch.distributed.fsdp"] = _fsdp


def _preload_torch():
    """Compatibility wrapper for deferred, main-thread Torch initialization."""
    try:
        from utils.torch_runtime import ensure_ready
        return ensure_ready()
    except Exception as e:
        logger.warning("torch 初始化失败: %s", e)
        return False

logger = logging.getLogger("MemoryRAG")


def _recency_score(timestamp: str, config: dict) -> float:
    if not bool(config.get("rag_time_decay_enabled", True)):
        return 0.0
    try:
        age_days = max(0.0, (datetime.now() - datetime.strptime(
            timestamp, "%Y-%m-%d %H:%M:%S"
        )).total_seconds() / 86400.0)
        half_life = max(1.0, float(config.get("rag_time_decay_half_life_days", 90) or 90))
        return float(math.exp(-math.log(2.0) * age_days / half_life))
    except (TypeError, ValueError):
        return 0.0


def _text_overlap(left: str, right: str) -> float:
    left_terms = set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", str(left or "")))
    right_terms = set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", str(right or "")))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / max(1, len(left_terms | right_terms))


def _apply_mmr(candidates: list[tuple[float, dict]], top_k: int, config: dict):
    if not candidates or not bool(config.get("rag_mmr_enabled", True)):
        return candidates[:top_k]
    candidate_k = max(top_k, int(config.get("rag_mmr_candidate_k", 20) or 20))
    pool = candidates[:candidate_k]
    if len(pool) <= top_k:
        return pool
    lam = min(1.0, max(0.0, float(config.get("rag_mmr_lambda", 0.78) or 0.78)))
    selected = []
    remaining = list(pool)
    while remaining and len(selected) < top_k:
        best_index, best_value = 0, float("-inf")
        for index, (score, item) in enumerate(remaining):
            redundancy = max((_text_overlap(item.get("content", ""), chosen[1].get("content", ""))
                              for chosen in selected), default=0.0)
            value = lam * float(score) - (1.0 - lam) * redundancy
            if value > best_value:
                best_value, best_index = value, index
        selected.append(remaining.pop(best_index))
    return selected

# 全局单例
_model: Optional["SentenceTransformer"] = None
_model_name = "BAAI/bge-small-zh-v1.5"  # 96MB, 中文优化, 512维
_load_attempted = False


def _load_sentence_transformer(cls, device: str):
    """Construct the model, falling back to CPU when CUDA is unavailable."""
    try:
        return cls(_model_name, device=device)
    except Exception as exc:
        if str(device).startswith("cuda"):
            logger.warning("RAG CUDA 加载失败，回退 CPU: %s", exc)
            return cls(_model_name, device="cpu")
        raise


def _get_model():
    """懒加载 embedding 模型（首次调用约 5 秒下载）。"""
    global _model, _load_attempted
    if _model is None and not _load_attempted:
        # FunASR and SentenceTransformer both initialise native Torch objects.
        # Serialising just this construction avoids Windows heap corruption while
        # keeping normal model inference non-blocking.
        from utils.torch_model_loading import torch_model_load_lock
        with torch_model_load_lock:
            if _model is not None:
                return _model
            _load_attempted = True
            if not _preload_torch():
                return None
            _ensure_torch_fsdp_compat()
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading embedding model: {_model_name}")
                from config import resolve_device
                _model = _load_sentence_transformer(SentenceTransformer, resolve_device("rag"))
                logger.info("Embedding model ready")
            except ImportError:
                logger.warning("sentence-transformers not installed, RAG disabled")
            except Exception as e:
                err_str = str(e).lower()
                # hf-mirror.com 不稳定时自动回退官方 HuggingFace Hub
                if "hf-mirror" in err_str or "504" in err_str or "502" in err_str or \
                   "timeout" in err_str or "connection" in err_str or "reset" in err_str:
                    logger.warning(f"hf-mirror.com 下载失败，切换官方源重试: {e}")
                    _saved = _os.environ.get("HF_ENDPOINT", "")
                    _os.environ["HF_ENDPOINT"] = "https://huggingface.co"
                    try:
                        _model = _load_sentence_transformer(SentenceTransformer, resolve_device("rag"))
                        logger.info("Embedding model ready (via official HuggingFace)")
                    except Exception as e2:
                        logger.warning(f"Embedding model load failed (mirror & official 均失败): {e2}")
                    finally:
                        if _saved:
                            _os.environ["HF_ENDPOINT"] = _saved
                        else:
                            _os.environ.pop("HF_ENDPOINT", None)
                else:
                    logger.warning(f"Embedding model load failed: {e}")
        return _model
    return _model


def embed(text: str) -> Optional[np.ndarray]:
    """将文本编码为归一化向量。返回 None 表示模型不可用。"""
    model = _get_model()
    if model is None:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.astype(np.float32)
    except Exception as e:
        logger.debug(f"Embed failed: {e}")
        return None


def embed_bytes(text: str) -> Optional[bytes]:
    """编码为 bytes（用于 SQLite BLOB 存储）。"""
    vec = embed(text)
    if vec is None:
        return None
    return vec.tobytes()


def search_similar(
    query: str,
    top_k: int = 3,
    threshold: float = 0.5,
    category: str = None,
    track_access: bool = True,
    hybrid: bool = True,
    allow_model_load: Optional[bool] = None,
) -> list[tuple[float, dict]]:
    """Run RAG retrieval and record its end-to-end latency."""
    started = time.perf_counter()
    trace = {}
    try:
        return _search_similar(
            query, top_k=top_k, threshold=threshold, category=category,
            track_access=track_access, hybrid=hybrid,
            allow_model_load=allow_model_load, _trace=trace,
        )
    finally:
        _record_retrieval_time((time.perf_counter() - started) * 1000.0, trace)


def _search_similar(
    query: str,
    top_k: int = 3,
    threshold: float = 0.5,
    category: str = None,
    track_access: bool = True,
    hybrid: bool = True,
    allow_model_load: Optional[bool] = None,
    _trace: dict | None = None,
) -> list[tuple[float, dict]]:
    """混合检索记忆：向量、关键词和叙事层结果用 RRF 融合。

    Args:
        query: 查询文本（通常是用户最后一条消息）
        top_k: 返回条数
        threshold: 最低相似度阈值 (0~1)
        category: 可选，限制分类

    Returns:
        [(融合分数, 记忆dict), ...] 按融合分数降序
    """
    rag_config = _get_rag_config()
    trace = _trace if _trace is not None else {}
    top_k = max(1, int(top_k))
    # Preserve the public default while allowing deployments to tune the
    # amount of context without changing every caller.
    if top_k == 3:
        top_k = max(1, int(rag_config.get("rag_final_top_k", 3) or 3))
    vector_candidate_k = max(
        max(1, int(top_k)),
        int(rag_config.get("rag_vector_candidate_k", 200) or 200),
    )
    model = _model
    if model is None and _should_load_semantic_model(query, allow_model_load):
        trace["semantic_attempted"] = True
        model = _get_model()
    if model is None:
        if _load_attempted:
            logger.debug("RAG search skipped: model unavailable (load previously attempted)")
        if hybrid and bool(rag_config.get("rag_fts_only_fallback_enabled", True)):
            trace["fts_fallback"] = True
            trace["retrieval_backend"] = "fts5_or_like"
            merged = _merge_hybrid_results(
                query, [], category=category, top_k=top_k, trace=trace
            )
        else:
            merged = []
        _record_hybrid_access(merged, track_access)
        return merged

    q_vec = embed(query)
    if q_vec is None:
        trace["fts_fallback"] = True
        trace["retrieval_backend"] = "fts5_or_like"
        merged = _merge_hybrid_results(
            query, [], category=category, top_k=top_k, trace=trace
        ) if hybrid else []
        _record_hybrid_access(merged, track_access)
        return merged

    try:
        from brain.graph_memory import _get_conn
        conn = _get_conn()

        ann_ids = None
        if bool(rag_config.get("rag_ann_enabled", True)):
            try:
                from utils.rag_ann_index import get_rag_ann_index

                ann_hits = get_rag_ann_index().search(
                    q_vec,
                    k=max(
                        vector_candidate_k,
                        int(rag_config.get("rag_ann_candidate_k", 200) or 200),
                    ),
                    model_name="BAAI/bge-small-zh-v1.5",
                )
                if ann_hits is not None:
                    trace["ann_used"] = True
                    trace["retrieval_backend"] = "faiss_cpu_hnsw"
                    ann_ids = [fact_id for fact_id, _score in ann_hits]
                    trace["vector_candidates"] = len(ann_ids)
            except Exception:
                logger.debug("RAG ANN query unavailable; using SQLite scan", exc_info=True)

        if ann_ids is None:
            trace["semantic_attempted"] = True
            trace.setdefault("retrieval_backend", "sqlite_exact_vector")

        where = ["embedding IS NOT NULL", "status='active'"]
        params: list = []
        if ann_ids is not None:
            if not ann_ids:
                rows = []
            else:
                placeholders = ",".join("?" for _ in ann_ids)
                where.append(f"id IN ({placeholders})")
                params.extend(ann_ids)
                if category:
                    where.append("category = ?")
                    params.append(category)
                rows = conn.execute(
                    """SELECT id, content, category, source, strength, embedding,
                              created_at, updated_at, source_channel, quality_score,
                              review_status, access_count
                       FROM memory_facts WHERE """ + " AND ".join(where)
                    + " ORDER BY strength DESC",
                    params,
                ).fetchall()
        else:
            if category:
                where.append("category = ?")
                params.append(category)
            rows = conn.execute(
                """SELECT id, content, category, source, strength, embedding,
                          created_at, updated_at, source_channel, quality_score,
                          review_status, access_count
                   FROM memory_facts WHERE """ + " AND ".join(where)
                + " ORDER BY strength DESC",
                params,
            ).fetchall()

        if not rows:
            merged = _merge_hybrid_results(
                query, [], category=category, top_k=top_k, trace=trace
            ) if hybrid else []
            _record_hybrid_access(merged, track_access)
            return merged

        evidence_counts = {
            int(row["fact_id"]): int(row["evidence_count"])
            for row in conn.execute(
                """SELECT fact_id, COUNT(*) AS evidence_count
                   FROM memory_fragments WHERE status='active' GROUP BY fact_id"""
            ).fetchall()
        }

        # 批量计算余弦相似度
        results = []
        for r in rows:
            emb = r["embedding"]
            if emb is None:
                continue
            mem_vec = np.frombuffer(emb, dtype=np.float32)
            sim = float(np.dot(q_vec, mem_vec))
            if sim >= threshold:
                timestamp = r["updated_at"] or r["created_at"] or ""
                recency = _recency_score(timestamp, rag_config)
                strength_score = min(max(int(r["strength"] or 1), 1), 5) / 5.0
                quality_score = min(1.0, max(0.0, float(r["quality_score"] or 0.5)))
                combined = (
                    sim * 0.75 + quality_score * 0.10
                    + recency * 0.10 + strength_score * 0.05
                )
                results.append((combined, {
                    "memory_id": r["id"],
                    "content": r["content"],
                    "category": r["category"],
                    "source": r["source"],
                    "strength": r["strength"],
                    "semantic_similarity": sim,
                    "updated_at": timestamp,
                    "source_channel": r["source_channel"],
                    "evidence_count": evidence_counts.get(int(r["id"]), 0),
                    "quality_score": quality_score,
                    "review_status": r["review_status"] or "normal",
                    "access_count": int(r["access_count"] or 0),
                }))

        results.sort(key=lambda x: x[0], reverse=True)
        # Keep a configurable candidate pool for later ANN parity.  The old
        # top_k*3 truncation discarded useful vector candidates before RRF.
        ranked = results[:vector_candidate_k]
        trace["vector_candidates"] = len(ranked)
        if hybrid:
            ranked = _merge_hybrid_results(
                query, ranked, category=category, top_k=top_k, trace=trace
            )
        else:
            ranked = ranked[:top_k]
            trace["final_results"] = len(ranked)
        try:
            from brain.memory_conflicts import get_fact_relations
            for _score, memory in ranked:
                memory["fact_relations"] = get_fact_relations(memory["memory_id"])
        except Exception:
            pass
        if track_access and ranked:
            try:
                from brain.memory_quality import record_memory_access
                record_memory_access([
                    memory["memory_id"] for _score, memory in ranked
                    if memory.get("memory_id") is not None
                ])
            except Exception:
                pass
        return ranked

    except Exception as e:
        logger.debug(f"RAG search failed: {e}")
        return []


def find_similar_memory(
    content: str,
    category: str = None,
    threshold: float = 0.85,
) -> Optional[tuple[float, dict]]:
    """查找与 content 语义相近的已有记忆（用于去重合并）。
    返回最相似的那条 (相似度, 记忆dict) 或 None。"""
    results = search_similar(
        content, top_k=1, threshold=threshold, category=category,
        track_access=False, hybrid=False, allow_model_load=True,
    )
    return results[0] if results else None


def route_memory_intent(query: str) -> str:
    """Choose retrieval emphasis without changing the model's semantic answer."""
    text = str(query or "").lower()
    if any(marker in text for marker in ("回忆", "记得", "以前", "过去", "聊过", "那时候", "经历")):
        return "long_term"
    if any(marker in text for marker in ("最近", "这段时间", "进展", "总结", "回顾")):
        return "summary"
    if any(marker in text for marker in ("谁", "哪个项目", "关于", "和谁", "关系")):
        return "entity"
    if any(marker in text for marker in ("什么时候", "几点", "哪天", "日期", "多少")):
        return "fact"
    return "general"


def _should_load_semantic_model(
    query: str,
    allow_model_load: Optional[bool] = None,
) -> bool:
    """Decide whether this request justifies loading the local embedding model."""
    if allow_model_load is not None:
        return bool(allow_model_load)
    try:
        from config import get_memory_config
        mode = str(get_memory_config().get("semantic_retrieval_mode", "on_demand")).lower()
    except Exception:
        mode = "on_demand"
    if mode in {"off", "disabled", "false", "0"}:
        return False
    if mode in {"always", "eager"}:
        return True
    # Ordinary chat uses the existing FTS/LIKE path. Explicit memory,
    # timeline, entity, or fact questions opt into semantic retrieval.
    return route_memory_intent(query) != "general"


def _lexical_memory_results(
    query: str,
    category: str | None = None,
    *,
    limit: int = 100,
) -> list[tuple[float, dict]]:
    from brain.graph_memory import _get_conn
    conn = _get_conn()
    text = " ".join(str(query or "").split()).strip()
    from brain.memory_query import recall_subjects
    subjects = recall_subjects(text)
    terms = list(subjects)
    for term in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text):
        terms.append(term)
        if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 2:
            terms.extend(term[index:index + 2] for index in range(len(term) - 1))
    if text and text not in terms:
        terms.append(text)
    if not terms:
        return []
    terms = list(dict.fromkeys(terms))[:16]
    candidates = {}
    # Prefer the indexed channel.  SQLite builds without FTS5 continue below
    # with the same parameterized LIKE fallback used by older installations.
    try:
        match_terms = [term.replace('"', ' ') for term in terms if term.strip()]
        match_query = " OR ".join(f'"{term}"' for term in match_terms)
        fts_sql = """SELECT f.id,f.content,f.category,f.source,f.strength,f.created_at,
                    f.updated_at,f.source_channel,f.quality_score,f.review_status,f.access_count,
                    bm25(memory_facts_fts) AS rank
             FROM memory_facts_fts JOIN memory_facts f ON f.id=memory_facts_fts.fact_id
             WHERE memory_facts_fts MATCH ? AND f.status='active'"""
        fts_params: list = [match_query]
        if category:
            fts_sql += " AND f.category=?"
            fts_params.append(category)
        fts_params.append(max(1, int(limit)))
        fts_rows = conn.execute(fts_sql + " ORDER BY rank LIMIT ?", fts_params).fetchall()
        for row in fts_rows:
            candidates[int(row["id"])] = {**dict(row), "keyword_channel": "fts5"}
    except Exception as exc:
        logger.debug("FTS5 memory search unavailable, using LIKE fallback: %s", exc)

    where = ["status='active'"]
    params: list[str] = []
    clauses = []
    for term in terms:
        clauses.append("content LIKE ?")
        params.append(f"%{term}%")
    where.append("(" + " OR ".join(clauses) + ")")
    if category:
        where.append("category=?")
        params.append(category)
    ordering = "updated_at DESC"
    if subjects:
        ordering = "CASE WHEN " + " OR ".join("content LIKE ?" for subject in subjects) + " THEN 0 ELSE 1 END, " + ordering
        params.extend(f"%{subject}%" for subject in subjects)
    rows = conn.execute(
        """SELECT id,content,category,source,strength,created_at,updated_at,
                  source_channel,quality_score,review_status,access_count
           FROM memory_facts WHERE """ + " AND ".join(where) + " ORDER BY " + ordering + " LIMIT ?",
        params + [max(1, int(limit))],
    ).fetchall()
    for row in rows:
        candidates.setdefault(int(row["id"]), {**dict(row), "keyword_channel": "like"})
    ranked = sorted(candidates.values(), key=lambda item: sum(
        subject.casefold() in item["content"].casefold() for subject in subjects
    ), reverse=True)
    output = []
    for index, item in enumerate(ranked[:max(1, int(limit))]):
        item.update({"memory_id": int(item["id"]), "semantic_similarity": 0.0,
                     "evidence_count": 0, "source_table": "memory_facts"})
        output.append((1.0 / (index + 1), item))
    return output


def _merge_hybrid_results(query: str, vector_results: list[tuple[float, dict]],
                          *, category: str | None, top_k: int,
                          keyword_limit: int | None = None,
                          rrf_k: int | None = None,
                          trace: dict | None = None) -> list[tuple[float, dict]]:
    """RRF merge with an intent-specific narrative boost."""
    from brain.memory_narrative import get_entity_context, get_narrative_context

    rag_config = _get_rag_config()
    if keyword_limit is None:
        keyword_limit = int(rag_config.get("rag_keyword_candidate_k", 100) or 100)
    if rrf_k is None:
        rrf_k = int(rag_config.get("rag_rrf_k", 60) or 60)
    rrf_k = max(1, rrf_k)
    intent = route_memory_intent(query)
    keyword_results = _lexical_memory_results(query, category, limit=keyword_limit)
    if trace is not None:
        trace["keyword_candidates"] = len(keyword_results)
    channel_specs: list[tuple[str, float, list[tuple[float, dict]]]] = [
        ("vector", 1.15 if intent in {"general", "long_term"} else 1.0, vector_results),
        ("keyword", 1.25 if intent == "fact" else 1.0, keyword_results),
    ]
    narratives = get_narrative_context(query, limit=max(4, top_k * 2))
    entities = get_entity_context(query, limit=max(4, top_k * 2))
    if intent in {"long_term", "summary", "entity"}:
        channel_specs.append(("episode", 1.35 if intent in {"long_term", "summary"} else 1.0,
                              [(item.get("narrative_score", 0.0), item) for item in narratives]))
    if intent == "entity":
        channel_specs.append(("entity", 1.45, [(item.get("narrative_score", 0.0), item) for item in entities]))
    ranks: dict[str, float] = {}
    items: dict[str, dict] = {}
    channel_ranks: dict[str, dict[str, int]] = {}
    channel_names: dict[str, set[str]] = {}
    for _channel_name, channel_weight, channel in channel_specs:
        for rank, (_score, item) in enumerate(channel, start=1):
            key = ("episode:" + str(item["id"])) if item.get("source_table") == "memory_episodes" else ("fact:" + str(item.get("memory_id", item.get("id"))))
            if item.get("source_table") == "memory_entity_profiles":
                key = "entity:" + str(item["id"])
            ranks[key] = ranks.get(key, 0.0) + channel_weight / (rrf_k + rank)
            items[key] = item
            channel_ranks.setdefault(key, {})[_channel_name] = rank
            channel_names.setdefault(key, set()).add(_channel_name)
    candidate_limit = max(
        int(top_k),
        int(rag_config.get("rag_mmr_candidate_k", 20) or 20)
        if bool(rag_config.get("rag_mmr_enabled", True)) else int(top_k),
    )
    merged = sorted(ranks.items(), key=lambda pair: pair[1], reverse=True)[:candidate_limit]
    output = []
    for key, score in merged:
        item = dict(items[key])
        item.update({
            "retrieval_intent": intent,
            "rrf_score": round(score, 6),
            "retrieval_channels": sorted(channel_names.get(key, set())),
            "retrieval_ranks": dict(channel_ranks.get(key, {})),
        })
        output.append((score, item))
    output = _apply_mmr(output, max(1, int(top_k)), rag_config)
    if trace is not None:
        trace["final_results"] = len(output)
    return output


def _record_hybrid_access(results: list[tuple[float, dict]], enabled: bool) -> None:
    if not enabled or not results:
        return
    try:
        from brain.memory_quality import record_memory_access
        record_memory_access([
            item["memory_id"] for _score, item in results
            if item.get("memory_id") is not None
        ])
    except Exception:
        pass


def format_rag_context(memories: list[tuple[float, dict]]) -> str:
    """将检索到的记忆格式化为 LLM 可读的提示文本。"""
    if not memories:
        return ""
    lines = ["【你可能记得的相关信息】"]
    for sim, mem in memories:
        if mem.get("source_table") == "memory_episodes":
            lines.append(
                f"· [叙事#{mem.get('id', '?')}] {mem.get('title', '相关经历')}：{mem.get('summary', '')} "
                f"(融合相关度:{float(mem.get('rrf_score', sim)):.1%}, 来源:叙事记忆)"
            )
            continue
        if mem.get("source_table") == "memory_entity_profiles":
            lines.append(
                f"· [实体#{mem.get('id', '?')}] {mem.get('name', '')}：{mem.get('summary', '')} "
                f"当前状态：{mem.get('current_status', '')} "
                f"(融合相关度:{float(mem.get('rrf_score', sim)):.1%})"
            )
            continue
        source = mem.get("source_channel") or mem.get("source") or "unknown"
        updated = mem.get("updated_at") or "时间未知"
        semantic = mem.get("semantic_similarity", sim)
        relevance = f"语义相关:{semantic:.0%}"
        if mem.get("keyword_channel") and "vector" not in mem.get("retrieval_channels", []):
            relevance = "关键词命中，未计算向量相似度"
        lines.append(
            f"· [记忆#{mem.get('memory_id', '?')}] {mem['content']} "
            f"({relevance}, 更新:{updated}, 来源:{source}, "
            f"证据:{mem.get('evidence_count', 0)}条, "
            f"质量:{float(mem.get('quality_score', 0.5)):.0%})"
        )
        for relation in mem.get("fact_relations", []):
            if relation.get("relation") != "contradicts":
                continue
            memory_id = int(mem.get("memory_id", 0) or 0)
            if int(relation.get("source_fact_id", 0)) == memory_id:
                other_id = relation.get("target_fact_id")
                other_content = relation.get("target_content", "")
            else:
                other_id = relation.get("source_fact_id")
                other_content = relation.get("source_content", "")
            lines.append(
                f"  ⚠ 与记忆#{other_id}存在已识别的语义矛盾：{other_content}。"
                "不得擅自选择其中一条，应保留不确定性或向用户确认。"
            )
    return "\n".join(lines)


_reindex_lock = threading.Lock()


def reindex_pending_facts(*, max_items: int = 20) -> dict:
    """Index one bounded batch of pending facts in the caller's worker thread."""
    max_items = max(1, int(max_items))
    if not _reindex_lock.acquire(blocking=False):
        return {"status": "busy", "indexed": 0, "remaining": 0}
    try:
        from brain.graph_memory import _get_conn

        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, content FROM memory_facts
               WHERE embedding IS NULL AND status='active'
               ORDER BY updated_at ASC, id ASC LIMIT ?""",
            (max_items,),
        ).fetchall()
        if not rows:
            return {"status": "idle", "indexed": 0, "remaining": 0}

        from utils.embedding_cache import MODEL_NAME, get_many, put_many
        from utils.embedding_cache import content_hash

        contents = [str(row["content"] or "") for row in rows]
        cached = get_many(contents, model_name=MODEL_NAME, dimension=512)
        missing_indexes = [index for index, text in enumerate(contents)
                           if text not in cached]
        vectors_by_index = {
            index: np.frombuffer(cached[text], dtype=np.float32)
            for index, text in enumerate(contents) if text in cached
        }
        generated = 0
        if missing_indexes:
            model = _get_model()
            if model is None:
                return {"status": "unavailable", "indexed": 0,
                        "cached": len(cached), "generated": 0,
                        "remaining": len(rows)}
            logger.info("Indexing %s pending memory embeddings (%s cache hits)",
                        len(rows), len(cached))
            generated_vecs = model.encode(
                [contents[index] for index in missing_indexes],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            generated_blobs = []
            for index, vec in zip(missing_indexes, generated_vecs):
                array = np.asarray(vec, dtype=np.float32)
                blob = array.tobytes()
                vectors_by_index[index] = array
                generated_blobs.append((contents[index], blob))
            if generated_blobs:
                put_many(generated_blobs, model_name=MODEL_NAME,
                         dimension=int(generated_vecs.shape[1] if generated_vecs.ndim > 1
                                       else generated_vecs.shape[0]))
                generated = len(generated_blobs)
        if not vectors_by_index:
            return {"status": "unavailable", "indexed": 0,
                    "cached": len(cached), "generated": generated,
                    "remaining": len(rows)}
        indexed = 0
        for index, row in enumerate(rows):
            vec = vectors_by_index.get(index)
            if vec is None:
                continue
            cur = conn.execute(
                """UPDATE memory_facts SET embedding=?,
                   embedding_model='BAAI/bge-small-zh-v1.5', embedding_version=1,
                   embedding_content_hash=?
                   WHERE id=? AND embedding IS NULL""",
                (vec.astype(np.float32).tobytes(),
                 content_hash(row["content"]),
                 row["id"]),
            )
            indexed += cur.rowcount
        conn.commit()
        try:
            from utils.rag_ann_index import get_rag_ann_index
            get_rag_ann_index().add_rows(
                [(rows[index]["id"], vec.astype(np.float32).tobytes())
                 for index, vec in vectors_by_index.items()],
                model_name=MODEL_NAME,
            )
        except Exception:
            logger.debug("Pending embeddings were saved; ANN update deferred", exc_info=True)
        remaining = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE embedding IS NULL AND status='active'"
        ).fetchone()[0]
        return {"status": "success", "indexed": indexed,
                "cached": len(cached), "generated": generated,
                "remaining": int(remaining)}
    except Exception:
        logger.exception("Pending memory reindex failed")
        raise
    finally:
        _reindex_lock.release()


def reindex_all_facts():
    """Compatibility helper: index all currently pending facts in a worker."""
    def _run():
        while True:
            result = reindex_pending_facts(max_items=100)
            if result.get("status") != "success" or not result.get("remaining"):
                return

    threading.Thread(target=_run, daemon=True).start()


def warmup():
    """后台预热 embedding 模型（Windows 默认按需加载）。"""
    if _os.name == "nt" and _os.environ.get("LIANXIN_ENABLE_BACKGROUND_MODEL_WARMUP") != "1":
        logger.info("Windows 已跳过 RAG 后台预热，将在首次语义检索时加载")
        return
    def _load():
        _get_model()
        if _model is not None:
            logger.info("RAG embedding model warmed up successfully")
        else:
            logger.warning("RAG embedding model warmup failed or unavailable")
    t = threading.Thread(target=_load, daemon=True)
    t.start()
