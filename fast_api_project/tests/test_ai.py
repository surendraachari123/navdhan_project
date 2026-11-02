"""
Tests for NavDhan AI. No Oracle or Groq needed: the DB cursor and the LLM
are faked. Run from the project root:

    pip install pytest httpx
    python -m pytest tests -q
"""
import os
import sys
from pathlib import Path

# Dummy settings so config.py's fail-fast validation passes.
for k, v in dict(DB_USER="u", DB_PASSWORD="p", DB_HOST="h", DB_PORT="1521",
                 DB_SERVICE="s", JWT_SECRET_KEY="test-secret", GROQ_API_KEY="test-key",
                 QDRANT_URL=":memory:").items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server_api"))

import pytest
from fastapi.testclient import TestClient

import ai_llm
import ai_qdrant
import ai_service
from ai_sql_guard import UnsafeSQLError, validate_sql, wrap_with_row_limit
from database import get_db
from main import app
import auth

import hashlib
import math
import re


def fake_embed(texts, dim=256):
    """Deterministic bag-of-words embedding so tests need no model download."""
    out = []
    for t in texts:
        v = [0.0] * dim
        for w in re.findall(r"[a-z]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


ai_qdrant.set_embedder(fake_embed)

TABLES = ["investor_table", "mfund_transactions"]
SENSITIVE = ["pan", "mobile", "email"]


# ---------------------------------------------------------------- SQL guard
@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) AS n FROM investor_table",
    "SELECT txn_type, SUM(amount) total FROM mfund_transactions GROUP BY txn_type ORDER BY total DESC;",
    "```sql\nSELECT investor_id FROM investor_table WHERE UPPER(kyc_status)='VERIFIED'\n```",
    "WITH t AS (SELECT investor_id, SUM(amount) a FROM mfund_transactions GROUP BY investor_id) "
    "SELECT i.name, t.a FROM t JOIN investor_table i ON i.investor_id = t.investor_id",
    "SELECT TO_CHAR(txn_date,'YYYY-MM') m, COUNT(*) FROM mfund_transactions GROUP BY TO_CHAR(txn_date,'YYYY-MM')",
    "SELECT SYSDATE FROM dual",
])
def test_guard_accepts_safe_queries(sql):
    assert validate_sql(sql, TABLES, SENSITIVE)


@pytest.mark.parametrize("sql", [
    "DELETE FROM investor_table",
    "UPDATE investor_table SET name='x'",
    "DROP TABLE investor_table",
    "SELECT 1 FROM dual; DROP TABLE investor_table",
    "SELECT * FROM app_users",
    "SELECT password_hash FROM app_users",
    "SELECT * FROM investor_table i JOIN app_users u ON u.email = i.email",
    "SELECT name FROM investor_table -- sneaky",
    "SELECT name FROM investor_table /* x */",
    "SELECT * FROM mfund_transactions@remote_db",
    "SELECT name FROM other_schema.investor_table",
    "SELECT dbms_xmlgen.getxml('select 1 from dual') FROM dual",
    "SELECT name FROM investor_table FOR UPDATE",
    "INSERT INTO investor_table(investor_id) VALUES ('x')",
    "BEGIN NULL; END",
    "",
    "not sql at all",
])
def test_guard_rejects_unsafe_queries(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql, TABLES, SENSITIVE)


def test_guard_blocks_personal_columns_for_non_admin_only():
    with pytest.raises(UnsafeSQLError):
        validate_sql("SELECT name, pan FROM investor_table", TABLES, SENSITIVE)
    with pytest.raises(UnsafeSQLError):
        validate_sql("SELECT name FROM investor_table WHERE email LIKE '%@x.com'", TABLES, SENSITIVE)
    with pytest.raises(UnsafeSQLError):
        validate_sql("SELECT * FROM investor_table", TABLES, SENSITIVE)
    # admin: no blocked columns
    assert validate_sql("SELECT name, pan FROM investor_table", TABLES, [])
    assert validate_sql("SELECT * FROM investor_table", TABLES, [])
    # COUNT(*) is not a column star
    assert validate_sql("SELECT COUNT(*) FROM investor_table", TABLES, SENSITIVE)


