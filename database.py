"""
HatBhau (हट भाउ) — Database Layer
Simplified: uses LIKE for search (indexed), no FTS5 to avoid errors.
"""

import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from scraper import SITES   # for label → key resolution

DB_FILE = "hatbhau.db"

PRODUCT_SCHEMA: Dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "source": "TEXT NOT NULL",
    "name": "TEXT NOT NULL",
    "price": "REAL NOT NULL",
    "currency": "TEXT NOT NULL",
    "original_price": "REAL",
    "discount_percent": "REAL",
    "url": "TEXT",
    "image_url": "TEXT",
    "category": "TEXT",
    "rating": "REAL",
    "reviews": "INTEGER",
    "availability": "TEXT",
    "scraped_at": "TEXT NOT NULL",
}
PRODUCT_INPUT_FIELDS = [f for f in PRODUCT_SCHEMA if f not in ("id", "scraped_at")]

PRICE_HISTORY_SCHEMA: Dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "source": "TEXT NOT NULL",
    "url": "TEXT NOT NULL",
    "price": "REAL NOT NULL",
    "currency": "TEXT NOT NULL",
    "recorded_at": "TEXT NOT NULL",
}

SCRAPE_LOG_SCHEMA: Dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "source": "TEXT NOT NULL",
    "status": "TEXT NOT NULL",
    "products_found": "INTEGER",
    "message": "TEXT",
    "duration_seconds": "REAL",
    "started_at": "TEXT NOT NULL",
    "finished_at": "TEXT",
}

# ── Fuzzy matching config (only for grouping, not for search) ──
MATCH_BRANDS: List[str] = [
    "samsung", "apple", "iphone", "xiaomi", "redmi", "realme", "oppo",
    "vivo", "oneplus", "nokia", "motorola", "moto", "infinix", "itel",
    "tecno", "poco", "honor", "huawei", "nothing", "google", "pixel",
    "lava", "micromax", "iqoo", "nubia", "cmf",
]
MATCH_STOPWORDS = {
    "mobile", "phone", "smartphone", "edition", "version", "series",
    "in", "and", "with", "gb", "tb", "ram", "rom", "storage",
    "memory", "nepal", "india", "pakistan", "price", "the", "a", "an",
    "of", "for", "5g", "4g",
}
MATCH_SEQ_THRESHOLD = 0.82
MATCH_TOKEN_THRESHOLD = 0.60
MATCH_TOKEN_SEQ_FLOOR = 0.60


def get_conn() -> sqlite3.Connection:
    """Return a connection with optimised PRAGMA settings and busy timeout."""
    conn = sqlite3.connect(DB_FILE, timeout=10.0)   # 10s busy timeout
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-100000")          # 100MB cache
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=30000000000")       # 30GB
    conn.row_factory = sqlite3.Row
    return conn


def _create_table(conn, table_name: str, schema: Dict[str, str]):
    cols_sql = ", ".join(f"{col} {ddl}" for col, ddl in schema.items())
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_sql})")


