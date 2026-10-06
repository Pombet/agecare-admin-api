"""Execute immutable PostgreSQL SQL snapshots from Alembic revisions."""

from __future__ import annotations

import re
from pathlib import Path

from alembic import op


SQL_DIRECTORY = Path(__file__).resolve().parents[1] / "alembic" / "sql"


def split_postgresql_statements(script: str) -> list[str]:
    """Split SQL at semicolons outside strings, comments and dollar quotes."""
    statements: list[str] = []
    current: list[str] = []
    i = 0
    quote: str | None = None
    dollar_quote: str | None = None
    line_comment = False
    block_comment_depth = 0
    escape_string = False

    while i < len(script):
        char = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ""

        if line_comment:
            current.append(char)
            if char == "\n":
                line_comment = False
            i += 1
            continue

        if block_comment_depth:
            if char == "/" and nxt == "*":
                current.extend((char, nxt))
                block_comment_depth += 1
                i += 2
            elif char == "*" and nxt == "/":
                current.extend((char, nxt))
                block_comment_depth -= 1
                i += 2
            else:
                current.append(char)
                i += 1
            continue

        if dollar_quote is not None:
            if script.startswith(dollar_quote, i):
                current.append(dollar_quote)
                i += len(dollar_quote)
                dollar_quote = None
            else:
                current.append(char)
                i += 1
            continue

        if quote is not None:
            current.append(char)
            if escape_string and char == "\\" and i + 1 < len(script):
                current.append(script[i + 1])
                i += 2
            elif char == quote:
                if nxt == quote:
                    current.append(nxt)
                    i += 2
                else:
                    quote = None
                    escape_string = False
                    i += 1
            else:
                i += 1
            continue

        if char == "-" and nxt == "-":
            current.extend((char, nxt))
            line_comment = True
            i += 2
            continue
        if char == "/" and nxt == "*":
            current.extend((char, nxt))
            block_comment_depth = 1
            i += 2
            continue
        if char in ("'", '"'):
            quote = char
            if char == "'" and current and current[-1] in ("e", "E"):
                before_e = "".join(current[:-1])
                escape_string = not before_e or not (before_e[-1].isalnum() or before_e[-1] in "_$")
            current.append(char)
            i += 1
            continue
        if char == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", script[i:])
            if match:
                dollar_quote = match.group(0)
                current.append(dollar_quote)
                i += len(dollar_quote)
                continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current.clear()
            i += 1
            continue

        current.append(char)
        i += 1

    remainder = "".join(current).strip()
    if remainder:
        statements.append(remainder)
    return statements


def execute_sql_snapshot(filename: str) -> None:
    """Run each top-level statement on Alembic's current connection."""
    sql_path = SQL_DIRECTORY / filename
    script = sql_path.read_text(encoding="utf-8")
    for statement in split_postgresql_statements(script):
        uncommented = re.sub(r"/\*.*?\*/|--[^\r\n]*", "", statement, flags=re.DOTALL)
        if not uncommented.strip():
            continue
        # Execute raw PostgreSQL SQL through the driver so SQLAlchemy does not
        # parse PostgreSQL JSON literals (for example ``"minimum": 0``) as
        # named bind parameters.
        op.get_bind().exec_driver_sql(statement)