def test_row_limit_wrapper():
    wrapped = wrap_with_row_limit("SELECT 1 a FROM dual", 200)
    assert "ROWNUM <= 201" in wrapped and wrapped.startswith("SELECT * FROM (")


# ---------------------------------------------------------------- fakes
class FakeConn:
    call_timeout = 0


class FakeCursor:
    """Answers the few statements the AI service issues."""
    def __init__(self, result_rows=None, result_cols=("n",), fail_first=False):
        self.connection = FakeConn()
        self.executed = []
        self.description = None
        self._rows = []
        self.result_rows = result_rows if result_rows is not None else [(3,)]
        self.result_cols = result_cols
        self.fail_first = fail_first
        self._failed = False

    def execute(self, sql, params=None):
        self.executed.append(sql)
        s = " ".join(sql.split()).upper()
        if s.startswith("SET TRANSACTION"):
            self.description, self._rows = None, []
        elif "USER_TAB_COLUMNS" in s:
            self.description = [("T",), ("C",), ("D",)]
            self._rows = [("investor_table", "investor_id", "VARCHAR2"),
                          ("investor_table", "name", "VARCHAR2"),
                          ("investor_table", "pan", "VARCHAR2"),
                          ("mfund_transactions", "amount", "NUMBER")]
        elif "TOTAL_INVESTORS" in s:  # insights: overview
            self._set(("total_investors", "total_transactions", "investors_with_transactions",
                       "total_inflow", "total_outflow", "first_txn_date", "last_txn_date"),
                      [(100, 500, 90, 1000000.0, 250000.0, "2024-01-01", "2025-06-30")])
        elif "KYC_STATUS, COUNT" in s:
            self._set(("kyc_status", "investors"), [("Verified", 80), ("Pending", 15), ("Rejected", 5)])
        elif "TXN_TYPE, COUNT" in s:
            self._set(("txn_type", "transactions", "total_amount"), [("SIP", 300, 600000.0), ("REDEMPTION", 50, 250000.0)])
        elif "ADD_MONTHS" in s:
            self._set(("month", "transactions", "inflow", "outflow"),
                      [("2025-05", 40, 100000.0, 20000.0), ("2025-06", 50, 150000.0, 30000.0)])
        elif "GROUP BY SCHEME_ID" in s:
            self._set(("scheme_id", "transactions", "net_investment"), [("SCH1", 10, 5000.0)])
        elif "GROUP BY T.INVESTOR_ID" in s:
            self._set(("investor_id", "name", "net_investment", "transactions"), [("I1", "Asha", 9000.0, 4)])
        elif "WITHOUT_VERIFIED_KYC" in s:
            self._set(("investors_transacting_without_verified_kyc", "amount_involved"), [(7, 12345.0)])
        elif "INVESTORS_WITH_NO_TRANSACTIONS" in s:
            self._set(("investors_with_no_transactions",), [(10,)])
        else:  # a wrapped AI query
            if self.fail_first and not self._failed:
                self._failed = True
                import oracledb
                raise oracledb.DatabaseError("ORA-00904: invalid identifier")
            self._set(self.result_cols, self.result_rows)

    def _set(self, cols, rows):
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class FakeLLM:
    """Scripted replies, in order. Records every call."""
    def __init__(self, json_replies, text_reply="There are 3 investors."):
        self.json_replies = list(json_replies)
        self.text_reply = text_reply
        self.json_calls, self.text_calls = [], []

    def chat_json(self, messages, **kw):
        self.json_calls.append(messages)
        return self.json_replies.pop(0)

    def chat(self, messages, **kw):
        self.text_calls.append(messages)
        return self.text_reply


@pytest.fixture(autouse=True)
def _reset_caches():
    ai_service._schema_cache.update(at=0.0, columns=None)
    ai_service._insights_cache.update(at=0.0, data=None)


def _client(cursor):
    app.dependency_overrides[get_db] = lambda: cursor
    return TestClient(app)


