"""
Loads navdhan_glossary.yaml into Qdrant.

    cd server_api
    python ingest_glossary.py

Run it once after Qdrant is up, and again whenever you edit the glossary
(admins can also click "Re-index" on the AI page / call POST /ai/reindex).
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

import ai_qdrant  # noqa: E402

if __name__ == "__main__":
    summary = ai_qdrant.ingest()
    print(f"\nIndexed {summary['documents']} documents into '{summary['collection']}':")
    for doc_type, count in sorted(summary["by_type"].items()):
        print(f"  {doc_type:24s} {count}")
