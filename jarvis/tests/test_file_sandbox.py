"""File sandbox, notes and memory tests (tmp_path only, no real user folders)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory.database import MemoryDB, redact
from tools.file_manager import FileManager, PathError
from tools.notes import NotesStore


@pytest.fixture()
def sandbox(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "readme.md").write_text("# Project\nline two", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("do not read", encoding="utf-8")
    workspace = tmp_path / "workspace"
    fm = FileManager([str(allowed)], workspace)
    return fm, allowed, outside, workspace


def test_read_inside_allowed_root(sandbox):
    fm, allowed, _, _ = sandbox
    text, path = fm.read_text(allowed / "readme.md")
    assert "Project" in text and path.name == "readme.md"


def test_read_outside_denied(sandbox):
    fm, _, outside, _ = sandbox
    with pytest.raises(PathError):
        fm.read_text(outside / "private.txt")


def test_traversal_denied(sandbox):
    fm, allowed, outside, _ = sandbox
    with pytest.raises(PathError):
        fm.read_text(str(allowed / ".." / "outside" / "private.txt"))


def test_absolute_path_outside_denied(sandbox):
    fm, *_ = sandbox
    with pytest.raises(PathError):
        fm.read_text(Path(__file__).as_posix())


def test_write_creates_inside_workspace_only(sandbox):
    fm, _, outside, workspace = sandbox
    target = workspace / "notes" / "todo.md"
    fm.write_text(target, "buy milk")
    assert target.read_text(encoding="utf-8") == "buy milk"
    with pytest.raises(PathError):
        fm.write_text(outside / "evil.txt", "nope")


def test_allowed_read_folder_does_not_become_a_write_folder(sandbox):
    fm, allowed, _, workspace = sandbox
    with pytest.raises(PathError, match="workspace only"):
        fm.write_text(allowed / "new.md", "not here")
    saved = fm.write_text(workspace / "todo.txt", "one\n")
    fm.write_text(saved, "two\n", append=True)
    assert saved.read_text(encoding="utf-8") == "one\ntwo\n"


def test_symlink_to_outside_is_not_found_or_read(sandbox):
    fm, allowed, outside, _ = sandbox
    link = allowed / "leak.txt"
    try:
        link.symlink_to(outside / "private.txt")
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable on this filesystem")
    assert not any(hit.path.name == "leak.txt" for hit in fm.search("leak"))
    with pytest.raises(PathError):
        fm.read_text(link)


def test_secret_filename_is_not_read_even_inside_allowed_folder(sandbox):
    fm, allowed, _, _ = sandbox
    (allowed / ".env").write_text("API_KEY=not-for-a-model")
    with pytest.raises(PathError, match="protected"):
        fm.read_text(allowed / ".env")


def test_search_never_walks_the_disk(sandbox):
    fm, allowed, _, _ = sandbox
    hits = fm.search("readme")
    assert hits and hits[0].path.name == "readme.md"
    assert all(str(allowed) in str(h.path) for h in hits)


def test_binary_and_oversized_files_refused(sandbox):
    fm, allowed, _, _ = sandbox
    blob = allowed / "blob.bin"
    blob.write_bytes(b"\x00\x01\x02" * 3000)
    with pytest.raises(PathError):
        fm.read_text(blob)


def test_grep_finds_matching_line(sandbox):
    fm, allowed, _, _ = sandbox
    rows = fm.grep("line two")
    assert rows and rows[0]["line"] == 2


def test_notes_lifecycle(sandbox):
    fm, _, _, workspace = sandbox
    notes = NotesStore(workspace)
    note = notes.create("Shopping", "milk and bread", tags=["home"])
    assert note.path.exists() and note.path.suffix == ".md"
    assert notes.find("shopping")[0].title == "Shopping"
    notes.append(note.path, "eggs too")
    assert "eggs too" in note.path.read_text(encoding="utf-8")
    trashed = notes.delete(note.path)
    assert trashed.exists() and not note.path.exists()  # soft delete, never silent


def test_memory_roundtrip_and_redaction(tmp_path: Path):
    db = MemoryDB(tmp_path / "test.db", retention_days=30)
    db.add_turn("s1", "user", "my password is hunter2secret")
    turns = db.recent_turns(5)
    assert "hunter2secret" not in turns[-1]["text"]
    db.remember("favourite language", "python")
    assert db.recall("favourite language") == "python"
    db.set_meta("last_run", {"ok": True})
    assert db.get_meta("last_run")["ok"] is True
    db.index_document(str(tmp_path / "a.md"), ["chunk about python decorators and more words"])
    hits = db.query_docs("python decorators")
    assert hits and "python" in hits[0]["text"].lower()
    stats = db.stats()
    assert stats["turns"] >= 1
    db.clear_conversation()
    assert db.stats()["turns"] == 0
    db.close()


def test_redact_hits_common_secrets():
    assert "hunter2" not in redact("password: hunter2")
    assert "hunter2" not in redact("my password is hunter2secret")
    assert "AIzaSy" not in redact("use AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456 for it")
    assert "4111" not in redact("card 4111 1111 1111 1111")
    assert "@" not in redact("email me at rahul@example.com")
    assert redact("schedule my 8am lab") == "schedule my 8am lab"


def test_permission_persistence_roundtrip(tmp_path: Path):
    db = MemoryDB(tmp_path / "p.db")
    db.save_permissions({"apps.launch": "permanent", "fs.read": "permanent"})
    from security.permissions import Capability, PermissionBroker

    broker = PermissionBroker([tmp_path], store=db)
    assert broker.is_granted(Capability.APP_LAUNCH)
    broker.revoke(Capability.APP_LAUNCH)          # persists through the store
    reloaded = PermissionBroker([tmp_path], store=db)
    assert not reloaded.is_granted(Capability.APP_LAUNCH)
    assert reloaded.is_granted(Capability.FS_READ)
    db.close()


def test_session_grants_do_not_survive_restart(tmp_path: Path):
    from security.permissions import Capability, PermissionBroker

    db = MemoryDB(tmp_path / "sessions.db")
    broker = PermissionBroker([str(tmp_path)], store=db)
    broker.grant(Capability.APP_LAUNCH, "session")
    assert broker.is_granted(Capability.APP_LAUNCH)
    assert not PermissionBroker([str(tmp_path)], store=db).is_granted(Capability.APP_LAUNCH)
    db.close()


def test_clear_memory_removes_facts_but_keeps_permissions(tmp_path: Path):
    db = MemoryDB(tmp_path / "forget.db")
    db.add_turn("s", "user", "please remember this")
    db.remember("name", "Alice")
    db.index_document("doc.txt", ["some indexed words here"])
    db.save_permissions({"fs.read": "permanent"})
    db.clear_memory()
    assert not db.recent_turns() and not db.all_facts() and not db.indexed_paths()
    assert db.get_permissions() == [("fs.read", "permanent")]
    db.close()
