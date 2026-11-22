"""Natural-language -> SQL with a validate-and-repair loop.

The model drafts a query, the guard checks it, and any rejection reason is fed
back to the model so it can fix its own mistake (up to `max_attempts`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .guard import GuardError, QueryResult, SqlGuard
from .llm import LLMClient

SYSTEM_PROMPT = """You translate questions into a single read-only SQLite SELECT query.
Rules:
- Use only the tables and columns in the schema.
- Never modify data. Never use PRAGMA, ATTACH or multiple statements.
- Prefer explicit column lists over SELECT *.
- Reply with the SQL inside one ```sql code block and nothing else."""

_CODE_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str:
    match = _CODE_BLOCK.search(text)
    return (match.group(1) if match else text).strip()


@dataclass
class Attempt:
    sql: str
    error: str | None


@dataclass
class AnswerResult:
    question: str
    result: QueryResult | None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None


class NL2SQL:
    def __init__(self, guard: SqlGuard, llm: LLMClient, max_attempts: int = 3):
        self.guard = guard
        self.llm = llm
        self.max_attempts = max_attempts

    def ask(self, question: str) -> AnswerResult:
        schema = self.guard.describe()
        prompt = f"Schema:\n{schema}\n\nQuestion: {question}"
        out = AnswerResult(question=question, result=None)

        for _ in range(self.max_attempts):
            sql = extract_sql(self.llm.complete(SYSTEM_PROMPT, prompt))
            try:
                out.result = self.guard.run(sql)
                out.attempts.append(Attempt(sql, None))
                return out
            except GuardError as exc:
                out.attempts.append(Attempt(sql, str(exc)))
                prompt += (f"\n\nYour previous query:\n```sql\n{sql}\n```\n"
                           f"was rejected: {exc}\nReturn a corrected query.")
        return out
