import hashlib
import pathlib
import re


def normalize_pdf_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 1100, overlap: int = 180) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            for cursor in range(0, len(paragraph), chunk_size - overlap):
                chunks.append(paragraph[cursor:cursor + chunk_size])
            current = ""

    if current:
        chunks.append(current)
    return chunks


def file_sha256(path: pathlib.Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def pdf_document_id(path: pathlib.Path) -> str:
    return file_sha256(path)[:16]


def format_pdf_pages_for_llm(path: pathlib.Path, pages: list[dict], max_chars: int = 10000) -> str:
    body = "\n\n".join(
        f"[Page {page['page']}]\n{page['text']}"
        for page in pages
    )
    combined = (
        f"PDF direct summary context\n"
        f"File: {path.name}\n"
        f"Pages extracted: {len(pages)}\n\n"
        "Write a clear structured summary from the page text below.\n"
        "Use page citations like [p. 2] for important claims whenever possible.\n"
        "Use a format that fits the document type. For most PDFs, include:\n"
        "1. Document overview\n"
        "2. Main points\n"
        "3. Important details, facts, or evidence\n"
        "4. Conclusions or recommendations\n"
        "5. Limitations, risks, or missing information\n"
        "6. Action items or next steps, if any\n"
        "7. Key terms/names/dates, if relevant\n"
        "If the PDF is clearly a job description, use role-focused sections instead: "
        "role overview, responsibilities, required skills, preferred skills, qualifications, "
        "tools/technologies, how to prepare, and fit checklist.\n"
        "Do not force job-description sections on non-JD PDFs.\n"
        "If text is sparse or extraction looks incomplete, say that clearly and recommend OCR.\n\n"
        f"{body}"
    )
    if len(combined) <= max_chars:
        return combined
    return f"{combined[:max_chars]}\n...[truncated to {max_chars} chars]"
