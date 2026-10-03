"""
五元组知识图谱记忆存储 — SQLite 图结构（零额外依赖）。

与现有的 6 分类 JSON 记忆并行运作：
- JSON 记忆：独立事实/偏好
- 图记忆：实体间的关系（主体, 主体类型, 关系, 客体, 客体类型）

两张表存在于 memory/conversations.db 中。
"""

import hashlib
import json
import sqlite3
import threading
import logging
from pathlib import Path

from memory.sqlite_coordination import connect_database

_DB_PATH = Path(__file__).parent.parent / "memory" / "conversations.db"
_local = threading.local()

ENTITY_TYPES = ("人物", "地点", "组织", "物品", "概念", "时间", "事件", "活动", "技术", "文件")


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        # Most graph-memory callers perform short independent mutations.  Use
        # autocommit so a later Python exception cannot strand a write
        # transaction on this long-lived thread-local connection.  Callers
        # that need atomic multi-step work can still issue BEGIN explicitly.
        conn = connect_database(
            _DB_PATH, check_same_thread=False, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_tables(conn)
        _local.conn = conn
    return _local.conn


def _init_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS graph_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(name, entity_type)
        );
        CREATE TABLE IF NOT EXISTS graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            head_id INTEGER NOT NULL,
            head_type TEXT NOT NULL,
            relation TEXT NOT NULL,
            tail_id INTEGER NOT NULL,
            tail_type TEXT NOT NULL,
            source TEXT DEFAULT 'auto',
            strength INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(head_id) REFERENCES graph_entities(id),
            FOREIGN KEY(tail_id) REFERENCES graph_entities(id),
            UNIQUE(head_id, relation, tail_id)
        );
        CREATE TABLE IF NOT EXISTS memory_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'knowledge',
            source TEXT NOT NULL DEFAULT 'user_saved',
            strength INTEGER DEFAULT 1,
            last_accessed_at TEXT DEFAULT '',
            access_count INTEGER NOT NULL DEFAULT 0,
            quality_score REAL NOT NULL DEFAULT 0.5,
            review_status TEXT NOT NULL DEFAULT 'normal',
            quality_updated_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(content, category)
        );
        CREATE TABLE IF NOT EXISTS memory_fragments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'knowledge',
            source TEXT NOT NULL DEFAULT 'auto_extracted',
            source_session_id INTEGER,
            source_channel TEXT DEFAULT '',
            source_message_ids TEXT NOT NULL DEFAULT '[]',
            persona_id TEXT DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.5,
            extraction_model TEXT DEFAULT '',
            occurred_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_graph_entities_name ON graph_entities(name);
        CREATE INDEX IF NOT EXISTS idx_graph_entities_type ON graph_entities(entity_type);
        CREATE INDEX IF NOT EXISTS idx_graph_edges_head ON graph_edges(head_id);
        CREATE INDEX IF NOT EXISTS idx_graph_edges_tail ON graph_edges(tail_id);
        CREATE INDEX IF NOT EXISTS idx_graph_edges_relation ON graph_edges(relation);
        CREATE INDEX IF NOT EXISTS idx_memory_facts_category ON memory_facts(category);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_fact ON memory_fragments(fact_id);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_session ON memory_fragments(source_session_id);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_status ON memory_fragments(status);
    """)
    # 为 RAG 向量检索添加 embedding 列（已存在的表不影响）
    try:
        conn.execute("ALTER TABLE memory_facts ADD COLUMN embedding BLOB")
    except Exception:
        pass  # 列已存在
    for sql in (
        "ALTER TABLE memory_facts ADD COLUMN updated_at TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN occurred_at TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN source_session_id INTEGER",
        "ALTER TABLE memory_facts ADD COLUMN source_channel TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN embedding_model TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN embedding_version INTEGER DEFAULT 1",
        "ALTER TABLE memory_facts ADD COLUMN embedding_content_hash TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE memory_facts ADD COLUMN valid_from TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN valid_to TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN last_accessed_at TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_facts ADD COLUMN quality_score REAL NOT NULL DEFAULT 0.5",
        "ALTER TABLE memory_facts ADD COLUMN review_status TEXT NOT NULL DEFAULT 'normal'",
        "ALTER TABLE memory_facts ADD COLUMN quality_updated_at TEXT DEFAULT ''",
        "ALTER TABLE memory_facts ADD COLUMN lifecycle_stage TEXT DEFAULT 'maturing'",
        "ALTER TABLE memory_facts ADD COLUMN emotional_weight REAL NOT NULL DEFAULT 0.5",
        "ALTER TABLE graph_edges ADD COLUMN updated_at TEXT DEFAULT ''",
        "ALTER TABLE graph_edges ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE graph_edges ADD COLUMN source_session_id INTEGER",
        "ALTER TABLE graph_edges ADD COLUMN source_channel TEXT DEFAULT ''",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """UPDATE memory_facts SET updated_at=created_at
           WHERE updated_at IS NULL OR updated_at=''"""
    )
    conn.execute(
        """UPDATE graph_edges SET updated_at=created_at
           WHERE updated_at IS NULL OR updated_at=''"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_status ON memory_facts(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_quality ON memory_facts(status, quality_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_review ON memory_facts(review_status)")
    # FTS5 is optional in some SQLite builds.  The RAG layer detects its absence
    # and falls back to parameterized LIKE queries without changing facts.
    try:
        conn.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts USING fts5(
                   content, category UNINDEXED, fact_id UNINDEXED,
                   tokenize='unicode61')"""
        )
        conn.executescript("""
        DROP TRIGGER IF EXISTS memory_facts_fts_insert;
        DROP TRIGGER IF EXISTS memory_facts_fts_update;
        DROP TRIGGER IF EXISTS memory_facts_fts_delete;
        CREATE TRIGGER IF NOT EXISTS memory_facts_fts_insert AFTER INSERT ON memory_facts BEGIN
            INSERT INTO memory_facts_fts(rowid, content, category, fact_id)
            VALUES (new.id, new.content, new.category, new.id);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_facts_fts_update AFTER UPDATE OF content, category ON memory_facts BEGIN
            DELETE FROM memory_facts_fts WHERE rowid=old.id;
            INSERT INTO memory_facts_fts(rowid, content, category, fact_id)
            VALUES (new.id, new.content, new.category, new.id);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_facts_fts_delete AFTER DELETE ON memory_facts BEGIN
            DELETE FROM memory_facts_fts WHERE rowid=old.id;
        END;
        """)
        indexed = conn.execute("SELECT COUNT(*) FROM memory_facts_fts").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0]
        if indexed != total:
            conn.execute("INSERT INTO memory_facts_fts(memory_facts_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _get_or_create_entity(conn: sqlite3.Connection, name: str, entity_type: str) -> int:
    name = name.strip()
    entity_type = entity_type.strip()
    row = conn.execute(
        "SELECT id FROM graph_entities WHERE name=? AND entity_type=?",
        (name, entity_type)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO graph_entities(name, entity_type) VALUES (?, ?)",
        (name, entity_type)
    )
    return cur.lastrowid


def store_quintuple(head: str, head_type: str, relation: str,
                    tail: str, tail_type: str, source: str = "auto",
                    source_session_id: int | None = None,
                    source_channel: str = "") -> bool:
    """写入一条五元组记忆。实体自动 merge，关系重复则 strength+1。返回是否新增。"""
    head = head.strip()
    tail = tail.strip()
    relation = relation.strip()
    if not head or not tail or not relation:
        return False
    if head_type not in ENTITY_TYPES:
        head_type = "概念"
    if tail_type not in ENTITY_TYPES:
        tail_type = "概念"

    conn = _get_conn()
    hid = _get_or_create_entity(conn, head, head_type)
    tid = _get_or_create_entity(conn, tail, tail_type)

    cur = conn.execute(
        """INSERT INTO graph_edges(head_id, head_type, relation, tail_id, tail_type,
                                   source, source_session_id, source_channel)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(head_id, relation, tail_id) DO UPDATE SET
               strength = CASE WHEN excluded.source='auto' THEN strength ELSE strength + 1 END,
               source_session_id=COALESCE(excluded.source_session_id, source_session_id),
               source_channel=CASE WHEN excluded.source_channel<>'' THEN excluded.source_channel ELSE source_channel END,
               updated_at = datetime('now','localtime')""",
        (hid, head_type, relation, tid, tail_type, source,
         source_session_id, str(source_channel or ""))
    )
    conn.commit()
    return cur.lastrowid is not None and cur.rowcount > 0

def add_graph_edge(head: str, head_type: str, relation: str,
                   tail: str, tail_type: str) -> str:
    """手动添加一条五元组边（供 LLM 工具调用），自动去重。返回操作结果文本。"""
    head = head.strip()
    tail = tail.strip()
    relation = relation.strip()
    if not head or not tail or not relation:
        return "错误：实体名和关系名不能为空。"
    if head_type not in ENTITY_TYPES:
        head_type = "概念"
    if tail_type not in ENTITY_TYPES:
        tail_type = "概念"

    conn = _get_conn()
    hid = _get_or_create_entity(conn, head, head_type)
    tid = _get_or_create_entity(conn, tail, tail_type)

    existing = conn.execute(
        "SELECT strength FROM graph_edges WHERE head_id=? AND relation=? AND tail_id=?",
        (hid, relation, tid)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE graph_edges SET strength=strength+1 WHERE head_id=? AND relation=? AND tail_id=?",
            (hid, relation, tid)
        )
        conn.commit()
        return f"已强化：{head}({head_type}) —[{relation}]→ {tail}({tail_type})，当前强度 {existing['strength']+1}"

    conn.execute(
        """INSERT INTO graph_edges(head_id, head_type, relation, tail_id, tail_type, source)
           VALUES (?, ?, ?, ?, ?, 'manual')""",
        (hid, head_type, relation, tid, tail_type)
    )
    conn.commit()
    return f"已添加：{head}({head_type}) —[{relation}]→ {tail}({tail_type})"


def remove_graph_edge(head: str, relation: str, tail: str) -> str:
    """删除一条指定的五元组边。返回操作结果文本。"""
    head = head.strip()
    tail = tail.strip()
    relation = relation.strip()
    if not head or not tail or not relation:
        return "错误：实体名和关系名不能为空。"

    conn = _get_conn()
    hid_row = conn.execute("SELECT id FROM graph_entities WHERE name=?", (head,)).fetchone()
    tid_row = conn.execute("SELECT id FROM graph_entities WHERE name=?", (tail,)).fetchone()
    if not hid_row:
        return f"错误：实体「{head}」不存在于图记忆中。"
    if not tid_row:
        return f"错误：实体「{tail}」不存在于图记忆中。"

    cur = conn.execute(
        "DELETE FROM graph_edges WHERE head_id=? AND relation=? AND tail_id=?",
        (hid_row["id"], relation, tid_row["id"])
    )
    conn.commit()
    if cur.rowcount == 0:
        return f"未找到匹配的边：{head} —[{relation}]→ {tail}"
    return f"已删除：{head} —[{relation}]→ {tail}"


def query_by_entity(keyword: str, entity_type: str = None) -> list[dict]:
    """按实体名模糊搜索，返回该实体的所有关联边。"""
    conn = _get_conn()
    q = "%" + keyword.strip() + "%"
    if entity_type:
        rows = conn.execute("""
            SELECT h.name AS head, e.head_type, e.relation,
                   t.name AS tail, e.tail_type, e.source, e.strength
            FROM graph_edges e
            JOIN graph_entities h ON e.head_id = h.id
            JOIN graph_entities t ON e.tail_id = t.id
            WHERE (h.name LIKE ? OR t.name LIKE ?) AND (h.entity_type=? OR t.entity_type=?)
            ORDER BY e.strength DESC
            LIMIT 30
        """, (q, q, entity_type, entity_type)).fetchall()
    else:
        rows = conn.execute("""
            SELECT h.name AS head, e.head_type, e.relation,
                   t.name AS tail, e.tail_type, e.source, e.strength
            FROM graph_edges e
            JOIN graph_entities h ON e.head_id = h.id
            JOIN graph_entities t ON e.tail_id = t.id
            WHERE h.name LIKE ? OR t.name LIKE ?
            ORDER BY e.strength DESC
            LIMIT 30
        """, (q, q)).fetchall()
    result = [dict(r) for r in rows]
    if result:
        try:
            from brain.memory_quality import record_memory_access
            record_memory_access([item["id"] for item in result])
        except Exception:
            pass
    return result


def query_by_relation(relation_keyword: str, head_type: str = None,
                     tail_type: str = None) -> list[dict]:
    """按关系关键词搜索，可选过滤头尾实体类型。"""
    conn = _get_conn()
    q = "%" + relation_keyword.strip() + "%"
    sql = """
        SELECT h.name AS head, e.head_type, e.relation,
               t.name AS tail, e.tail_type, e.source, e.strength
        FROM graph_edges e
        JOIN graph_entities h ON e.head_id = h.id
        JOIN graph_entities t ON e.tail_id = t.id
        WHERE e.relation LIKE ?
    """
    params = [q]
    if head_type:
        sql += " AND e.head_type = ?"
        params.append(head_type)
    if tail_type:
        sql += " AND e.tail_type = ?"
        params.append(tail_type)
    sql += " ORDER BY e.strength DESC LIMIT 30"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_connected(entity_name: str, depth: int = 1) -> list[dict]:
    """BFS 多跳遍历，找到与指定实体间接关联的实体和路径。"""
    if depth > 3:
        depth = 3
    conn = _get_conn()
    start_rows = conn.execute(
        "SELECT id, name, entity_type FROM graph_entities WHERE name LIKE ? LIMIT 3",
        ("%" + entity_name.strip() + "%",)
    ).fetchall()
    if not start_rows:
        return []

    results = []
    visited = set()

    for start in start_rows:
        current_ids = {start["id"]}
        for hop in range(depth):
            if not current_ids:
                break
            placeholders = ",".join("?" * len(current_ids))
            ids_list = list(current_ids)
            rows = conn.execute(f"""
                SELECT h.name AS head, e.head_type, e.relation,
                       t.name AS tail, e.tail_type, e.strength
                FROM graph_edges e
                JOIN graph_entities h ON e.head_id = h.id
                JOIN graph_entities t ON e.tail_id = t.id
                WHERE e.head_id IN ({placeholders}) OR e.tail_id IN ({placeholders})
                ORDER BY e.strength DESC LIMIT 30
            """, ids_list + ids_list).fetchall()

            new_ids = set()
            for r in rows:
                key = (r["head"], r["relation"], r["tail"])
                if key not in visited:
                    visited.add(key)
                    results.append(dict(r))
                hid = conn.execute(
                    "SELECT id FROM graph_entities WHERE name=?", (r["head"],)
                ).fetchone()
                tid = conn.execute(
                    "SELECT id FROM graph_entities WHERE name=?", (r["tail"],)
                ).fetchone()
                if hid:
                    new_ids.add(hid["id"])
                if tid:
                    new_ids.add(tid["id"])
            current_ids = new_ids - current_ids  # 只探索新发现的实体

    return results


def discover_from_entity(entity_name: str, depth: int = 2) -> dict:
    """从指定实体出发，BFS 遍历图并返回结构化发现摘要。

    返回: {
        "entity": "用户",
        "direct_relations": [...],    # 直接关联的边
        "indirect_relations": [...],  # 间接关联的边
        "entities_discovered": [...], # 发现的实体列表
        "relation_groups": {...},     # 按关系类型分组
        "summary": "..."              # 自然语言摘要
    }
    """
    if depth > 3:
        depth = 3
    conn = _get_conn()

    start_rows = conn.execute(
        "SELECT id, name, entity_type FROM graph_entities WHERE name LIKE ? LIMIT 3",
        ("%" + entity_name.strip() + "%",)
    ).fetchall()
    if not start_rows:
        return {"entity": entity_name, "direct_relations": [], "indirect_relations": [],
                "entities_discovered": [], "relation_groups": {}, "summary": "未找到该实体。"}

    visited_edges = set()
    visited_entities = set()
    direct_relations = []
    indirect_relations = []
    entities_discovered = []

    for start in start_rows:
        visited_entities.add(start["name"])
        current_ids = {start["id"]}

        for hop in range(depth):
            if not current_ids:
                break
            placeholders = ",".join("?" * len(current_ids))
            ids_list = list(current_ids)
            rows = conn.execute(f"""
                SELECT h.name AS head, e.head_type, e.relation,
                       t.name AS tail, e.tail_type, e.strength
                FROM graph_edges e
                JOIN graph_entities h ON e.head_id = h.id
                JOIN graph_entities t ON e.tail_id = t.id
                WHERE e.head_id IN ({placeholders}) OR e.tail_id IN ({placeholders})
                ORDER BY e.strength DESC LIMIT 30
            """, ids_list + ids_list).fetchall()

            new_ids = set()
            for r in rows:
                key = (r["head"], r["relation"], r["tail"])
                if key not in visited_edges:
                    visited_edges.add(key)
                    d = dict(r)
                    if hop == 0:
                        direct_relations.append(d)
                    else:
                        indirect_relations.append(d)
                for name in (r["head"], r["tail"]):
                    if name not in visited_entities:
                        visited_entities.add(name)
                        entities_discovered.append(name)
                hid = conn.execute(
                    "SELECT id FROM graph_entities WHERE name=?", (r["head"],)
                ).fetchone()
                tid = conn.execute(
                    "SELECT id FROM graph_entities WHERE name=?", (r["tail"],)
                ).fetchone()
                if hid:
                    new_ids.add(hid["id"])
                if tid:
                    new_ids.add(tid["id"])
            current_ids = new_ids - current_ids

    # 按关系类型分组
    relation_groups = {}
    for r in direct_relations + indirect_relations:
        rel = r["relation"]
        if rel not in relation_groups:
            relation_groups[rel] = []
        relation_groups[rel].append(r)

    # 生成自然语言摘要
    summary_lines = [f"「{entity_name}」的图谱发现："]
    if direct_relations:
        summary_lines.append(f"  直接关联 {len(direct_relations)} 条关系：")
        for r in direct_relations[:8]:
            summary_lines.append(
                f"    · {r['head']} —[{r['relation']}]→ {r['tail']} [强度:{r['strength']}]"
            )
    if indirect_relations:
        summary_lines.append(f"  间接关联 {len(indirect_relations)} 条关系：")
        for r in indirect_relations[:5]:
            summary_lines.append(
                f"    · {r['head']} —[{r['relation']}]→ {r['tail']} [强度:{r['strength']}]"
            )
    if entities_discovered:
        unique_entities = list(set(entities_discovered))[:10]
        summary_lines.append(f"  发现的实体: {', '.join(unique_entities)}")

    return {
        "entity": entity_name,
        "direct_relations": direct_relations,
        "indirect_relations": indirect_relations,
        "entities_discovered": list(set(entities_discovered)),
        "relation_groups": relation_groups,
        "summary": "\n".join(summary_lines),
    }


def get_graph_summary_for_user(depth: int = 2) -> str:
    """获取「用户」实体的图谱发现摘要，用于自动注入 system prompt。
    返回空字符串表示无可用图谱。"""
    from config import get_graph_config
    cfg = get_graph_config()
    if not cfg.get("graph_enabled", True):
        return ""

    discovery = discover_from_entity("用户", depth=depth)
    if not discovery["direct_relations"] and not discovery["indirect_relations"]:
        return ""

    # 精简 version：只返回直接关系 + 关键实体
    lines = ["【图谱发现】"]
    for r in discovery["direct_relations"][:6]:
        lines.append(f"  {r['head']} —[{r['relation']}]→ {r['tail']}")
    if discovery["entities_discovered"]:
        unique = [e for e in discovery["entities_discovered"] if e != "用户"][:8]
        if unique:
            lines.append(f"  相关: {', '.join(unique)}")
    return "\n".join(lines)


def search_graph(keywords: list[str]) -> list[dict]:
    """综合搜索：匹配实体名 + 关系名。"""
    if isinstance(keywords, str):
        keywords = [keywords]
    if not keywords:
        return []

    conn = _get_conn()
    results = []
    seen = set()

    for kw in keywords:
        q = "%" + kw.strip() + "%"
        rows = conn.execute("""
            SELECT h.name AS head, e.head_type, e.relation,
                   t.name AS tail, e.tail_type, e.source, e.strength
            FROM graph_edges e
            JOIN graph_entities h ON e.head_id = h.id
            JOIN graph_entities t ON e.tail_id = t.id
            WHERE h.name LIKE ? OR t.name LIKE ? OR e.relation LIKE ?
            ORDER BY e.strength DESC LIMIT 10
        """, (q, q, q)).fetchall()

        for r in rows:
            key = (r["head"], r["relation"], r["tail"])
            if key not in seen:
                seen.add(key)
                results.append(dict(r))

    return results[:50]


def search_graph_ranked(keywords: list[str]) -> list[dict]:
    """多关键词相关性排序搜索。每条结果按命中关键词数计分，分数高的排前面。"""
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [kw.strip() for kw in keywords if kw.strip()]
    if not keywords:
        return []

    conn = _get_conn()
    scored: dict[tuple, tuple[dict, int]] = {}

    for kw in keywords:
        q = "%" + kw + "%"
        rows = conn.execute("""
            SELECT h.name AS head, e.head_type, e.relation,
                   t.name AS tail, e.tail_type, e.source, e.strength
            FROM graph_edges e
            JOIN graph_entities h ON e.head_id = h.id
            JOIN graph_entities t ON e.tail_id = t.id
            WHERE h.name LIKE ? OR t.name LIKE ? OR e.relation LIKE ?
            ORDER BY e.strength DESC LIMIT 15
        """, (q, q, q)).fetchall()

        for r in rows:
            key = (r["head"], r["relation"], r["tail"])
            d = dict(r)
            if key in scored:
                scored[key] = (d, scored[key][1] + 1)
            else:
                scored[key] = (d, 1)

    ranked = sorted(scored.values(), key=lambda x: x[1], reverse=True)
    return [item[0] for item in ranked[:30]]


def format_graph_result(rows: list[dict]) -> str:
    """将图查询结果格式化为 LLM 可读文本。"""
    if not rows:
        return "图记忆中未找到相关信息。"

    lines = [f"找到 {len(rows)} 条关联记忆："]
    for i, r in enumerate(rows, 1):
        src = "自动" if r.get("source", "auto") == "auto" else "手动"
        strength = r.get("strength", 1)
        lines.append(
            f"{i}. ({r['head']}, {r['head_type']}) "
            f"—[{r['relation']}]→ "
            f"({r['tail']}, {r['tail_type']}) "
            f"[强度:{strength}, {src}]"
        )
    return "\n".join(lines)


def get_graph_stats() -> dict:
    """获取图统计信息。"""
    conn = _get_conn()
    entity_count = conn.execute("SELECT COUNT(*) FROM graph_entities").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    top_relations = conn.execute("""
        SELECT relation, COUNT(*) AS cnt FROM graph_edges
        GROUP BY relation ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    top_entities = conn.execute("""
        SELECT name, entity_type FROM graph_entities
        ORDER BY id DESC LIMIT 10
    """).fetchall()
    return {
        "entity_count": entity_count,
        "edge_count": edge_count,
        "top_relations": [(r["relation"], r["cnt"]) for r in top_relations],
        "recent_entities": [(r["name"], r["entity_type"]) for r in top_entities],
    }


