import os

import chromadb
from langchain.tools import tool

persist_dir = os.getenv("CHROMA_PERSIST_DIR", "/root/.cache/chroma_data")

chroma_client = chromadb.PersistentClient(path=persist_dir)
collection = chroma_client.get_or_create_collection(name="incident_history")
pdf_collection = chroma_client.get_or_create_collection(name="pdf_documents")

# ── Seeding guard ──────────────────────────────────────────────────────────────
# Only seed when the collection is brand-new (count == 0).
# Without this guard every container restart re-upserts the same two docs,
# wasting a ChromaDB write and masking whether real incidents were ingested.
if collection.count() == 0:
    collection.upsert(
        documents=[
            "March 14: checkout-service latency spiked due to connection pool "
            "leak. Fix: Restart pod and increase pool size to 20.",
            "April 2: auth-service returning 500s on all endpoints. "
            "Fix: Rolled back bad credential deployment from 09:15 UTC.",
        ],
        metadatas=[
            {"incident_id": "INC-014", "service": "checkout-service"},
            {"incident_id": "INC-089", "service": "auth-service"},
        ],
        ids=["id1", "id2"],
    )


@tool
def search_past_incidents(query: str) -> str:
    """
    Search past resolved incidents for similar issues.
    Always call this before starting a new investigation —
    the answer may already be documented.
    """
    results = collection.query(query_texts=[query], n_results=2)
    docs = results.get("documents", [[]])[0]
    if not docs:
        return "No similar past incidents found."
    found = "\n---\n".join(docs)
    return f"Found past incident(s):\n{found}"
