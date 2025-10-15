import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# DB path inside container volume
DB_DIR = Path("/app/data")
DB_PATH = DB_DIR / "rentals.db"

logger = logging.getLogger(__name__)


def _dict_factory(cursor: sqlite3.Cursor, row: Tuple[Any, ...]) -> Dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


async def init_db() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()

    def _init() -> None:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rentals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    rent_price INTEGER NOT NULL,
                    start_time INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rentals_active ON rentals(active);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    price INTEGER NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS revenues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    rental_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(date, rental_id)
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    await loop.run_in_executor(None, _init)
    logger.info("Database initialized at %s", DB_PATH)


@asynccontextmanager
async def _connect() -> Any:
    loop = asyncio.get_running_loop()
    def _open():
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = _dict_factory
        return c
    conn = await loop.run_in_executor(None, _open)
    try:
        yield conn
    finally:
        await loop.run_in_executor(None, conn.close)


async def add_rental(tool_name: str, rent_price: int, user_id: int) -> int:
    start_ts = int(datetime.utcnow().timestamp())
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> int:
            cur = conn.execute(
                "INSERT INTO rentals(tool_name, rent_price, start_time, user_id, active) VALUES (?, ?, ?, ?, 1)",
                (tool_name, rent_price, start_ts, user_id),
            )
            conn.commit()
            return int(cur.lastrowid)

        rental_id = await loop.run_in_executor(None, _exec)
        logger.info("Rental added: id=%s, tool=%s, price=%s, user=%s", rental_id, tool_name, rent_price, user_id)
        return rental_id


async def get_active_rentals(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> List[Dict[str, Any]]:
            if user_id is None:
                cur = conn.execute("SELECT * FROM rentals WHERE active = 1 ORDER BY id DESC")
            else:
                cur = conn.execute(
                    "SELECT * FROM rentals WHERE active = 1 AND user_id = ? ORDER BY id DESC",
                    (user_id,),
                )
            return list(cur.fetchall())

        rows = await loop.run_in_executor(None, _query)
        return rows


async def close_rental(rental_id: int) -> None:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute("UPDATE rentals SET active = 0 WHERE id = ?", (rental_id,))
            conn.commit()

        await loop.run_in_executor(None, _exec)
        logger.info("Rental closed: id=%s", rental_id)


async def renew_rental(rental_id: int) -> None:
    new_start = int(datetime.utcnow().timestamp())
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute(
                "UPDATE rentals SET start_time = ?, active = 1 WHERE id = ?",
                (new_start, rental_id),
            )
            conn.commit()

        await loop.run_in_executor(None, _exec)
        logger.info("Rental renewed: id=%s", rental_id)


async def get_rental_by_id(rental_id: int) -> Optional[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> Optional[Dict[str, Any]]:
            cur = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,))
            row = cur.fetchone()
            return row

        return await loop.run_in_executor(None, _query)


async def all_active_for_reschedule() -> List[Dict[str, Any]]:
    # Used on startup to reschedule expiration jobs
    return await get_active_rentals()


async def add_revenue(date: str, rental_id: int, amount: int) -> None:
    ts = int(datetime.utcnow().timestamp())
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute(
                "INSERT OR IGNORE INTO revenues(date, rental_id, amount, created_at) VALUES (?, ?, ?, ?)",
                (date, rental_id, amount, ts),
            )
            conn.commit()

        await loop.run_in_executor(None, _exec)


async def sum_revenue_by_date(date: str) -> int:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> int:
            cur = conn.execute("SELECT COALESCE(SUM(amount), 0) as s FROM revenues WHERE date = ?", (date,))
            row = cur.fetchone()
            return int(row["s"]) if row and row["s"] is not None else 0

        return await loop.run_in_executor(None, _query)

# --- Catalog (tools) ---

async def upsert_tool(name: str, price: int) -> None:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute(
                "INSERT INTO tools(name, price) VALUES(?, ?) ON CONFLICT(name) DO UPDATE SET price=excluded.price",
                (name, price),
            )
            conn.commit()

        await loop.run_in_executor(None, _exec)


async def get_tool_by_name(name: str) -> Optional[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> Optional[Dict[str, Any]]:
            cur = conn.execute("SELECT * FROM tools WHERE name = ?", (name,))
            return cur.fetchone()

        return await loop.run_in_executor(None, _query)


async def list_tools(limit: int = 50) -> List[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> List[Dict[str, Any]]:
            cur = conn.execute("SELECT * FROM tools ORDER BY name ASC LIMIT ?", (limit,))
            return list(cur.fetchall())

        return await loop.run_in_executor(None, _query)


async def import_catalog_from_csv(csv_path: str) -> int:
    import csv
    loop = asyncio.get_running_loop()

    def _read_rows() -> List[tuple[str, int]]:
        rows: List[tuple[str, int]] = []
        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or len(row) < 2:
                    continue
                name = str(row[0]).strip()
                try:
                    price = int(str(row[1]).strip())
                except ValueError:
                    continue
                if name and price > 0:
                    rows.append((name, price))
        return rows

    rows = await loop.run_in_executor(None, _read_rows)
    count = 0
    for name, price in rows:
        await upsert_tool(name, price)
        count += 1
    logger.info("Catalog imported: %s items from %s", count, csv_path)
    return count


async def reset_database() -> None:
    """Remove SQLite file and recreate schema."""
    loop = asyncio.get_running_loop()
    def _remove_db() -> None:
        try:
            if DB_PATH.exists():
                DB_PATH.unlink()
        except Exception as e:
            # If file is locked or cannot be removed, fallback to wiping tables
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            try:
                conn.execute("DELETE FROM rentals;")
                conn.execute("DELETE FROM revenues;")
                conn.execute("DELETE FROM tools;")
                conn.commit()
            finally:
                conn.close()

    await loop.run_in_executor(None, _remove_db)
    await init_db()
    logger.info("Database reset completed")


async def get_tool_by_id(tool_id: int) -> Optional[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> Optional[Dict[str, Any]]:
            cur = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,))
            return cur.fetchone()

        return await loop.run_in_executor(None, _query)


async def update_tool_name(tool_id: int, new_name: str) -> None:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute("UPDATE tools SET name = ? WHERE id = ?", (new_name, tool_id))
            conn.commit()

        await loop.run_in_executor(None, _exec)


async def update_tool_price(tool_id: int, new_price: int) -> None:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute("UPDATE tools SET price = ? WHERE id = ?", (new_price, tool_id))
            conn.commit()

        await loop.run_in_executor(None, _exec)


async def delete_tool(tool_id: int) -> None:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _exec() -> None:
            conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
            conn.commit()

        await loop.run_in_executor(None, _exec)


async def sum_revenue_by_date_for_user(date: str, user_id: int) -> int:
    loop = asyncio.get_running_loop()
    async with _connect() as conn:
        def _query() -> int:
            cur = conn.execute(
                """
                SELECT COALESCE(SUM(r.amount), 0) as s
                FROM revenues r
                JOIN rentals t ON t.id = r.rental_id
                WHERE r.date = ? AND t.user_id = ?
                """,
                (date, user_id),
            )
            row = cur.fetchone()
            return int(row["s"]) if row and row["s"] is not None else 0

        return await loop.run_in_executor(None, _query)