def delete_entity(name: str) -> int:
    """删除实体及其所有关联边。返回删除的边数。"""
    conn = _get_conn()
    entity_rows = conn.execute(
        "SELECT id FROM graph_entities WHERE name=?",
        (name.strip(),)
    ).fetchall()
    if not entity_rows:
        return 0
    ids = [r["id"] for r in entity_rows]
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"DELETE FROM graph_edges WHERE head_id IN ({placeholders}) OR tail_id IN ({placeholders})",
        ids + ids
    )
    edge_count = cur.rowcount
    conn.execute(f"DELETE FROM graph_entities WHERE id IN ({placeholders})", ids)
    conn.commit()
    return edge_count


# ── 分类事实记忆（替换 long_term.json） ─────────────────
ALL_MEMORY_CATEGORIES = [
    "profile", "preferences", "events", "knowledge", "behaviors", "skills"
]
ALL_CATEGORIES = ALL_MEMORY_CATEGORIES  # 向后兼容别名

CATEGORY_DESCRIPTIONS = {
    "profile": "个人档案：用户的姓名、年龄、外貌、性格、职业、背景故事等长期稳定的个人信息",
    "preferences": "偏好：用户喜欢的音乐、游戏、食物、颜色、动漫、风格等喜好信息",
    "events": "事件：用户过去经历的事、旅行、比赛、重要日期、未来的计划等",
    "knowledge": "知识：路径配置、工作原理、规则、使用方法等客观事实",
    "behaviors": "行为模式：沟通风格偏好、习惯、互动方式、期望的回应方式等",
    "skills": "技能：莲心学会的能力、工具使用经验、新掌握的领域知识",
}



