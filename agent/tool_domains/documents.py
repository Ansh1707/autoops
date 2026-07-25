from langchain.tools import tool

from agent.memory import collection
from agent.tool_domains.pdf import ingest_pdf_chunks
from agent.tool_domains.shared import resolve_existing_file


@tool
def ingest_document(file_path: str, doc_type: str = "note") -> str:
    """
    Ingest a local .txt, .md, .py, or .pdf file into the ChromaDB knowledge base.
    After ingestion the document is permanently searchable via search_past_incidents.
    """
    try:
        path = resolve_existing_file(file_path)
        if not path.is_file():
            return f"Not a file: {path}"

        if path.suffix.lower() == ".pdf":
            try:
                ingest_result = ingest_pdf_chunks(path)
            except ImportError:
                return "PDF extraction dependency missing. Run: pip install pymupdf pypdf"
            pages_read = ingest_result["pages_read"]
            chunks_written = ingest_result["chunks_written"]
            if chunks_written == 0:
                if ingest_result["skipped_existing"]:
                    return (
                        f"PDF '{path.name}' is already indexed in PDF RAG memory "
                        f"(doc_id: {ingest_result['doc_id']}, pages: {pages_read})."
                    )
                return (
                    f"No text extracted from '{path.name}'. It may be scanned/image-only; "
                    "OCR support is the next upgrade for this file."
                )
            return (
                f"Ingested PDF '{path.name}' into RAG memory: "
                f"{chunks_written} chunks from {pages_read} pages."
            )

        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            return f"No text extracted from {path.name}."

        chunks = []
        chunk_size = 900
        overlap = 120
        cursor = 0
        while cursor < len(text):
            chunks.append(text[cursor:cursor + chunk_size])
            cursor += chunk_size - overlap
            if len(chunks) >= 100:
                break

        ids = [f"{path.resolve()}::{idx}" for idx in range(len(chunks))]
        metadatas = [
            {"source": str(path), "type": doc_type, "chunk": idx}
            for idx in range(len(chunks))
        ]
        collection.upsert(documents=chunks, metadatas=metadatas, ids=ids)
        return f"Ingested {len(chunks)} chunks from '{path.name}' (type: {doc_type})."
    except Exception as exc:
        return f"ingest_document failed: {exc}"
