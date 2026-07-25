import pathlib
import shutil

from langchain.tools import tool

from agent.memory import pdf_collection
from agent.pdf_utils import (
    chunk_text,
    file_sha256,
    format_pdf_pages_for_llm,
    normalize_pdf_text,
    pdf_document_id,
)
from agent.tool_domains.shared import cap_text, resolve_existing_file


def ocr_tooling_status() -> dict:
    tesseract = shutil.which("tesseract")
    pdftoppm = shutil.which("pdftoppm")
    return {
        "available": bool(tesseract and pdftoppm),
        "tesseract": tesseract,
        "pdftoppm": pdftoppm,
    }


def ocr_recommendation() -> str:
    status = ocr_tooling_status()
    if status["available"]:
        return "OCR tooling is available; run an OCR pipeline before summarization."
    missing = [
        name for name in ("tesseract", "pdftoppm")
        if not status.get(name)
    ]
    return (
        "OCR tooling is not fully available. Install Tesseract and Poppler "
        f"({', '.join(missing)} missing) before summarizing scanned PDFs."
    )


def extract_pdf_pages(path: pathlib.Path, max_pages: int | None = None) -> list[dict]:
    """Extract PDF text page-by-page, preferring PyMuPDF and falling back to pypdf."""
    pages: list[dict] = []

    try:
        import fitz

        doc = fitz.open(str(path))
        page_limit = min(max_pages or len(doc), len(doc))
        for idx in range(page_limit):
            page = doc[idx]
            text = normalize_pdf_text(page.get_text("text") or "")
            if text:
                pages.append({"page": idx + 1, "text": text, "extractor": "pymupdf"})
        doc.close()
        if pages:
            return pages
    except Exception:
        pass

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        page_limit = min(max_pages or len(reader.pages), len(reader.pages))
        for idx, page in enumerate(reader.pages[:page_limit]):
            text = normalize_pdf_text(page.extract_text() or "")
            if text:
                pages.append({"page": idx + 1, "text": text, "extractor": "pypdf"})
    except ImportError:
        raise ImportError("Install PyMuPDF or pypdf for PDF extraction.")

    return pages


def pdf_already_indexed(doc_id: str) -> bool:
    existing = pdf_collection.get(where={"doc_id": doc_id}, limit=1)
    return bool(existing.get("ids"))


def ingest_pdf_chunks(path: pathlib.Path, max_pages: int | None = None, force: bool = False) -> dict:
    pages = extract_pdf_pages(path, max_pages=max_pages)
    if not pages:
        return {
            "doc_id": pdf_document_id(path),
            "pages_read": 0,
            "chunks_written": 0,
            "skipped_existing": False,
            "extractor": "none",
        }

    doc_id = pdf_document_id(path)
    if not force and pdf_already_indexed(doc_id):
        return {
            "doc_id": doc_id,
            "pages_read": len(pages),
            "chunks_written": 0,
            "skipped_existing": True,
            "extractor": pages[0].get("extractor", "unknown"),
        }

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []
    total_pages_read = len(pages)

    for page in pages:
        page_no = page["page"]
        for chunk_idx, chunk in enumerate(chunk_text(page["text"])):
            documents.append(f"[Source: {path.name}, page {page_no}]\n{chunk}")
            metadatas.append({
                "source": str(path),
                "filename": path.name,
                "source_basename": path.name,
                "type": "pdf",
                "doc_id": doc_id,
                "page": page_no,
                "chunk": chunk_idx,
                "pages_read": total_pages_read,
                "extractor": page.get("extractor", "unknown"),
            })
            ids.append(f"pdf::{doc_id}::p{page_no}::c{chunk_idx}")

    if documents:
        pdf_collection.upsert(documents=documents, metadatas=metadatas, ids=ids)
    return {
        "doc_id": doc_id,
        "pages_read": len(pages),
        "chunks_written": len(documents),
        "skipped_existing": False,
        "extractor": pages[0].get("extractor", "unknown"),
    }


def query_pdf_chunks(path: pathlib.Path, query: str, n_results: int = 8) -> str:
    doc_id = pdf_document_id(path)
    results = pdf_collection.query(
        query_texts=[query],
        n_results=max(1, min(n_results, 20)),
        where={"doc_id": doc_id},
    )
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    if not docs:
        return ""

    sections = []
    for doc, meta in zip(docs, metas):
        page = meta.get("page", "?") if meta else "?"
        filename = meta.get("filename", path.name) if meta else path.name
        sections.append(f"[Citation: {filename}, page {page}]\n{doc}")
    return "\n\n---\n\n".join(sections)