def add_fact(content: str, category: str = "knowledge",
             source: str = "user_saved", source_session_id: int | None = None,
             source_channel: str = "", occurred_at: str = "",
             *, embed_immediately: bool = False) -> int:
    """Insert a fact; only exact matches are strengthened automatically.

    Semantic similarity is never treated as proof of duplication.  Related facts
    are recorded as conflict candidates for a model to review.
    """
    content = content.strip()
    if not content:
        return 0
    if category not in ALL_MEMORY_CATEGORIES:
        category = "knowledge"

    conn = _get_conn()
    exact_before = conn.execute(
        "SELECT id FROM memory_facts WHERE content=? AND category=?",
        (content, category),
    ).fetchone()

    # Semantic indexing is intentionally decoupled from the write path.  The
    # row remains available to lexical retrieval until the idle index duty runs.
    emb_bytes = None
    if embed_immediately:
        try:
            from brain.memory_rag import embed_bytes
            emb_bytes = embed_bytes(content)
        except Exception:
            pass

    if emb_bytes:
        from utils.embedding_cache import content_hash
        conn.execute(
            """INSERT INTO memory_facts
               (content, category, source, embedding, embedding_model, embedding_content_hash, updated_at,
                occurred_at, source_session_id, source_channel, status)
               VALUES (?, ?, ?, ?, 'BAAI/bge-small-zh-v1.5', ?, datetime('now','localtime'), ?, ?, ?, 'active')
               ON CONFLICT(content, category) DO UPDATE SET
               strength = strength + CASE WHEN excluded.source='auto_extracted' THEN 0 ELSE 1 END,
               source = excluded.source, embedding=excluded.embedding,
               embedding_model=excluded.embedding_model,
               embedding_content_hash=excluded.embedding_content_hash,
               updated_at=datetime('now','localtime'), status='active'""",
            (content, category, source, emb_bytes, content_hash(content),
             occurred_at, source_session_id, source_channel)
        )
    else:
        conn.execute(
            """INSERT INTO memory_facts
               (content, category, source, updated_at, occurred_at,
                source_session_id, source_channel, status)
               VALUES (?, ?, ?, datetime('now','localtime'), ?, ?, ?, 'active')
               ON CONFLICT(content, category) DO UPDATE SET
               strength = strength + CASE WHEN excluded.source='auto_extracted' THEN 0 ELSE 1 END,
               source = excluded.source, updated_at=datetime('now','localtime'), status='active'""",
            (content, category, source, occurred_at, source_session_id, source_channel)
        )
    conn.commit()

    _prune_category(conn, category)

    row = conn.execute(
        "SELECT id FROM memory_facts WHERE content=? AND category=?",
        (content, category)
    ).fetchone()
    fact_id = row["id"] if row else 0
    if fact_id and exact_before is None:
        try:
            from brain.memory_conflicts import record_conflict_candidates
            record_conflict_candidates(fact_id)
        except Exception as exc:
            logging.getLogger("MemoryConflict").debug(
                "Conflict candidate discovery skipped: %s", exc
            )
    return fact_id