def _headers(role="user"):
    return {"Authorization": f"Bearer {auth.create_access_token('tester', role)}"}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- /ai/ask
def test_ask_happy_path(monkeypatch):
    llm = FakeLLM([{"action": "sql", "sql": "SELECT COUNT(*) AS n FROM investor_table;"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    cur = FakeCursor()
    r = _client(cur).post("/ai/ask", json={"question": "how many investors?"}, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "sql" and body["columns"] == ["n"] and body["rows"] == [[3]]
    assert body["sql"] == "SELECT COUNT(*) AS n FROM investor_table"
    assert body["answer"] == "There are 3 investors."
    assert any("ROWNUM <= 201" in s for s in cur.executed)
    assert any(s.startswith("SET TRANSACTION READ ONLY") for s in cur.executed)
    # non-admin: the prompt must not even mention the pan column
    assert "pan VARCHAR2" not in llm.json_calls[0][0]["content"]


def test_admin_sees_sensitive_columns_in_prompt(monkeypatch):
    llm = FakeLLM([{"action": "answer", "reply": "hi"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    _client(FakeCursor()).post("/ai/ask", json={"question": "hello there"}, headers=_headers("admin"))
    assert "pan VARCHAR2" in llm.json_calls[0][0]["content"]


def test_ask_requires_login():
    assert _client(FakeCursor()).post("/ai/ask", json={"question": "how many?"}).status_code == 401


def test_ask_validates_input():
    r = _client(FakeCursor()).post("/ai/ask", json={"question": "x"}, headers=_headers())
    assert r.status_code == 422


def test_unsafe_sql_is_never_executed_and_model_gets_one_retry(monkeypatch):
    llm = FakeLLM([{"action": "sql", "sql": "SELECT * FROM app_users"},
                   {"action": "sql", "sql": "DROP TABLE investor_table"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    cur = FakeCursor()
    r = _client(cur).post("/ai/ask", json={"question": "show me all the users"}, headers=_headers())
    assert r.status_code == 400
    assert len(llm.json_calls) == 2  # original + one correction
    assert not any("APP_USERS" in s.upper() or "DROP" in s.upper() for s in cur.executed)


def test_self_correction_after_db_error(monkeypatch):
    llm = FakeLLM([{"action": "sql", "sql": "SELECT COUNT(*) AS n FROM investor_table"},
                   {"action": "sql", "sql": "SELECT COUNT(*) AS n FROM investor_table WHERE 1=1"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    r = _client(FakeCursor(fail_first=True)).post("/ai/ask", json={"question": "count investors"}, headers=_headers())
    assert r.status_code == 200 and r.json()["rows"] == [[3]]
    assert "ORA-00904" in llm.json_calls[1][-1]["content"]


def test_pan_blocked_for_user_allowed_for_admin(monkeypatch):
    sql = {"action": "sql", "sql": "SELECT name, pan FROM investor_table"}
    monkeypatch.setattr(ai_service, "ai_llm", FakeLLM([sql, sql]))
    r = _client(FakeCursor()).post("/ai/ask", json={"question": "list PAN numbers"}, headers=_headers("user"))
    assert r.status_code == 400 and "personal data" in r.json()["error"]["message"]

    monkeypatch.setattr(ai_service, "ai_llm", FakeLLM([sql]))
    r = _client(FakeCursor(result_cols=("name", "pan"), result_rows=[("A", "ABCDE1234F")])).post(
        "/ai/ask", json={"question": "list PAN numbers"}, headers=_headers("admin"))
    assert r.status_code == 200


def test_truncation_flag(monkeypatch):
    monkeypatch.setattr(ai_service, "ai_llm", FakeLLM([{"action": "sql", "sql": "SELECT investor_id FROM investor_table"}]))
    rows = [(f"I{i}",) for i in range(ai_service.AI_MAX_ROWS + 1)]
    r = _client(FakeCursor(result_rows=rows, result_cols=("investor_id",))).post(
        "/ai/ask", json={"question": "list every investor"}, headers=_headers())
    body = r.json()
    assert body["truncated"] is True and body["row_count"] == ai_service.AI_MAX_ROWS


def test_llm_not_configured_returns_503(monkeypatch):
    monkeypatch.setattr(ai_llm, "GROQ_API_KEY", None)
    r = _client(FakeCursor()).post("/ai/ask", json={"question": "how many investors?"}, headers=_headers())
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["error"]["message"]


# ---------------------------------------------------------------- /ai/chat
def test_chat_follow_up_sees_history_and_previous_sql(monkeypatch):
    llm = FakeLLM([{"action": "sql", "sql": "SELECT COUNT(*) AS n FROM mfund_transactions WHERE txn_type = 'SIP'"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    msgs = [
        {"role": "user", "content": "how many transactions are there?"},
        {"role": "assistant", "content": "There are 500.", "sql": "SELECT COUNT(*) AS n FROM mfund_transactions"},
        {"role": "user", "content": "and only SIPs?"},
    ]
    r = _client(FakeCursor()).post("/ai/chat", json={"messages": msgs}, headers=_headers())
    assert r.status_code == 200
    sent = llm.json_calls[0]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert "SQL used for this answer" in sent[2]["content"]


def test_chat_can_answer_without_sql(monkeypatch):
    monkeypatch.setattr(ai_service, "ai_llm", FakeLLM([{"action": "answer", "reply": "Ask me about investors or transactions."}]))
    cur = FakeCursor()
    r = _client(cur).post("/ai/chat", json={"messages": [{"role": "user", "content": "what can you do?"}]}, headers=_headers())
    body = r.json()
    assert body["mode"] == "answer" and body["sql"] is None
    assert not any("ROWNUM" in s for s in cur.executed)


def test_chat_last_message_must_be_user():
    r = _client(FakeCursor()).post("/ai/chat", json={"messages": [{"role": "assistant", "content": "hi"}]}, headers=_headers())
    assert r.status_code == 422


# ---------------------------------------------------------------- /ai/insights
def test_insights_roles_and_content(monkeypatch):
    narrative = {"headline": "Healthy inflows.", "insights": [{"title": "t", "detail": "d", "tone": "positive"}],
                 "suggested_questions": ["q1"]}
    llm = FakeLLM([narrative, narrative])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    c = _client(FakeCursor())

    assert c.get("/ai/insights", headers=_headers("user")).status_code == 403
    assert c.get("/ai/insights").status_code == 401

    r = c.get("/ai/insights", headers=_headers("manager"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["narrative"]["headline"] == "Healthy inflows."
    m = body["metrics"]
    assert m["overview"]["net_investment"] == 750000.0
    assert m["month_over_month"]["inflow_change_pct"] == 50.0
    assert m["kyc_risk"]["investors_transacting_without_verified_kyc"] == 7
    assert body["cached"] is False

    # second call is served from cache (no extra LLM call)
    assert c.get("/ai/insights", headers=_headers("admin")).json()["cached"] is True
    assert len(llm.json_calls) == 1
    # refresh bypasses it
    assert c.get("/ai/insights?refresh=true", headers=_headers("admin")).json()["cached"] is False


def test_insights_degrade_without_llm(monkeypatch):
    monkeypatch.setattr(ai_llm, "GROQ_API_KEY", None)
    r = _client(FakeCursor()).get("/ai/insights", headers=_headers("admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["narrative"] is None and "GROQ_API_KEY" in body["narrative_error"]
    assert body["metrics"]["overview"]["total_investors"] == 100


def test_status_endpoint():
    r = _client(FakeCursor()).get("/ai/status", headers=_headers("user"))
    body = r.json()
    assert body["configured"] is True and body["can_view_insights"] is False
    assert "pan" in body["restricted_columns"]


# ---------------------------------------------------------------- Qdrant retrieval
SENSITIVE_RE = re.compile(r"\b(pan|mobile|email)\b", re.IGNORECASE)


def test_glossary_documents_and_restricted_text():
    docs = ai_qdrant.build_documents(ai_qdrant.load_glossary())
    types = {d["type"] for d in docs}
    assert {"global_rule", "table", "metric", "relationship", "business_rule",
            "question_pattern", "sql_example", "business_language"} <= types
    investor = next(d for d in docs if d["name"] == "investor_table")
    assert SENSITIVE_RE.search(investor["text"])               # admins see them
    for d in docs:                                              # nobody else does
        assert not SENSITIVE_RE.search(d["text_restricted"]), d["name"]
    # the examples must still be fully usable in restricted mode
    assert any(d["type"] == "sql_example" and "SELECT" in d["text_restricted"] for d in docs)


def test_example_sql_in_glossary_passes_the_guard():
    """Every example SQL we feed the LLM must itself be allowed by the guard."""
    glossary = ai_qdrant.load_glossary()
    examples = glossary["semantic_context"]["sql_generation_examples"]
    assert len(examples) >= 10
    for name, ex in examples.items():
        assert validate_sql(ex["sql"], TABLES, SENSITIVE), name


def test_ingest_and_search_returns_relevant_documents():
    summary = ai_qdrant.ingest()
    assert summary["documents"] > 30
    hits = ai_qdrant.search("how many investors have each KYC status", 6, restricted=True)
    names = [h["name"] for h in hits]
    assert "investors_by_kyc_status" in names or "investor_table" in names
    assert all(not SENSITIVE_RE.search(h["text"]) for h in hits)
    assert ai_qdrant.status()["documents"] == summary["documents"]


def test_ask_uses_qdrant_context_and_reports_sources(monkeypatch):
    llm = FakeLLM([{"action": "sql", "sql": "SELECT COUNT(*) AS n FROM investor_table"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    r = _client(FakeCursor()).post("/ai/ask", json={"question": "how many investors have each kyc status?"},
                                   headers=_headers("user"))
    body = r.json()
    assert body["context"]["source"] == "qdrant"
    assert body["context"]["documents"][0]["name"] == "global_rules"
    assert len(body["context"]["documents"]) > 1
    prompt = llm.json_calls[0][0]["content"]
    assert "BUSINESS CONTEXT" in prompt and "Oracle SQL" in prompt
    assert not SENSITIVE_RE.search(prompt.split("HOW TO RESPOND")[0].replace("personal data", ""))


def test_admin_prompt_includes_sensitive_columns(monkeypatch):
    llm = FakeLLM([{"action": "answer", "reply": "hi"}])
    monkeypatch.setattr(ai_service, "ai_llm", llm)
    _client(FakeCursor()).post("/ai/ask", json={"question": "show investor details"}, headers=_headers("admin"))
    assert SENSITIVE_RE.search(llm.json_calls[0][0]["content"])


def test_falls_back_to_schema_when_qdrant_is_down(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("qdrant unreachable")
    monkeypatch.setattr(ai_qdrant, "search", boom)
    monkeypatch.setattr(ai_service, "ai_llm", FakeLLM([{"action": "sql", "sql": "SELECT COUNT(*) AS n FROM investor_table"}]))
    r = _client(FakeCursor()).post("/ai/ask", json={"question": "how many investors?"}, headers=_headers())
    assert r.status_code == 200
    assert r.json()["context"] == {"source": "schema", "documents": []}


def test_follow_up_retrieval_query_includes_previous_question():
    hist = [{"role": "user", "content": "total SIP amount by scheme"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "and redemptions?"}]
    q = ai_service._retrieval_query(hist)
    assert "SIP amount by scheme" in q and "redemptions" in q


def test_reindex_is_admin_only_and_status_reports_qdrant():
    c = _client(FakeCursor())
    assert c.post("/ai/reindex", headers=_headers("manager")).status_code == 403
    r = c.post("/ai/reindex", headers=_headers("admin"))
    assert r.status_code == 200 and r.json()["documents"] > 30
    st = c.get("/ai/status", headers=_headers("user")).json()
    assert st["qdrant"]["enabled"] is True and st["qdrant"]["indexed"] is True
