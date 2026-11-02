# NavDhan AI inside the FastAPI project

Same idea as the NavDhan AI platform (business glossary -> Qdrant -> context -> Groq writes SQL -> validated -> executed),
rebuilt for this project's **Oracle** data (`investor_table`, `mfund_transactions`).

```
question -> Qdrant search (navdhan_glossary.yaml) -> context + schema -> Groq -> SQL
         -> SQL guard (SELECT only, allow-listed tables, role checks) -> Oracle (read-only) -> answer + table
```

## Features (page: `web_client/ai.html`, sidebar -> NavDhan AI, plus a floating "Ask AI" button on every page)
| Feature | Endpoint | Who |
|---|---|---|
| Ask your data | `POST /ai/ask` | any logged-in user |
| Assistant (remembers earlier turns) | `POST /ai/chat` | any logged-in user |
| Insights (KPIs, trend, written briefing) | `GET /ai/insights[?refresh=true]` | admin, manager |
| Re-read the glossary into Qdrant | `POST /ai/reindex` | admin |
| Status (AI configured? Qdrant indexed?) | `GET /ai/status` | any logged-in user |

## Setup
1. `pip install -r requirements.txt`  (adds sqlglot, qdrant-client, fastembed, pyyaml)
2. Start Qdrant: `docker run -p 6333:6333 qdrant/qdrant`
   (no Docker? set `QDRANT_URL=:memory:`; on Render use a free Qdrant Cloud cluster and set `QDRANT_URL` + `QDRANT_API_KEY`)
3. Add to `.env` (see `.env.ai.example`): `GROQ_API_KEY`, `QDRANT_URL`
4. Index the glossary: `cd server_api && python ingest_glossary.py`
   (optional - it also indexes itself on the first question; admins can click "Re-index glossary" on the AI page)
5. `uvicorn main:app --reload`, open the frontend, log in, open NavDhan AI.

If Qdrant is down the AI falls back to the plain table schema and keeps working (answers may be less accurate).

## Market Research dashboard
The manager market dashboard, provider rights, Oracle migration, manual refresh, and daily runner are documented in [MARKET_RESEARCH.md](MARKET_RESEARCH.md). Apply `server_api/market_data.sql` before enabling its endpoints.

## Teaching it more
Everything the AI knows about your data is in `server_api/navdhan_glossary.yaml`: table/column meanings, business metrics
(net investment, SIP amount ...), rules, synonyms and **example question -> SQL pairs**. Add an example whenever an answer
is wrong, then re-index. To expose more tables, add them to the glossary and to `AI_ALLOWED_TABLES`.

## Safety
- The LLM only *proposes* SQL; `ai_sql_guard.py` allows one SELECT on allow-listed tables (no comments, DML, DDL, db links, DBMS_/UTL_).
- Runs in a READ ONLY transaction with a DB timeout and a 200-row cap. Login/ETL tables are never visible.
- `pan`, `mobile`, `email` are removed from the prompt and the retrieved glossary text, and rejected in SQL, for non-admin roles.
- Qdrant holds definitions and example SQL only - no row data.
- Every AI query is logged with username, question and SQL.

## Files
New: `ai_router.py`, `ai_service.py`, `ai_sql_guard.py`, `ai_llm.py`, `ai_qdrant.py`, `navdhan_glossary.yaml`, `ingest_glossary.py`, `web_client/ai.html`, `tests/test_ai.py`
Changed: `main.py` (2 lines), `config.py`, `models.py`, `web_client/app.js` + `style.css`, `requirements.txt`

## Tests
`pip install pytest httpx` then `python -m pytest tests -q` (48 tests, no Oracle, Groq or Qdrant server needed).