def add_memory_fragment(
    fact_id: int,
    content: str,
    category: str,
    *,
    source: str = "auto_extracted",
    source_session_id: int | None = None,
    source_channel: str = "",
    source_message_ids: list[int] | None = None,
    persona_id: str = "",
    confidence: float = 0.5,
    extraction_model: str = "",
    occurred_at: str = "",
    commit: bool = True,
) -> int:
    """Store immutable evidence for a normalized fact and return its fragment id."""
    content = " ".join(str(content or "").split()).strip()
    if not fact_id or not content:
        return 0
    if category not in ALL_MEMORY_CATEGORIES:
        category = "knowledge"

    message_ids = []
    for value in source_message_ids or []:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id > 0 and message_id not in message_ids:
            message_ids.append(message_id)
    message_ids_json = json.dumps(message_ids, ensure_ascii=False)
    fingerprint_payload = json.dumps({
        "fact_id": int(fact_id),
        "content": content.casefold(),
        "session_id": source_session_id,
        "message_ids": message_ids,
        "source": source,
    }, sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
    try:
        confidence_value = min(1.0, max(0.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 0.5

    conn = _get_conn()
    fact_exists = conn.execute(
        "SELECT 1 FROM memory_facts WHERE id=?", (int(fact_id),)
    ).fetchone()
    if not fact_exists:
        return 0
    conn.execute(
        """INSERT INTO memory_fragments (
               fact_id, content, category, source, source_session_id,
               source_channel, source_message_ids, persona_id, confidence,
               extraction_model, occurred_at, status, fingerprint
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
           ON CONFLICT(fingerprint) DO UPDATE SET
               confidence=MAX(confidence, excluded.confidence),
               updated_at=datetime('now','localtime')""",
        (
            int(fact_id), content, category, str(source or "auto_extracted"),
            source_session_id, str(source_channel or ""), message_ids_json,
            str(persona_id or ""), confidence_value,
            str(extraction_model or ""), str(occurred_at or ""), fingerprint,
        ),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "SELECT id FROM memory_fragments WHERE fingerprint=?", (fingerprint,)
    ).fetchone()
    return int(row["id"]) if row else 0


def get_fact_fragments(fact_id: int, *, include_inactive: bool = False,
                       limit: int = 20) -> list[dict]:
    """Return evidence fragments for a fact, newest first."""
    sql = """SELECT id, fact_id, content, category, source, source_session_id,
                    source_channel, source_message_ids, persona_id, confidence,
                    extraction_model, occurred_at, status, created_at, updated_at
             FROM memory_fragments WHERE fact_id=?"""
    params: list = [int(fact_id)]
    if not include_inactive:
        sql += " AND status='active'"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 100)))
    rows = _get_conn().execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["source_message_ids"] = json.loads(item["source_message_ids"] or "[]")
        except (TypeError, json.JSONDecodeError):
            item["source_message_ids"] = []
        result.append(item)
    return result


