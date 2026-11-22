# SQL Guardian MCP

**Read-only, guarded SQL access for AI agents, served over the Model Context Protocol (MCP).**

LLM agents are good at writing SQL, but a query written by a model is untrusted input. SQL Guardian sits between an agent and a database. It lets agents explore and query data while making writes, data leaks and runaway queries impossible by construction.

```
 MCP client (Claude Desktop, Cursor, VS Code agent, custom agent)
        │  list_tables / describe_schema / run_query / ask_database
        ▼
 ┌──────────────── SQL Guardian ────────────────┐
 │ 1. single-statement check                    │
 │ 2. SQLite authorizer: SELECT/READ only,      │
 │    table allow-list, column block-list,      │
 │    blocked functions, no system tables       │
 │ 3. read-only connection (mode=ro, query_only)│
 │ 4. row cap + wall-clock timeout              │
 │ 5. bound parameters, never string-building   │
 └──────────────────────────────────────────────┘
        ▼
     SQLite database
```

## Why the guardrails don't rely on regex

Most "safe SQL" filters pattern-match keywords like `DROP` or `DELETE`, and they're easy to bypass. SQL Guardian asks the database engine instead. Every statement is compiled with `EXPLAIN` while a SQLite **authorizer callback** inspects each action the engine would perform. Anything other than reading an allowed table is denied before a single row is touched. The connection is also opened read-only at the file level, so even a bug in the policy can't write.

## Features

- **MCP server** with four tools: `list_tables`, `describe_schema`, `run_query`, and `ask_database`. The last one appears when an LLM is configured.
- **Natural language → SQL with self-repair.** If a generated query is rejected, the rejection reason goes back to the model so it can fix its own query, for up to 3 attempts.
- **Policy controls:** a table allow-list, a sensitive-column block-list, a row cap and a timeout.
- **Works with any OpenAI-compatible endpoint** (OpenAI, Databricks model serving, vLLM, Ollama) using only the standard library.
- **21 tests** covering injection attempts, stacked statements, writes, PRAGMA/ATTACH, system tables, timeouts and the repair loop.

## Quick start

```bash
git clone https://github.com/<you>/sql-guardian-mcp && cd sql-guardian-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python examples/seed_demo_db.py          # creates examples/demo.db
pytest -q                                # 21 passed
```

Run the server (the `salaries` table and customer emails stay hidden):

```bash
python -m sql_guardian.server --db examples/demo.db \
  --tables customers,products,orders --block-columns customers.email
```

Turn on natural-language questions by setting an OpenAI-compatible endpoint:

```bash
export OPENAI_API_KEY=...                       # or a dummy value for Ollama
export OPENAI_BASE_URL=http://localhost:11434/v1  # optional, e.g. Ollama
export LLM_MODEL=llama3.1                          # optional
```

### Use it from Claude Desktop or another MCP client

```json
{
  "mcpServers": {
    "sql-guardian": {
      "command": "python",
      "args": ["-m", "sql_guardian.server", "--db", "/abs/path/examples/demo.db",
               "--tables", "customers,products,orders"]
    }
  }
}
```

### Use it as a library

```python
from sql_guardian import SqlGuard, GuardPolicy, GuardError

guard = SqlGuard("examples/demo.db", GuardPolicy(allowed_tables={"orders", "products"}, max_rows=100))
guard.run("SELECT category, SUM(price) FROM products GROUP BY category")
guard.run("SELECT * FROM orders WHERE customer_id = ?", (42,))   # parameters are bound
guard.run("DELETE FROM orders")  # -> GuardError: Query rejected: operation not permitted
```

## Project layout

```
sql_guardian/
  guard.py    # authorizer-based validation + guarded execution
  nl2sql.py   # NL → SQL with validate-and-repair loop
  llm.py      # dependency-free OpenAI-compatible client + scripted test client
  server.py   # MCP server (stdio or streamable HTTP)
examples/seed_demo_db.py
tests/
```

## Roadmap

- Postgres and Databricks SQL backends, using `EXPLAIN` plans plus read-only roles
- Query-cost estimates before execution
- An audit log of every query an agent runs

## License

MIT
