import asyncio
import sqlite3

from sql_guardian import GuardPolicy, NL2SQL, SqlGuard
from sql_guardian.llm import ScriptedClient
from sql_guardian.server import build_server


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _guard(tmp_path):
    path = tmp_path / "s.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL)")
    conn.executemany("INSERT INTO products VALUES (?,?,?)", [(1, "Desk", 450.0), (2, "Lamp", 60.0)])
    conn.commit()
    conn.close()
    return SqlGuard(str(path), GuardPolicy())


def test_tools_are_registered(tmp_path):
    server = build_server(_guard(tmp_path))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"list_tables", "describe_schema", "run_query"}


def test_run_query_tool(tmp_path):
    server = build_server(_guard(tmp_path))
    ok = _text(asyncio.run(server.call_tool("run_query", {"sql": "SELECT name FROM products ORDER BY price"})))
    assert "Lamp" in ok and "Desk" in ok
    bad = _text(asyncio.run(server.call_tool("run_query", {"sql": "DROP TABLE products"})))
    assert bad.startswith("REJECTED")


def test_ask_database_tool(tmp_path):
    guard = _guard(tmp_path)
    llm = ScriptedClient(["```sql\nSELECT name FROM products WHERE price > 100\n```"])
    server = build_server(guard, NL2SQL(guard, llm))
    out = _text(asyncio.run(server.call_tool("ask_database", {"question": "Which products cost over $100?"})))
    assert "Desk" in out and "Lamp" not in out
