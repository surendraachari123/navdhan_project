# api_web_client

## NavDhan AI page
`ai.html` (sidebar -> NavDhan AI) adds three tabs: Ask your data, Assistant, Insights (admin/manager only).
It needs the backend with the `/ai/*` endpoints deployed (see README_AI.md in the API project), `GROQ_API_KEY` set on the server, and Qdrant reachable (`QDRANT_URL`). Answers show which glossary entries were used; admins get a "Re-index glossary" button.

A floating "Ask AI" button (bottom-right) is also available on every logged-in page. It opens a small chat using `/ai/chat`; the conversation is kept while you move between pages.