def get_fact_by_id(fact_id: int) -> dict | None:
    row = _get_conn().execute(
        """SELECT id, content, category, source, strength, created_at,
                  updated_at, occurred_at, source_session_id, source_channel,
                  status, valid_from, valid_to, last_accessed_at, access_count,
                  quality_score, review_status, quality_updated_at
           FROM memory_facts WHERE id=?""",
        (int(fact_id),),
    ).fetchone()
    return dict(row) if row else None


def correct_fact_by_id(fact_id: int, new_content: str) -> dict | None:
    """Correct one fact using an explicit id and mark it as user-verified."""
    content = " ".join(str(new_content or "").split()).strip()
    if not content:
        raise ValueError("记忆内容不能为空")

    conn = _get_conn()
    row = conn.execute(
        "SELECT id, category, content FROM memory_facts WHERE id=? AND status='active'",
        (int(fact_id),),
    ).fetchone()
    if not row:
        return None

    try:
        from brain.memory_corrections import apply_correction_feedback
        apply_correction_feedback(
            [int(fact_id)], action="update",
            reason=f"用户在星图中纠正记忆：{content[:240]}",
            conn=conn, commit=False,
        )
    except Exception:
        pass

    embedding = None
    embedding_model = ""
    embedding_hash = ""
    try:
        from brain.memory_rag import embed_bytes
        from utils.embedding_cache import content_hash
        embedding = embed_bytes(content)
        embedding_model = "BAAI/bge-small-zh-v1.5"
        embedding_hash = content_hash(content)
    except Exception:
        pass

    conn.execute(
        """UPDATE memory_fragments
           SET status='superseded', updated_at=datetime('now','localtime')
           WHERE fact_id=? AND status='active'""",
        (int(fact_id),),
    )
    conn.execute(
        """UPDATE memory_facts
           SET content=?, source='user_correction', strength=1,
               quality_score=1.0, review_status='confirmed',
               quality_updated_at=datetime('now','localtime'),
               embedding=?, embedding_model=?, embedding_content_hash=?,
               updated_at=datetime('now','localtime'), status='active'
           WHERE id=? AND status='active'""",
        (content, embedding, embedding_model, embedding_hash, int(fact_id)),
    )
    add_memory_fragment(
        int(fact_id), content, row["category"], source="user_correction",
        confidence=1.0, commit=False,
    )
    conn.commit()
    return get_fact_by_id(int(fact_id))


