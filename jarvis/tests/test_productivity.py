"""Productivity tests: SQLite reminders, recurring tasks, dashboard, routing."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from config.settings import Settings
from core.assistant import Jarvis
from core.router import Router
from memory.database import MemoryDB
from security.permissions import Capability, PermissionBroker
from tools.registry import ToolRegistry
from tools.system_tools import ToolContext, register_system_tools


class _StubRegistry:
    tools: dict = {}

    def schemas(self):
        return []


def _router() -> Router:
    return Router(provider=None, registry=_StubRegistry(), settings=Settings())


# --------------------------------------------------------------------- db layer
def test_reminder_lifecycle_and_due(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    future = time.time() + 3600
    rid = db.add_reminder("buy milk", future)
    assert rid > 0
    assert db.list_reminders()[0]["text"] == "buy milk"
    assert db.due_reminders(now=time.time() + 7200)
    assert not db.due_reminders(now=time.time())  # not due yet
    assert db.complete_reminder(rid)
    assert db.list_reminders() == []
    db.close()


def test_recurring_reminder_reschedules_daily(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    now = time.time()
    rid = db.add_reminder("take meds", now, recurring="daily")
    db.complete_reminder(rid)
    remaining = db.list_reminders()
    assert len(remaining) == 1
    assert remaining[0]["recurring"] == "daily"
    assert remaining[0]["due"] > now
    db.close()


def test_monthly_recurrence_advances_one_month(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    due = datetime(2026, 1, 15, 9, 0).timestamp()
    rid = db.add_reminder("pay rent", due, recurring="monthly")
    db.complete_reminder(rid)
    nxt = db.list_reminders()[0]["due"]
    assert datetime.fromtimestamp(nxt).month == 2
    assert datetime.fromtimestamp(nxt).day == 15
    db.close()


def test_invalid_recurring_rejected(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    with pytest.raises(ValueError):
        db.add_reminder("x", time.time(), recurring="yearly")
    db.close()


def test_task_lifecycle_and_dashboard(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    tid = db.add_task("call mum", recurring="weekly")
    db.add_task("finish report")
    dash = db.task_dashboard()
    assert dash["open_tasks"] == 2 and dash["done_tasks"] == 0
    # Completing a recurring task reschedules it instead of marking it done.
    db.complete_task(tid)
    dash = db.task_dashboard()
    assert dash["open_tasks"] == 2
    assert any(t["recurring"] == "weekly" for t in dash["open"])
    # A one-off task becomes "done" when completed.
    oneoff = db.add_task("one off")
    db.complete_task(oneoff)
    dash = db.task_dashboard()
    assert dash["done_tasks"] == 1
    db.close()


def test_overdue_task_is_flagged(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    db.add_task("late", due=time.time() - 100)
    dash = db.task_dashboard()
    assert len(dash["overdue"]) == 1
    db.close()


def test_startup_summary_mentions_pending_items(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    db.add_reminder("water plants", time.time() - 1)
    db.add_task("submit forms")
    summary = db.startup_summary()
    assert "reminder" in summary and "task" in summary
    db.close()


# --------------------------------------------------------------------- routing
def test_router_maps_reminder_and_task_phrases():
    router = _router()
    remind = router.route("remind me to buy milk")
    assert remind.tool == "add_reminder" and remind.arguments["text"] == "buy milk"
    task = router.route("add a task to call mum")
    assert task.tool == "add_task" and task.arguments["title"] == "call mum"
    dash = router.route("show my tasks")
    assert dash.tool == "task_dashboard"
    reminders = router.route("what are my reminders")
    assert reminders.tool == "list_reminders"


# --------------------------------------------------------------------- tools
def _registry(tmp_path, db) -> ToolRegistry:
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    broker = PermissionBroker([str(tmp_path)])
    broker.grant(Capability.FS_READ)
    broker.grant(Capability.FS_WRITE)
    reg = ToolRegistry(broker, confirm=lambda *a: True)
    register_system_tools(reg, ToolContext(settings=settings, launcher=None, files=None,
                                           notes=None, memory=db, browser=None, index=None))
    return reg


def test_add_reminder_and_dashboard_tools(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    reg = _registry(tmp_path, db)
    res = reg.execute("add_reminder", {"text": "call the bank", "minutes": 30})
    assert res.ok
    dash = reg.execute("task_dashboard", {})
    assert dash.ok and dash.data["open_reminders"] == 1


def test_add_task_tool_stores_title(tmp_path):
    db = MemoryDB(tmp_path / "p.db")
    reg = _registry(tmp_path, db)
    res = reg.execute("add_task", {"title": "review pull request"})
    assert res.ok and res.data["title"] == "review pull request"
    listing = reg.execute("list_tasks", {})
    assert "review pull request" in listing.display


class _FakeProvider:
    name = "fake-local"

    def probe(self):
        return True, "mock"

    def models(self):
        return ["mock"]

    def complete(self, messages, **kwargs):
        from ai.base_provider import Completion

        return Completion(text="ok", provider=self.name)


def test_boot_report_includes_startup_summary(tmp_path):
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    db = MemoryDB(tmp_path / "p.db")
    db.add_task("submit forms")
    jarvis = Jarvis(settings)
    report = jarvis.boot({"db": db, "providers": [_FakeProvider()]})
    assert "startup_summary" in report
    assert "submit forms" in report["startup_summary"]
    jarvis.shutdown()
