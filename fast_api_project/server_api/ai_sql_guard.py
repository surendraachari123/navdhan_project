"""
SQL safety gate for NavDhan AI.

Anything an LLM writes is untrusted input. Before a generated query is
allowed anywhere near Oracle it must pass validate_sql(), which enforces:

  * exactly ONE statement, and it must be a plain SELECT (WITH ... SELECT ok)
  * every table it reads is on the AI_ALLOWED_TABLES allowlist
  * no personal-data columns (PAN / mobile / email) unless the caller is admin
  * no DML/DDL/PL-SQL, no database links, no dangerous built-in packages

The validated query is then wrapped in a ROWNUM cap by wrap_with_row_limit()
so a careless "SELECT * ..." can never stream a whole table to the browser.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from exceptions import AppError


class UnsafeSQLError(AppError):
    """The generated SQL was rejected by the guard. Maps to HTTP 400."""
    status_code = 400
    message = "The generated query was not allowed."


# Words that must never appear in a generated query, even inside something
# that parses as a SELECT (e.g. a function call that runs code).
_FORBIDDEN_WORDS = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|"
    r"execute|exec|call|commit|rollback|savepoint|lock|begin|declare|"
    r"dbms_\w+|utl_\w+|sys\.\w+|dba_\w+|all_\w+|v\$\w+|"
    r"into\s+outfile|for\s+update)\b",
    re.IGNORECASE,
)


def _strip_fences(sql: str) -> str:
    """LLMs often wrap SQL in ```sql fences or add a trailing semicolon."""
    sql = sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";").strip()


def validate_sql(raw_sql: str, allowed_tables: list[str], blocked_columns: list[str]) -> str:
    """Returns the cleaned SQL if it is safe, otherwise raises UnsafeSQLError.

    allowed_tables   - lower-case table names the caller may read.
    blocked_columns  - lower-case column names this caller must NOT touch
                       (empty list for admins).
    """
    sql = _strip_fences(raw_sql or "")
    if not sql:
        raise UnsafeSQLError("The model did not return a query.")

    # A semicolon left in the middle means more than one statement.
    if ";" in sql:
        raise UnsafeSQLError("Only a single SQL statement is allowed.")

    # Comments can hide intent from simple checks - reject them outright.
    if "--" in sql or "/*" in sql:
        raise UnsafeSQLError("SQL comments are not allowed.")

    if _FORBIDDEN_WORDS.search(sql):
        raise UnsafeSQLError("Only read-only SELECT queries are allowed.")

    try:
        statements = sqlglot.parse(sql, read="oracle")
    except ParseError as exc:
        raise UnsafeSQLError(f"The generated query could not be parsed: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeSQLError("Only a single SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise UnsafeSQLError("Only SELECT queries are allowed.")

    # Any write/DDL node buried anywhere in the tree (e.g. inside a CTE).
    forbidden_nodes = (
        exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
        exp.Alter, exp.Command, exp.Into, exp.Lock,
    )
    if tree.find(*forbidden_nodes):
        raise UnsafeSQLError("Only read-only SELECT queries are allowed.")

    # --- table allowlist -------------------------------------------------
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name:
            continue
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError("Schema-qualified tables are not allowed.")
        if name in cte_names or name == "dual":
            continue
        if name not in allowed:
            raise UnsafeSQLError(f"Table '{name}' is not available to the AI assistant.")

    # --- personal-data columns -------------------------------------------
    blocked = {c.lower() for c in blocked_columns}
    if blocked:
        for column in tree.find_all(exp.Column):
            if column.name.lower() in blocked:
                raise UnsafeSQLError(
                    f"Column '{column.name.lower()}' contains personal data and is restricted to admins."
                )
        # SELECT * would silently pull the blocked columns, so make the
        # caller name the columns explicitly.
        # (COUNT(*) is fine - only a star that selects columns is a problem.)
        for star in tree.find_all(exp.Star):
            if not isinstance(star.parent, (exp.Select, exp.Column)):
                continue
            raise UnsafeSQLError(
                "SELECT * is not allowed for your role because some columns are restricted. "
                "Ask for specific columns."
            )

    return sql


def wrap_with_row_limit(sql: str, max_rows: int) -> str:
    """Caps the result set. Fetches one extra row so the caller can tell
    whether the result was truncated. Uses ROWNUM, which works on every
    Oracle version (FETCH FIRST needs 12c+)."""
    return f"SELECT * FROM (\n{sql}\n) WHERE ROWNUM <= {int(max_rows) + 1}"
