"""Document knowledge base: PDF/text import, chunking, page citations, Q&A."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfReader

from config.settings import Settings
from core.assistant import Jarvis
from core.router import Route
from memory.database import MemoryDB
from memory.document_index import DocumentIndex
from memory.document_loader import DocumentLoadError, DocumentText, load_document
from security.permissions import Capability
from tools.registry import ToolRegistry


def make_pdf(page_texts: list[str]) -> bytes:
    """Build a tiny but valid multi-page PDF with real text (no external deps)."""
    n = len(page_texts)
    objs: list[tuple[int, bytes]] = []
    font_num = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()))
    for i, text in enumerate(page_texts):
        pnum, cnum = 3 + 2 * i, 4 + 2 * i
        objs.append((pnum, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {cnum} 0 R /Resources << /Font << /F1 {font_num} 0 R >> >> >>".encode()))
        stream = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
        objs.append((cnum, f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"))
    objs.append((font_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num, body in objs:
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    maxnum = max(n for n, _ in objs)
    out += f"xref\n0 {maxnum + 1}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, maxnum + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {maxnum + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture()
def docs(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text("The quarterly budget report discusses revenue and costs.", encoding="utf-8")
    (tmp_path / "report.pdf").write_bytes(make_pdf(["Page one discusses the alpha budget line.", "Page two covers the beta rollout plan."]))
    return tmp_path


# --------------------------------------------------------------------- loader
def test_text_file_loads_as_single_page(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello world", encoding="utf-8")
    doc = load_document(p)
    assert doc.kind == "text" and doc.page_count == 1 and "hello world" in doc.text


def test_pdf_loads_with_page_boundaries(tmp_path):
    p = tmp_path / "b.pdf"
    p.write_bytes(make_pdf(["first page text", "second page text"]))
    doc = load_document(p)
    assert doc.kind == "pdf" and doc.page_count == 2
    assert "first page text" in doc.pages[0]
    assert "second page text" in doc.pages[1]
    assert doc.page_text(2) == "second page text"


def test_unsupported_and_empty_files_raise(tmp_path):
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"\x00\x01binary")
    with pytest.raises(DocumentLoadError):
        load_document(bad)
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(DocumentLoadError):
        load_document(empty)


# --------------------------------------------------------------------- index
def test_index_text_stores_page_one(docs):
    db = MemoryDB(docs / "m.db")
    index = DocumentIndex(db)
    assert index.index_file(docs / "notes.md") >= 1
    hits = index.search("budget revenue")
    assert hits and hits[0]["page"] == 1
    assert "notes.md" in hits[0]["path"]


def test_index_pdf_keeps_real_page_numbers(docs):
    db = MemoryDB(docs / "m.db")
    index = DocumentIndex(db)
    assert index.index_file(docs / "report.pdf") >= 2
    alpha = index.search("alpha budget line")
    beta = index.search("beta rollout plan")
    assert alpha and alpha[0]["page"] == 1
    assert beta and beta[0]["page"] == 2
    assert index.citation(beta[0]) == "report.pdf (page 2)"


def test_index_roots_scans_folder_and_skips_unchanged(docs):
    db = MemoryDB(docs / "m.db")
    index = DocumentIndex(db)
    report = index.index_roots([str(docs)])
    assert report["files"] >= 2
    # A second scan with nothing changed should skip everything.
    assert index.index_roots([str(docs)])["files"] == 0


def test_status_reports_documents_and_chunks(docs):
    db = MemoryDB(docs / "m.db")
    index = DocumentIndex(db)
    index.index_roots([str(docs)])
    status = index.status()
    assert status["documents"] == 2 and status["chunks"] >= 2


# --------------------------------------------------------------------- tools
def _registry(tmp_path: Path, index: DocumentIndex) -> ToolRegistry:
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    from security.permissions import PermissionBroker
    from tools.system_tools import ToolContext, register_system_tools

    broker = PermissionBroker([str(tmp_path)])
    broker.grant(Capability.FS_READ)
    reg = ToolRegistry(broker, confirm=lambda *a: False)
    register_system_tools(reg, ToolContext(settings=settings, launcher=None, files=None,
                                           notes=None, memory=None, browser=None, index=index))
    return reg


def test_import_document_tool_indexes_a_file(docs):
    db = MemoryDB(docs / "m.db")
    index = DocumentIndex(db)
    reg = _registry(docs, index)
    result = reg.execute("import_document", {"path": str(docs / "notes.md")})
    assert result.ok and result.data["chunks"] >= 1


def test_import_document_tool_rejects_outside_folder(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    db = MemoryDB(tmp_path / "m.db")
    index = DocumentIndex(db)
    reg = _registry(allowed, index)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = reg.execute("import_document", {"path": str(outside)})
    assert not result.ok and "allowed" in result.error.lower()


def test_ask_documents_tool_returns_citations(docs):
    db = MemoryDB(docs / "m.db")
    index = DocumentIndex(db)
    index.index_file(docs / "report.pdf")
    reg = _registry(docs, index)
    result = reg.execute("ask_documents", {"question": "beta rollout"})
    assert result.ok
    citations = result.data["citations"]
    assert citations and citations[0]["page"] == "2"
    assert "<source>" in result.data["text"]


# --------------------------------------------------------------- assistant path
class _Provider:
    def __init__(self, name, reply):
        self.name = name
        self.reply = reply
        self.calls = 0

    def probe(self):
        return True, "mock"

    def models(self):
        return ["mock"]

    def complete(self, messages, **kwargs):
        self.calls += 1
        from ai.base_provider import Completion

        return Completion(text=self.reply, provider=self.name)


def test_ask_documents_answers_locally_and_cites_pages(docs):
    settings = Settings()
    settings.automation.allowed_dirs = [str(docs)]
    db = MemoryDB(docs / "memory.db")
    index = DocumentIndex(db)
    index.index_file(docs / "report.pdf")
    cloud = _Provider("gemini", "cloud should not see this")
    local = _Provider("ollama", "The beta rollout begins in Q2.")
    jarvis = Jarvis(settings)
    jarvis.boot({"db": db, "index": index, "providers": [cloud, local]})
    jarvis.broker.grant(Capability.FS_READ)
    try:
        route = Route(kind="tool", tool="ask_documents", arguments={"question": "beta rollout"})
        answer = jarvis._act(route, spoken=False, original="beta rollout")
        assert "beta rollout" in answer.lower() or "Q2" in answer
        assert "SOURCES" in answer and "report.pdf" in answer and "page 2" in answer
        assert cloud.calls == 0 and local.calls == 1  # private docs stay local
    finally:
        jarvis.shutdown()


def test_old_database_migrates_to_page_column(tmp_path):
    import sqlite3

    raw = sqlite3.connect(str(tmp_path / "old.db"))
    raw.execute(
        "CREATE TABLE doc_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, "
        "chunk INTEGER NOT NULL, text TEXT NOT NULL, words INTEGER DEFAULT 0, UNIQUE(path, chunk))"
    )
    raw.commit()
    raw.close()
    db = MemoryDB(tmp_path / "old.db")
    cols = {row[1] for row in db._conn.execute("PRAGMA table_info(doc_chunks)")}
    assert "page" in cols
    db.close()


def test_folder_scan_does_not_follow_file_symlink_outside_root(tmp_path):
    root = tmp_path / "selected"
    root.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("Sensitive information outside selected folder", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(private)
    except (OSError, NotImplementedError):
        pytest.skip("File symlinks unavailable on this platform")
    db = MemoryDB(tmp_path / "index.db")
    try:
        index = DocumentIndex(db)
        assert index.index_roots([str(root)])["files"] == 0
        assert index.search("Sensitive information") == []
    finally:
        db.close()