@tool
def inspect_pdf(file_path: str, max_pages: int = 5) -> str:
    """
    Inspect whether a PDF is text-readable before summarizing.
    Reports extracted page count, approximate text volume, extractor, and OCR need.
    """
    try:
        path = resolve_existing_file(file_path)
        if not path.is_file():
            return f"Not a file: {file_path}"
        if path.suffix.lower() != ".pdf":
            return f"'{path.name}' is not a PDF."

        pages = extract_pdf_pages(path, max_pages=max_pages)
        total_chars = sum(len(page["text"]) for page in pages)
        extractor = pages[0].get("extractor", "none") if pages else "none"
        if not pages or total_chars < 200:
            return (
                "PDF inspection\n"
                f"File: {path.name}\n"
                f"Readable pages sampled: {len(pages)} / {max_pages}\n"
                f"Extracted characters: {total_chars}\n"
                f"Extractor: {extractor}\n"
                "Status: likely scanned/image-only or extraction quality is poor.\n"
                f"Recommended fix: {ocr_recommendation()}"
            )
        return (
            "PDF inspection\n"
            f"File: {path.name}\n"
            f"Readable pages sampled: {len(pages)} / {max_pages}\n"
            f"Extracted characters: {total_chars}\n"
            f"Extractor: {extractor}\n"
            "Status: text extraction looks usable."
        )
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"inspect_pdf failed: {exc}"


@tool
def summarise_pdf(
    file_path: str,
    max_pages: int = 80,
    focus: str = "main ideas, key findings, methodology, results, limitations, and action items",
) -> str:
    """
    Ingest a PDF into ChromaDB and retrieve the most relevant chunks for a grounded summary.
    """
    try:
        path = resolve_existing_file(file_path)

        if not path.exists():
            return (
                f"File not found: {file_path}\n"
                "Check the filename is exact. List the folder first with:\n"
                f"  list_directory('{str(path.parent)}')"
            )
        if not path.is_file():
            return f"Not a file: {file_path}"
        if path.suffix.lower() != ".pdf":
            return (
                f"'{path.name}' is not a PDF file (suffix: {path.suffix}).\n"
                "Use read_file for text files."
            )

        pages = extract_pdf_pages(path, max_pages=max_pages)
        total_chars = sum(len(page["text"]) for page in pages)

        if not pages:
            return (
                f"No text could be extracted from '{path.name}'.\n"
                "The PDF may be scanned (image-only) and requires OCR.\n"
                f"Better solution: {ocr_recommendation()}"
            )

        if len(pages) <= 8 or total_chars <= 15000:
            return format_pdf_pages_for_llm(path, pages)

        ingest_result = ingest_pdf_chunks(path, max_pages=max_pages)
        chunks_written = ingest_result["chunks_written"]

        if chunks_written == 0 and not ingest_result["skipped_existing"]:
            return (
                f"No text could be extracted from '{path.name}'.\n"
                "The PDF may be scanned (image-only) and requires OCR.\n"
                f"Better solution: {ocr_recommendation()}"
            )

        retrieval_query = (
            f"Summarize this PDF with focus on {focus}. "
            "Find abstract, introduction, method, results, conclusion, limitations, and recommendations."
        )
        evidence = query_pdf_chunks(path, retrieval_query, n_results=10)
        if not evidence:
            return f"PDF was ingested ({chunks_written} chunks), but retrieval returned no chunks."

        return cap_text(
            "PDF RAG summary context\n"
            f"File: {path.name}\n"
            f"Document ID: {ingest_result['doc_id']}\n"
            f"Pages read: {ingest_result['pages_read']} | New chunks indexed: {chunks_written} | Already indexed: {ingest_result['skipped_existing']}\n"
            f"Extractor: {ingest_result['extractor']}\n"
            f"Summary focus: {focus}\n\n"
            "Write the final summary with page citations like [p. 3]. "
            "If evidence is missing, say 'Not specified in the retrieved PDF context'.\n\n"
            f"Retrieved chunks:\n\n{evidence}",
            9000,
        )

    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"summarise_pdf failed: {exc}"


@tool
def ask_pdf(file_path: str, question: str, max_pages: int = 80, n_results: int = 8) -> str:
    """
    Answer a specific question about a PDF using RAG retrieval over page-aware chunks.
    """
    try:
        path = resolve_existing_file(file_path)
        if not path.is_file():
            return f"Not a file: {file_path}"
        if path.suffix.lower() != ".pdf":
            return f"'{path.name}' is not a PDF. Use read_file or search_codebase for text files."

        ingest_result = ingest_pdf_chunks(path, max_pages=max_pages)
        chunks_written = ingest_result["chunks_written"]
        if chunks_written == 0 and not ingest_result["skipped_existing"]:
            return (
                f"No text could be extracted from '{path.name}'. "
                f"The PDF may be scanned/image-only and needs OCR. {ocr_recommendation()}"
            )

        evidence = query_pdf_chunks(path, question, n_results=n_results)
        if not evidence:
            return f"PDF was ingested ({chunks_written} chunks), but no relevant chunks were retrieved."

        return cap_text(
            "PDF RAG answer context\n"
            f"File: {path.name}\n"
            f"Document ID: {ingest_result['doc_id']}\n"
            f"Pages read: {ingest_result['pages_read']} | New chunks indexed: {chunks_written} | Already indexed: {ingest_result['skipped_existing']}\n"
            f"Extractor: {ingest_result['extractor']}\n"
            f"Question: {question}\n\n"
            "Answer using only the retrieved page chunks below. Cite page numbers like [p. 3]. "
            "If the answer is not in the retrieved context, say so.\n\n"
            f"Retrieved chunks:\n\n{evidence}",
            9000,
        )
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"ask_pdf failed: {exc}"
