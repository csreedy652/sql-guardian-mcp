import sqlite3

import pytest

from sql_guardian import GuardError, GuardPolicy, SqlGuard
from sql_guardian.llm import ScriptedClient
from sql_guardian.nl2sql import NL2SQL, extract_sql


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, region TEXT);
        CREATE TABLE salaries (employee TEXT, amount REAL);
    """)
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?)",
                     [(i, f"c{i}", f"c{i}@x.com", "West" if i % 2 else "East") for i in range(1, 31)])
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def guard(db):
    return SqlGuard(db, GuardPolicy(allowed_tables={"customers"}, max_rows=10,
                                    blocked_columns={"customers.email"}))


def test_simple_select(guard):
    res = guard.run("SELECT id, name FROM customers WHERE region = 'West'")
    assert res.columns == ["id", "name"]
    assert len(res.rows) == 10 and res.truncated


def test_parameters_are_bound_not_interpolated(guard):
    res = guard.run("SELECT name FROM customers WHERE id = ?", (3,))
    assert res.rows == [("c3",)]
    res = guard.run("SELECT name FROM customers WHERE name = ?", ("x' OR '1'='1",))
    assert res.rows == []


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers",
    "UPDATE customers SET name = 'x'",
    "INSERT INTO customers (name) VALUES ('x')",
    "DROP TABLE customers",
    "CREATE TABLE evil (x)",
    "PRAGMA table_info(customers)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
])
def test_writes_and_admin_are_rejected(guard, sql):
    with pytest.raises(GuardError):
        guard.run(sql)


def test_stacked_statements_rejected(guard):
    with pytest.raises(GuardError, match="one SQL statement"):
        guard.run("SELECT 1; DROP TABLE customers")


def test_table_allow_list(guard):
    with pytest.raises(GuardError, match="allow-list"):
        guard.run("SELECT * FROM salaries")
    with pytest.raises(GuardError, match="allow-list"):
        guard.run("SELECT c.name FROM customers c JOIN salaries s ON 1=1")


def test_blocked_column(guard):
    with pytest.raises(GuardError, match="restricted"):
        guard.run("SELECT email FROM customers")
    with pytest.raises(GuardError, match="restricted"):
        guard.run("SELECT * FROM customers")


def test_system_tables_hidden(guard):
    with pytest.raises(GuardError):
        guard.run("SELECT sql FROM sqlite_master")


def test_timeout(db):
    slow = SqlGuard(db, GuardPolicy(timeout_seconds=0.2))
    with pytest.raises(GuardError, match="timeout"):
        slow.run("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT max(x) FROM n")


def test_describe_hides_blocked_and_disallowed(guard):
    schema = guard.describe()
    assert "customers(" in schema and "email" not in schema and "salaries" not in schema


def test_extract_sql():
    assert extract_sql("Sure!\n```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


def test_nl2sql_repairs_after_rejection(guard):
    llm = ScriptedClient([
        "```sql\nSELECT * FROM salaries\n```",          # rejected: not allow-listed
        "```sql\nSELECT count(*) AS n FROM customers\n```",
    ])
    answer = NL2SQL(guard, llm).ask("How many customers are there?")
    assert answer.ok and answer.result.rows == [(30,)]
    assert len(answer.attempts) == 2 and "allow-list" in answer.attempts[0].error
    assert "was rejected" in llm.calls[1][1]


def test_nl2sql_gives_up(guard):
    llm = ScriptedClient(["DELETE FROM customers"] * 3)
    answer = NL2SQL(guard, llm, max_attempts=3).ask("delete everything")
    assert not answer.ok and len(answer.attempts) == 3