def delete_fact_by_id(fact_id: int) -> bool:
    """Delete one fact and its evidence using an explicit id."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, content FROM memory_facts WHERE id=?", (int(fact_id),)
    ).fetchone()
    if not row:
        return False
    try:
        from brain.memory_corrections import apply_correction_feedback
        apply_correction_feedback(
            [int(fact_id)], action="delete",
            reason=f"用户在星图中删除记忆：{str(row['content'])[:240]}",
            conn=conn, commit=False,
        )
    except Exception:
        pass
    conn.execute("DELETE FROM memory_fragments WHERE fact_id=?", (int(fact_id),))
    conn.execute("DELETE FROM memory_facts WHERE id=?", (int(fact_id),))
    conn.commit()
    return True


def _prune_category(conn: sqlite3.Connection, category: str) -> None:
    """如果 category 条目数超出上限，淘汰最旧且强度最低的条目。"""
    try:
        from config import get_memory_config
        cfg = get_memory_config()
        max_items = cfg.get("max_items_per_category", 200)
    except Exception:
        max_items = 200

    count = conn.execute(
        "SELECT COUNT(*) FROM memory_facts WHERE category=? AND status='active'",
        (category,)
    ).fetchone()[0]

    if count <= max_items:
        return

    excess = count - max_items
    pruned_ids = [row["id"] for row in conn.execute(
        """SELECT id FROM memory_facts WHERE category=? AND status='active'
           ORDER BY strength ASC, created_at ASC LIMIT ?""",
        (category, excess),
    ).fetchall()]
    if pruned_ids:
        placeholders = ",".join("?" for _ in pruned_ids)
        conn.execute(
            f"DELETE FROM memory_fragments WHERE fact_id IN ({placeholders})",
            pruned_ids,
        )
    conn.execute(
        """DELETE FROM memory_facts WHERE id IN (
               SELECT id FROM memory_facts WHERE category=? AND status='active'
               ORDER BY strength ASC, created_at ASC LIMIT ?
           )""",
        (category, excess)
    )
    conn.commit()


def search_facts(keyword: str, category: str | None = None) -> list[dict]:
    """按关键词搜索事实。返回匹配条目列表，按 strength 降序。"""
    keyword = keyword.strip()
    if not keyword:
        return []

    conn = _get_conn()
    q = "%" + keyword + "%"
    if category:
        rows = conn.execute(
            """SELECT id, content, category, source, strength, created_at,
                      quality_score, review_status,
                      (SELECT COUNT(*) FROM memory_fragments mf
                       WHERE mf.fact_id=memory_facts.id AND mf.status='active') AS evidence_count
               FROM memory_facts
               WHERE content LIKE ? AND category = ? AND status='active'
               ORDER BY updated_at DESC, strength DESC LIMIT 30""",
            (q, category)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, content, category, source, strength, created_at,
                      quality_score, review_status,
                      (SELECT COUNT(*) FROM memory_fragments mf
                       WHERE mf.fact_id=memory_facts.id AND mf.status='active') AS evidence_count
               FROM memory_facts
               WHERE content LIKE ? AND status='active'
               ORDER BY updated_at DESC, strength DESC LIMIT 30""",
            (q,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_facts(
    old_keyword: str,
    new_content: str,
    category: str | None = None,
    *,
    source_session_id: int | None = None,
    source_channel: str = "",
    source_message_ids: list[int] | None = None,
    persona_id: str = "",
    occurred_at: str = "",
    embed_immediately: bool = False,
) -> int:
    """替换所有匹配 old_keyword 的事实内容。返回更新条数。"""
    old_keyword = old_keyword.strip()
    new_content = new_content.strip()
    if not old_keyword or not new_content:
        return 0

    conn = _get_conn()
    q = "%" + old_keyword + "%"
    target_sql = (
        "SELECT id, category FROM memory_facts "
        "WHERE content LIKE ? AND status='active'"
    )
    target_params: list = [q]
    if category:
        target_sql += " AND category=?"
        target_params.append(category)
    target_facts = [dict(row) for row in conn.execute(
        target_sql, target_params
    ).fetchall()]
    if target_facts:
        try:
            from brain.memory_corrections import apply_correction_feedback
            apply_correction_feedback(
                [fact["id"] for fact in target_facts], action="update",
                reason=f"用户将记忆修正为：{new_content[:240]}", conn=conn, commit=False,
            )
        except Exception:
            pass
    new_embedding = None
    if embed_immediately:
        try:
            from brain.memory_rag import embed_bytes
            new_embedding = embed_bytes(new_content)
        except Exception:
            pass
    if category:
        from utils.embedding_cache import content_hash
        cur = conn.execute(
            """UPDATE memory_facts SET content=?, source='user_saved',
               embedding=?, embedding_model=?, embedding_content_hash=?, updated_at=datetime('now','localtime'),
               source_session_id=COALESCE(?, source_session_id),
               source_channel=CASE WHEN ?<>'' THEN ? ELSE source_channel END,
               status='active'
               WHERE content LIKE ? AND category=? AND status='active'""",
            (new_content, new_embedding,
             'BAAI/bge-small-zh-v1.5' if new_embedding else '',
             content_hash(new_content) if new_embedding else '',
             source_session_id, source_channel, source_channel, q, category)
        )
    else:
        from utils.embedding_cache import content_hash
        cur = conn.execute(
            """UPDATE memory_facts SET content=?, source='user_saved',
               embedding=?, embedding_model=?, embedding_content_hash=?, updated_at=datetime('now','localtime'),
               source_session_id=COALESCE(?, source_session_id),
               source_channel=CASE WHEN ?<>'' THEN ? ELSE source_channel END,
               status='active'
               WHERE content LIKE ? AND status='active'""",
            (new_content, new_embedding,
             'BAAI/bge-small-zh-v1.5' if new_embedding else '',
             content_hash(new_content) if new_embedding else '',
             source_session_id, source_channel, source_channel, q)
        )
    try:
        for fact in target_facts:
            conn.execute(
                """UPDATE memory_fragments SET status='superseded',
                   updated_at=datetime('now','localtime')
                   WHERE fact_id=? AND status='active'""",
                (fact["id"],),
            )
            add_memory_fragment(
                fact["id"], new_content, fact["category"],
                source="user_correction",
                source_session_id=source_session_id,
                source_channel=source_channel,
                source_message_ids=source_message_ids,
                persona_id=persona_id,
                confidence=1.0,
                occurred_at=occurred_at,
                commit=False,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cur.rowcount


def delete_facts(keyword: str, category: str | None = None) -> int:
    """删除所有匹配 keyword 的事实。返回删除条数。"""
    keyword = keyword.strip()
    if not keyword:
        return 0

    conn = _get_conn()
    q = "%" + keyword + "%"
    select_sql = "SELECT id FROM memory_facts WHERE content LIKE ?"
    select_params: list = [q]
    if category:
        select_sql += " AND category=?"
        select_params.append(category)
    fact_ids = [row["id"] for row in conn.execute(
        select_sql, select_params
    ).fetchall()]
    if fact_ids:
        try:
            from brain.memory_corrections import apply_correction_feedback
            apply_correction_feedback(
                fact_ids, action="delete", reason=f"用户删除记忆：{keyword[:240]}",
                conn=conn, commit=False,
            )
        except Exception:
            pass
        placeholders = ",".join("?" for _ in fact_ids)
        conn.execute(
            f"DELETE FROM memory_fragments WHERE fact_id IN ({placeholders})",
            fact_ids,
        )
    if category:
        cur = conn.execute(
            "DELETE FROM memory_facts WHERE content LIKE ? AND category=?",
            (q, category)
        )
    else:
        cur = conn.execute(
            "DELETE FROM memory_facts WHERE content LIKE ?",
            (q,)
        )
    conn.commit()
    return cur.rowcount


def unified_search(keyword: str, category: str | None = None) -> dict:
    """统一搜索：同时查询分类事实和五元组图边。
    返回 {"facts": [...], "graph_edges": [...]}。"""
    keyword = keyword.strip()
    if not keyword:
        return {"facts": [], "graph_edges": []}

    conn = _get_conn()
    q = "%" + keyword + "%"

    # 1. 搜索分类事实
    if category:
        fact_rows = conn.execute(
            """SELECT id, content, category, source, strength, created_at,
                      quality_score, review_status,
                      (SELECT COUNT(*) FROM memory_fragments mf
                       WHERE mf.fact_id=memory_facts.id AND mf.status='active') AS evidence_count
               FROM memory_facts
               WHERE content LIKE ? AND category = ? AND status='active'
               ORDER BY updated_at DESC, strength DESC LIMIT 20""",
            (q, category)
        ).fetchall()
    else:
        fact_rows = conn.execute(
            """SELECT id, content, category, source, strength, created_at,
                      quality_score, review_status,
                      (SELECT COUNT(*) FROM memory_fragments mf
                       WHERE mf.fact_id=memory_facts.id AND mf.status='active') AS evidence_count
               FROM memory_facts
               WHERE content LIKE ? AND status='active'
               ORDER BY updated_at DESC, strength DESC LIMIT 20""",
            (q,)
        ).fetchall()

    # 2. 搜索五元组图边（匹配实体名或关系名）
    edge_rows = conn.execute("""
        SELECT h.name AS head, e.head_type, e.relation,
               t.name AS tail, e.tail_type, e.source, e.strength
        FROM graph_edges e
        JOIN graph_entities h ON e.head_id = h.id
        JOIN graph_entities t ON e.tail_id = t.id
        WHERE h.name LIKE ? OR t.name LIKE ? OR e.relation LIKE ?
        ORDER BY e.strength DESC LIMIT 15
    """, (q, q, q)).fetchall()

    facts = [dict(r) for r in fact_rows]
    if facts:
        try:
            from brain.memory_quality import record_memory_access
            record_memory_access([item["id"] for item in facts])
        except Exception:
            pass
    return {
        "facts": facts,
        "graph_edges": [dict(r) for r in edge_rows],
    }


def format_unified_search_result(result: dict) -> str:
    """将统一搜索结果格式化为 LLM 可读文本。"""
    facts = result.get("facts", [])
    edges = result.get("graph_edges", [])
    total = len(facts) + len(edges)

    if total == 0:
        return ("未找到匹配的记忆。\n\n"
                "\U0001F449 如果连续搜索多次都找不到，请直接告诉用户没找到，"
                "不要反复尝试不同关键词。")

    lines = [f"找到 {len(facts)} 条事实记忆 + {len(edges)} 条关联关系："]

    if facts:
        lines.append("\n【分类事实】")
        for i, f in enumerate(facts, 1):
            cat = f.get("category", "knowledge")
            src = "自动" if f.get("source") == "auto_extracted" else "手动"
            strength = f.get("strength", 1)
            lines.append(
                f"  {i}. [记忆#{f.get('id')}] [{cat}] {f['content']} "
            f"(强度:{strength}, 证据:{f.get('evidence_count', 0)}条, "
            f"质量:{float(f.get('quality_score', 0.5)):.0%}, "
            f"复核:{f.get('review_status', 'normal')}, {src})"
            )

    if edges:
        lines.append("\n【知识图谱关联】")
        for i, e in enumerate(edges, 1):
            src = "自动" if e.get("source", "auto") == "auto" else "手动"
            strength = e.get("strength", 1)
            lines.append(
                f"  {i}. ({e['head']}, {e['head_type']}) "
                f"—[{e['relation']}]→ "
                f"({e['tail']}, {e['tail_type']}) "
                f"[强度:{strength}, {src}]"
            )

    result_str = "\n".join(lines)

    # Layer 3: 结果级引导 — 防止模型过度依赖历史记忆忽略当前对话
    result_str += (
        "\n\n⚠️ 以上是长期记忆中的历史信息。"
        "如果你在当前对话中已经读到过相关内容，请以当前对话内容为准。"
    )

    return result_str


def list_all_facts() -> dict[str, list[dict]]:
    """返回按分类分组的有效事实；历史状态由审计接口查询。"""
    conn = _get_conn()
    result: dict[str, list[dict]] = {cat: [] for cat in ALL_MEMORY_CATEGORIES}
    rows = conn.execute(
        """SELECT id, content, category, source, strength, created_at,
                  quality_score, review_status
           FROM memory_facts WHERE status='active' ORDER BY created_at ASC"""
    ).fetchall()
    for r in rows:
        d = dict(r)
        cat = d.pop("category", "knowledge")
        if cat in result:
            result[cat].append(d)
    return result


def get_fact_count() -> int:
    """返回 memory_facts 表中的事实总数。"""
    conn = _get_conn()
    return conn.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0]


def migrate_from_json(json_path: str | None = None) -> int:
    """将 long_term.json 的数据迁移到 memory_facts 表。
    迁移完成后将 JSON 文件重命名为 .bak。
    如果 memory_facts 表已有数据则跳过。
    返回迁移的条目数。"""
    import json
    import shutil
    from pathlib import Path

    if get_fact_count() > 0:
        return 0

    # 查找 JSON 文件路径
    if json_path is None:
        candidates = [
            Path.home() / ".lianxin" / "long_term.json",
            Path(__file__).parent.parent / "memory" / "long_term.json",
        ]
        json_path = None
        for p in candidates:
            if p.exists():
                json_path = p
                break
        if json_path is None:
            return 0
    else:
        json_path = Path(json_path)

    if not json_path.exists():
        return 0

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return 0

    migrated = 0

    # 检测 v1 格式（扁平 facts 列表）
    if "facts" in data and isinstance(data["facts"], list):
        from brain.memory_store import _migrate_v1_to_v2
        data = _migrate_v1_to_v2(data)
        # 删除迁移标记
        data.pop("_migration_info", None)

    # v2 格式：按分类读取
    for cat in ALL_MEMORY_CATEGORIES:
        for item in data.get(cat, []):
            content = item.get("content", "").strip()
            source = item.get("source", "migrated")
            if content:
                add_fact(content, cat, source=source)
                migrated += 1

    # 重命名原文件为 .bak
    bak_path = json_path.with_suffix(json_path.suffix + ".bak")
    try:
        shutil.move(str(json_path), str(bak_path))
    except OSError:
        pass

    return migrated

# ── 格式化工具 ─────────────────────────────────────────

def format_all_memories(data: dict[str, list[dict]] | None = None) -> str:
    """格式化输出全部记忆（用于 LLM 查看）。"""
    if data is not None:
        lines = []
        for cat in ALL_CATEGORIES:
            items = data.get(cat, [])
            if items:
                lines.append(f"\n【{cat} ({CATEGORY_DESCRIPTIONS.get(cat, '')})】")
                for item in items:
                    src = "自动" if item.get("source") == "auto_extracted" else "手动"
                    lines.append(f"  · {item['content']} (强度:{item.get('strength', 1)}, {src})")
        if len(lines) == 0:
            return "还没有任何记忆。"
        return "\n".join(lines)
    return "还没有任何记忆。"


def build_extraction_prompt(recent_conversation: str) -> str:
    """构建用于自动记忆提取的 prompt。"""
    cat_desc = "\n".join(f"  - {k}：{v}" for k, v in CATEGORY_DESCRIPTIONS.items())
    return f"""分析以下最近的对话内容，从中提取值得长期记忆的信息。

## 记忆分类说明
{cat_desc}

## 提取规则
1. 只提取用户明确表达的稳定事实，不要从你自己的回复中提取
2. 不要记录临时状态（如「今天天气好」「我现在很累」等一次性信息）
3. 每条记忆应该自我完整，脱离上下文也能理解
4. 如果某条信息与已有记忆相似，可以合并或强化（在 review 中注明）
5. 宁少勿多：没有值得记的就不记
6. source_message_ids 必须只填写直接支持该事实的用户消息编号，不要填写助手消息
7. confidence 表示事实可信度，范围 0~1；用户明确陈述通常为 0.9 以上
8. occurred_at 填事件实际发生时间；无法判断时留空，不要猜测
9. 不提取助手/莲心自身的姓名、身份、外貌、瞳色、性格或语言风格；这些属于人格档案，不是用户记忆

## 五元组关系
在同一次输出中提取值得长期保存的实体关系，格式为：
`[主体, 主体类型, 关系, 客体, 客体类型]`。
类型仅限：人物、地点、组织、物品、概念、时间、事件、活动、技术、文件。
五元组必须来自用户明确表达的事实；没有时返回空数组。

## 输出格式（JSON，契约版本 memory_extraction_v2）
{{
    "contract_version": "memory_extraction_v2",
    "memories": [
        {{
            "category": "profile|preferences|events|knowledge|behaviors|skills",
            "content": "完整的记忆文本",
            "reason": "为什么这条信息值得记住",
            "source_message_ids": [123, 124],
            "confidence": 0.95,
            "occurred_at": "YYYY-MM-DD HH:MM:SS 或空字符串"
        }}
    ],
    "quintuples": [
        ["用户", "人物", "正在开发", "莲心AI", "技术"]
    ]
}}

如果没有值得记忆的内容，仍返回完整结构：
{{ "contract_version": "memory_extraction_v2", "memories": [], "quintuples": [] }}。

## 最近的对话
{recent_conversation}"""
