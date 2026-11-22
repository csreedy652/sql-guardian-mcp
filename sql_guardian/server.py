"""MCP server exposing guarded, read-only SQL tools to any MCP client
(Claude Desktop, Cursor, VS Code agents, custom agent frameworks).

Run:  python -m sql_guardian.server --db examples/demo.db
"""

from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer

from .guard import GuardError, GuardPolicy, SqlGuard
from .llm import OpenAICompatibleClient
from .nl2sql import NL2SQL


def _format(result) -> str:
    if not result.rows:
        return "(no rows)"
    header = " | ".join(result.columns)
    lines = [header, "-" * len(header)]
    lines += [" | ".join("" if v is None else str(v) for v in row) for row in result.rows]
    if result.truncated:
        lines.append(f"... truncated to {len(result.rows)} rows")
    return "\n".join(lines)


def build_server(guard: SqlGuard, nl2sql: NL2SQL | None = None) -> MCPServer:
    server = MCPServer(
        name="sql-guardian",
        instructions=("Read-only access to a SQL database. Call describe_schema first, "
                      "then run_query with a single SELECT. Write operations are rejected."),
    )

    @server.tool(description="List the tables this server is allowed to read.")
    def list_tables() -> list[str]:
        return guard.tables()

    @server.tool(description="Describe allowed tables and their columns.")
    def describe_schema() -> str:
        return guard.describe()

    @server.tool(description="Run one read-only SELECT. Rows are capped and the query times out.")
    def run_query(sql: str) -> str:
        try:
            return _format(guard.run(sql))
        except GuardError as exc:
            return f"REJECTED: {exc}"

    if nl2sql is not None:
        @server.tool(description="Answer a natural-language question by generating and running safe SQL.")
        def ask_database(question: str) -> str:
            answer = nl2sql.ask(question)
            if not answer.ok:
                last = answer.attempts[-1]
                return f"Could not produce a safe query after {len(answer.attempts)} attempts. Last error: {last.error}"
            return f"SQL:\n{answer.result.sql}\n\n{_format(answer.result)}"

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="SQL Guardian MCP server")
    parser.add_argument("--db", required=True, help="Path to a SQLite database")
    parser.add_argument("--tables", help="Comma-separated allow-list of tables")
    parser.add_argument("--block-columns", default="", help="Comma-separated table.column list to hide")
    parser.add_argument("--max-rows", type=int, default=200)
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    args = parser.parse_args()

    policy = GuardPolicy(
        allowed_tables=set(args.tables.split(",")) if args.tables else None,
        max_rows=args.max_rows,
        blocked_columns={c.strip().lower() for c in args.block_columns.split(",") if c.strip()},
    )
    guard = SqlGuard(args.db, policy)
    nl2sql = NL2SQL(guard, OpenAICompatibleClient()) if os.getenv("OPENAI_API_KEY") else None
    build_server(guard, nl2sql).run(transport=args.transport)


if __name__ == "__main__":
    main()
