import pathlib

from agent.pdf_utils import (
    chunk_text,
    format_pdf_pages_for_llm,
    normalize_pdf_text,
    pdf_document_id,
)


def test_normalize_pdf_text_cleans_spacing():
    text = "Hello\t\tworld\x00\n\n\nNext   line"

    assert normalize_pdf_text(text) == "Hello world\n\nNext line"


def test_chunk_text_splits_long_paragraph():
    chunks = chunk_text("a" * 250, chunk_size=100, overlap=20)

    assert len(chunks) >= 3
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_pdf_document_id_is_content_stable(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"same-content")

    first = pdf_document_id(pdf_path)
    second = pdf_document_id(pdf_path)

    assert first == second
    assert len(first) == 16


def test_format_pdf_pages_for_llm_requires_general_summary_and_citations():
    output = format_pdf_pages_for_llm(
        pathlib.Path("report.pdf"),
        [{"page": 1, "text": "This report explains market growth."}],
    )

    assert "Document overview" in output
    assert "Use page citations" in output
    assert "[Page 1]" in output
    assert "Do not force job-description sections" in output
