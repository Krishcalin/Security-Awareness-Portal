"""Database access.

One connection pool, one idempotent schema bootstrap, and thin query helpers.
The same shape as the SAP scanner's server tier, deliberately: two products in
one shop that access Postgres two different ways is two things to learn and two
places for a bug to hide.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from server.config import settings

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_pool: Optional[ConnectionPool] = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings.validate()
        _pool = ConnectionPool(settings.db_dsn, min_size=settings.pool_min,
                               max_size=settings.pool_max, open=True,
                               kwargs={"row_factory": dict_row})
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with pool().connection() as conn:
        yield conn


def init_schema() -> None:
    """Apply schema.sql. Idempotent — every statement is CREATE/ALTER ... IF NOT
    EXISTS, so this both creates a new database and upgrades an existing one."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connection() as conn:
        conn.execute(sql)
        conn.commit()
    log.info("schema applied")


def query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: Sequence[Any] = ()) -> None:
    with connection() as conn:
        conn.execute(sql, params)
        conn.commit()