def init_db():
    with get_conn() as conn:
        _create_table(conn, "products", PRODUCT_SCHEMA)
        _create_table(conn, "price_history", PRICE_HISTORY_SCHEMA)
        _create_table(conn, "scrape_log", SCRAPE_LOG_SCHEMA)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS phone_groups ("
            " group_id TEXT PRIMARY KEY, base_name TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS group_members ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL,"
            " product_id INTEGER NOT NULL, source TEXT NOT NULL)"
        )

        # ── All essential indexes for fast filtering, sorting and searching ──
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_source ON products(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_discount ON products(discount_percent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_rating ON products(rating)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_scraped_at ON products(scraped_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")   # for LIKE speed
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_url ON products(url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_price_source ON products(price, source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_name_source ON products(name, source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_url ON price_history(source, url)")

        conn.commit()
    print(f"[DB] Ready: {DB_FILE}")


# ── CRUD operations ──────────────────────────────────────────────────────

def clear_source(source: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM group_members WHERE product_id IN "
            "(SELECT id FROM products WHERE source=?)", (source,)
        )
        conn.execute("DELETE FROM products WHERE source=?", (source,))
        conn.commit()
    _rebuild_groups()


def save_products(source: str, products: List[dict]) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        prev = {
            row["url"]: row["price"]
            for row in conn.execute(
                "SELECT url, price FROM products WHERE source=? AND url IS NOT NULL", (source,)
            ).fetchall()
        }

        conn.execute(
            "DELETE FROM group_members WHERE product_id IN "
            "(SELECT id FROM products WHERE source=?)", (source,)
        )
        conn.execute("DELETE FROM products WHERE source=?", (source,))

        cols = PRODUCT_INPUT_FIELDS + ["scraped_at"]
        placeholders = ", ".join("?" for _ in cols)
        insert_sql = f"INSERT INTO products ({', '.join(cols)}) VALUES ({placeholders})"

        history_rows = []
        for p in products:
            row = [p.get(field) for field in PRODUCT_INPUT_FIELDS] + [now]
            conn.execute(insert_sql, row)

            url = p.get("url")
            price = p.get("price")
            if url and price is not None and prev.get(url) != price:
                history_rows.append((source, url, price, p.get("currency"), now))

        if history_rows:
            conn.executemany(
                "INSERT INTO price_history (source,url,price,currency,recorded_at) VALUES (?,?,?,?,?)",
                history_rows,
            )
        conn.commit()

    _rebuild_groups()
    return {"source": source, "count": len(products), "scraped_at": now}


def log_scrape(source: str, status: str, products_found: int, message: str,
               duration_seconds: float, started_at: str, finished_at: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scrape_log (source,status,products_found,message,"
            "duration_seconds,started_at,finished_at) VALUES (?,?,?,?,?,?,?)",
            (source, status, products_found, message, duration_seconds, started_at, finished_at),
        )
        conn.commit()


# ── READ HELPERS ───────────────────────────────────────────────────────

def get_all_products() -> List[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY price ASC")]


def get_products_by_source(source: str) -> List[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM products WHERE source=? ORDER BY price ASC", (source,))]


def get_distinct_sources() -> List[str]:
    with get_conn() as conn:
        return [r["source"] for r in conn.execute("SELECT DISTINCT source FROM products")]


def get_stats_by_source(source: str) -> Optional[dict]:
    with get_conn() as conn:
        stats = conn.execute(
            "SELECT COUNT(*) AS total, MIN(price) AS min_price, MAX(price) AS max_price, "
            "ROUND(AVG(price),2) AS avg_price, currency FROM products WHERE source=?",
            (source,),
        ).fetchone()
        last_log = conn.execute(
            "SELECT * FROM scrape_log WHERE source=? ORDER BY id DESC LIMIT 1", (source,)
        ).fetchone()
        if not stats or not stats["total"]:
            return {
                "total": 0, "min_price": None, "max_price": None, "avg_price": None,
                "currency": None,
                "last_scrape": last_log["finished_at"] if last_log else None,
                "status": last_log["status"] if last_log else "idle",
                "message": last_log["message"] if last_log else "",
            }
        return {
            "total": stats["total"], "min_price": stats["min_price"],
            "max_price": stats["max_price"], "avg_price": stats["avg_price"],
            "currency": stats["currency"],
            "last_scrape": last_log["finished_at"] if last_log else None,
            "status": last_log["status"] if last_log else "idle",
            "message": last_log["message"] if last_log else "",
        }


def get_price_history(source: str, url: str) -> List[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT price, currency, recorded_at FROM price_history "
            "WHERE source=? AND url=? ORDER BY recorded_at ASC", (source, url))]


def get_recent_scrape_logs(limit: int = 30) -> List[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM scrape_log ORDER BY id DESC LIMIT ?", (limit,))]


# ── FUZZY GROUPING (only used after saves, not during user search) ──

def _normalise(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> set:
    return set(_normalise(name).split()) - MATCH_STOPWORDS


def _phones_match(a: str, b: str) -> bool:
    na, nb = _normalise(a), _normalise(b)
    if not any(brand in na and brand in nb for brand in MATCH_BRANDS):
        return False
    seq = SequenceMatcher(None, na, nb).ratio()
    if seq >= MATCH_SEQ_THRESHOLD:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= MATCH_TOKEN_THRESHOLD and seq >= MATCH_TOKEN_SEQ_FLOOR


def _rebuild_groups():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, source FROM products").fetchall()
    if not rows:
        return

    groups: Dict[int, list] = {}
    assigned: Dict[int, int] = {}
    by_id = {r["id"]: r for r in rows}

    for row in rows:
        pid = row["id"]
        if pid in assigned:
            continue
        joined = False
        for rep_id in list(groups.keys()):
            if _phones_match(row["name"], by_id[rep_id]["name"]):
                groups[rep_id].append(row)
                assigned[pid] = rep_id
                joined = True
                break
        if not joined:
            groups[pid] = [row]
            assigned[pid] = pid

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("DELETE FROM group_members")
        conn.execute("DELETE FROM phone_groups")
        cross_count = 0
        for rep_id, members in groups.items():
            sources = {m["source"] for m in members}
            if len(sources) < 2:
                continue
            group_id = f"grp_{abs(hash(rep_id)) % 10**7:07d}"
            conn.execute(
                "INSERT OR IGNORE INTO phone_groups (group_id, base_name, created_at) VALUES (?,?,?)",
                (group_id, members[0]["name"], now),
            )
            for m in members:
                conn.execute(
                    "INSERT INTO group_members (group_id, product_id, source) VALUES (?,?,?)",
                    (group_id, m["id"], m["source"]),
                )
            cross_count += 1
        conn.commit()
    print(f"[DB] {cross_count} cross-source comparison groups rebuilt")


def get_comparisons() -> List[dict]:
    with get_conn() as conn:
        groups = conn.execute(
            "SELECT DISTINCT gm.group_id, pg.base_name FROM group_members gm "
            "JOIN phone_groups pg ON gm.group_id = pg.group_id "
            "GROUP BY gm.group_id HAVING COUNT(DISTINCT gm.source) > 1 "
            "ORDER BY pg.created_at DESC"
        ).fetchall()
        comparisons = []
        for g in groups:
            members = conn.execute(
                "SELECT p.* FROM products p JOIN group_members gm ON p.id = gm.product_id "
                "WHERE gm.group_id=? ORDER BY p.price ASC", (g["group_id"],)
            ).fetchall()
            if len(members) < 2:
                continue
            phones = [dict(m) for m in members]
            prices = [m["price"] for m in phones]
            min_p, max_p = min(prices), max(prices)
            comparisons.append({
                "group_id": g["group_id"],
                "base_name": g["base_name"],
                "phones": phones,
                "price_range": {
                    "min": min_p, "max": max_p, "difference": round(max_p - min_p, 2),
                    "savings_percent": round((1 - min_p / max_p) * 100, 1) if max_p else 0,
                },
            })
        return comparisons


# ── QUERY HELPERS (simple, reliable LIKE) ─────────────────────────────

_SORT_MAP = {
    "updated": "scraped_at DESC",
    "price_asc": "price ASC",
    "price_desc": "price DESC",
    "rating": "rating DESC",
    "discount": "discount_percent DESC",
}


def query_products(q: Optional[str] = None, store: Optional[str] = None,
                    category: Optional[str] = None, min_price: Optional[float] = None,
                    max_price: Optional[float] = None, min_rating: Optional[float] = None,
                    min_discount: Optional[float] = None, sort_by: str = "updated",
                    page: int = 1, per_page: int = 20) -> dict:
    """
    Simple, reliable search using LIKE with an index on name.
    All filters are applied in SQL.
    """
    where, params = [], []

    # Store filter: resolve label → key if needed
    if store:
        if store in SITES:
            where.append("source = ?")
            params.append(store)
        else:
            matched_key = None
            for key, cfg in SITES.items():
                if cfg.get("label", "").lower() == store.lower():
                    matched_key = key
                    break
            if matched_key:
                where.append("source = ?")
                params.append(matched_key)
            else:
                where.append("source = ?")
                params.append(store)

    if category:
        if category.lower() == "general":
            where.append("(category IS NULL OR category = '' OR category = 'General')")
        else:
            where.append("category = ?")
            params.append(category)

    if min_price is not None:
        where.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("price <= ?")
        params.append(max_price)
    if min_rating is not None:
        where.append("rating >= ?")
        params.append(min_rating)
    if min_discount is not None:
        where.append("discount_percent >= ?")
        params.append(min_discount)

    # Search term: use LIKE (with index)
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = _SORT_MAP.get(sort_by, _SORT_MAP["updated"])
    per_page = max(1, min(per_page, 100))
    page = max(1, page)

    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM products {where_sql}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT id, name, price, original_price, discount_percent, "
            f"image_url, source, category, rating, availability, scraped_at "
            f"FROM products {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

    pages = max(1, (total + per_page - 1) // per_page)
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "pages": pages}


def get_categories() -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(category, ''), 'General') AS name, COUNT(*) AS count "
            "FROM products GROUP BY name ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_product(product_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None


def get_price_history_by_id(product_id: int) -> List[dict]:
    p = get_product(product_id)
    if not p or not p.get("url"):
        return []
    return get_price_history(p["source"], p["url"])


def compare_query(q: str) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? ORDER BY price ASC", (f"%{q}%",)
        ).fetchall()
    items = [dict(r) for r in rows]
    if not items:
        return {"all": [], "cheapest": None, "by_store": {}, "total": 0}
    by_store: Dict[str, list] = {}
    for p in items:
        by_store.setdefault(p["source"], []).append(p)
    return {"all": items, "cheapest": items[0], "by_store": by_store, "total": len(items)}


if __name__ == "__main__":
    init_db()
    print("Database ready!")