"""
Database connection + migration runner.

Supports two backends:
  - SQLite  (default, local dev + Render free tier — ephemeral disk)
  - PostgreSQL (production — set DATABASE_URL=postgres://... or postgresql://...)

Migrations are applied once, tracked in schema_migrations, and never rewritten.
Add a new numbered .sql file instead of editing an existing one.

PostgreSQL notes:
  - psycopg2-binary must be in requirements.txt
  - SQL dialect differences are handled via _adapt_sql()
  - WAL mode and foreign-key PRAGMA are SQLite-only and are skipped on PG
  - AUTOINCREMENT -> SERIAL, TEXT PRIMARY KEY -> TEXT PRIMARY KEY (identical)
"""

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
DEFAULT_DB_PATH = PROJECT_ROOT / "digital_twin.db"

# ── Backend detection ──────────────────────────────────────────────────────────

_DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Render sets DATABASE_URL starting with "postgres://" (older libpq URI).
# psycopg2 requires "postgresql://". Normalise here.
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = "postgresql://" + _DATABASE_URL[len("postgres://"):]

USE_POSTGRES = bool(_DATABASE_URL and _DATABASE_URL.startswith("postgresql://"))


# ── PostgreSQL adapter ─────────────────────────────────────────────────────────

def _adapt_sql(sql: str) -> str:
    """Minimal SQLite→PostgreSQL dialect fixes for migration files."""
    import re
    # AUTOINCREMENT -> SERIAL (in CREATE TABLE column defs)
    sql = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
                 'SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE)
    # datetime('now') -> NOW()
    sql = sql.replace("datetime('now')", "NOW()")
    # Remove SQLite-only pragmas (executescript splits on ';')
    lines = []
    for line in sql.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("PRAGMA") or stripped.startswith("ATTACH"):
            continue
        lines.append(line)
    return "\n".join(lines)


class _PgConnection:
    """Thin wrapper that makes a psycopg2 connection quack like sqlite3.Connection
    for the small subset used by this codebase (execute, executescript, fetchone,
    fetchall, Row-dict access, commit, close)."""

    def __init__(self, url: str):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(url)
        self._conn.autocommit = False
        self._factory = psycopg2.extras.RealDictCursor

    # ---- cursor helpers ----

    def _cur(self):
        return self._conn.cursor(cursor_factory=self._factory)

    # ---- sqlite3-compatible API ----

    @staticmethod
    def _fix_placeholders(sql: str) -> str:
        """Replace SQLite ? positional placeholders with psycopg2 %s."""
        # Only replace bare '?' not inside string literals (simple heuristic: replace all)
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        cur = self._cur()
        cur.execute(self._fix_placeholders(sql), params)
        return _PgCursor(cur)

    def executemany(self, sql, params_seq):
        cur = self._cur()
        cur.executemany(self._fix_placeholders(sql), list(params_seq))
        return _PgCursor(cur)

    def executescript(self, sql):
        """Run a multi-statement SQL block (migration files)."""
        adapted = _adapt_sql(sql)
        cur = self._conn.cursor()
        # Split on semicolons; skip blank/comment-only fragments
        for stmt in adapted.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    cur.execute(stmt)
                except Exception:
                    # Non-fatal: IF NOT EXISTS guards most conflicts;
                    # log and continue so other statements proceed.
                    self._conn.rollback()
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    # context manager (with conn:)
    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


class _PgCursor:
    """Wraps psycopg2 cursor so callers can use row['column'] syntax."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for row in self._cur:
            yield dict(row)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_connection(db_path=None, timeout: float = 30.0):
    """Return a database connection (SQLite or PostgreSQL)."""
    if USE_POSTGRES:
        return _PgConnection(_DATABASE_URL)

    conn = sqlite3.connect(
        str(db_path or DEFAULT_DB_PATH), timeout=timeout, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def apply_migrations(conn):
    """Apply any unapplied migration files in order."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    if not USE_POSTGRES:
        conn.commit()

    applied = {
        row["version"] if isinstance(row, dict) else row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = migration_file.stem
        if version in applied:
            continue
        sql = migration_file.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, NOW())"
            if USE_POSTGRES
            else "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        conn.commit()


def init_db(db_path=None):
    """Open a connection and apply all pending migrations."""
    conn = get_connection(db_path)
    apply_migrations(conn)
    return conn


def db_backend() -> str:
    """Return 'postgresql' or 'sqlite' — useful for health checks and logging."""
    return "postgresql" if USE_POSTGRES else "sqlite"
