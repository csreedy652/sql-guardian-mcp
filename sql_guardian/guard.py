"""Read-only SQL guardrails for LLM-generated queries.

Every query an LLM writes is treated as untrusted input. Instead of trying to
parse SQL with regexes, the guard asks SQLite itself what a statement would do
(via the authorizer callback) and rejects anything that is not a plain read of
an allow-listed table.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

# Authorizer action codes that a read-only query is allowed to trigger.
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,  # WITH RECURSIVE ... (still read-only)
}

# Functions that could leak data or touch the filesystem even inside a SELECT.
_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class GuardError(ValueError):
    """Raised when a query violates a guardrail. The message is safe to show an LLM."""


@dataclass
class GuardPolicy:
    allowed_tables: set[str] | None = None  # None = every table in the schema
    max_rows: int = 200
    timeout_seconds: float = 3.0
    blocked_columns: set[str] = field(default_factory=set)  # "table.column", lowercase


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


def _single_statement(sql: str) -> str:
    """Strip trailing semicolons/whitespace and refuse stacked statements."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise GuardError("Empty query.")
    # sqlite3.complete_statement is True once a full statement has been seen;
    # if the text *before* the final character already forms one, there is more than one.
    for i, ch in enumerate(cleaned):
        if ch == ";" and sqlite3.complete_statement(cleaned[: i + 1]) and cleaned[i + 1 :].strip():
            raise GuardError("Only one SQL statement is allowed per call.")
    return cleaned


class SqlGuard:
    """Validates and executes untrusted SELECT queries against a SQLite database."""

    def __init__(self, db_path: str, policy: GuardPolicy | None = None) -> None:
        self.db_path = db_path
        self.policy = policy or GuardPolicy()

    # ---- connection -----------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        # mode=ro opens the file read-only at the OS level: a second line of defence.
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        return conn

    # ---- schema ---------------------------------------------------------
    def tables(self) -> list[str]:
        with self._connect() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if self.policy.allowed_tables is not None:
            allowed = {t.lower() for t in self.policy.allowed_tables}
            names = [n for n in names if n.lower() in allowed]
        return names

    def describe(self) -> str:
        """Compact schema description suitable for an LLM prompt."""
        lines = []
        with self._connect() as conn:
            for table in self.tables():
                cols = []
                for _, name, ctype, notnull, _, pk in conn.execute(f'PRAGMA table_info("{table}")'):
                    if f"{table}.{name}".lower() in self.policy.blocked_columns:
                        continue
                    flags = " PK" if pk else (" NOT NULL" if notnull else "")
                    cols.append(f"{name} {ctype or 'ANY'}{flags}")
                lines.append(f"{table}({', '.join(cols)})")
        return "\n".join(lines)

    # ---- validation -----------------------------------------------------
    def _authorizer(self, violations: list[str]):
        allowed_tables = (
            {t.lower() for t in self.policy.allowed_tables}
            if self.policy.allowed_tables is not None else None
        )
        blocked_cols = self.policy.blocked_columns

        def check(action, arg1, arg2, _db, _trigger):
            if action not in _ALLOWED_ACTIONS:
                violations.append(f"operation not permitted (sqlite action {action})")
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ:
                table, column = (arg1 or "").lower(), (arg2 or "").lower()
                if table.startswith("sqlite_"):
                    violations.append("system tables are not accessible")
                    return sqlite3.SQLITE_DENY
                if allowed_tables is not None and table not in allowed_tables:
                    violations.append(f"table '{arg1}' is not on the allow-list")
                    return sqlite3.SQLITE_DENY
                if f"{table}.{column}" in blocked_cols:
                    violations.append(f"column '{arg1}.{arg2}' is restricted")
                    return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
                violations.append(f"function '{arg2}' is blocked")
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        return check

    def validate(self, sql: str, params: tuple | dict = ()) -> str:
        """Return the normalised statement or raise GuardError explaining why it is unsafe."""
        statement = _single_statement(sql)
        violations: list[str] = []
        conn = self._connect()
        try:
            conn.set_authorizer(self._authorizer(violations))
            # EXPLAIN compiles the statement (running the authorizer) without executing it.
            conn.execute(f"EXPLAIN {statement}", params)
        except sqlite3.Error as exc:
            reason = "; ".join(dict.fromkeys(violations)) or str(exc)
            raise GuardError(f"Query rejected: {reason}") from None
        finally:
            conn.close()
        return statement

    # ---- execution ------------------------------------------------------
    def run(self, sql: str, params: tuple | dict = ()) -> QueryResult:
        """Validate, then execute with a row cap and a wall-clock timeout.

        `params` are bound by SQLite, never interpolated into the string.
        """
        statement = self.validate(sql, params)
        limit = self.policy.max_rows
        wrapped = f"SELECT * FROM ({statement}) LIMIT {limit + 1}"
        violations: list[str] = []
        deadline = time.monotonic() + self.policy.timeout_seconds

        conn = self._connect()
        try:
            conn.set_authorizer(self._authorizer(violations))
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            start = time.perf_counter()
            try:
                cur = conn.execute(wrapped, params)
                rows = cur.fetchall()
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise GuardError(f"Query exceeded {self.policy.timeout_seconds}s timeout.") from None
                raise GuardError(f"Query failed: {exc}") from None
            elapsed = (time.perf_counter() - start) * 1000
            columns = [d[0] for d in cur.description or []]
        finally:
            conn.close()

        truncated = len(rows) > limit
        return QueryResult(statement, columns, rows[:limit], truncated, round(elapsed, 2))
