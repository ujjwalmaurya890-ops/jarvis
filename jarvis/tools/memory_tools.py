"""Memory and privacy tools: inspect, export, delete, and cloud consent.

Everything here is local-first.  Cloud AI is opt-in: enabling it always goes
through the permission broker, which demands an explicit per-call confirmation,
and no local file or memory is ever uploaded automatically.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from security.permissions import Capability
from tools.registry import ToolResult, ToolRegistry

log = logging.getLogger("jarvis.memory.tools")


def register_memory_tools(reg: ToolRegistry, ctx) -> None:
    db = ctx.memory
    files = ctx.files

    @reg.tool("memory_stats", "Show how much memory JARVIS has stored (turns, facts, summaries, documents).", Capability.FS_READ, {})
    def memory_stats() -> ToolResult:
        if db is None:
            return ToolResult.say("Memory is disabled.")
        stats = db.stats()
        speech = (
            f"{stats.get('turns', 0)} turns, {stats.get('facts', 0)} facts, "
            f"{stats.get('summaries', 0)} summaries, {stats.get('doc_chunks', 0)} document chunks."
        )
        return ToolResult(True, speech=speech, display=str(stats), data=stats)

    @reg.tool("memory_list", "List recent conversation turns and summaries.", Capability.FS_READ, {"limit": int})
    def memory_list(limit: int = 10) -> ToolResult:
        if db is None:
            return ToolResult.say("Memory is disabled.")
        turns = db.recent_turns(limit=max(1, min(int(limit), 100)))
        lines = [f"{t['role']}: {t['text'][:80]}" for t in turns]
        return ToolResult(
            True,
            speech=f"{len(turns)} recent turns.",
            display="\n".join(lines),
            data={"turns": turns, "summaries": db.summaries(limit=5)},
        )

    @reg.tool("memory_export", "Export all memory to a JSON file in the workspace.", Capability.FS_WRITE, {"name": str})
    def memory_export(name: str = "memory") -> ToolResult:
        if db is None:
            return ToolResult.say("Memory is disabled.")
        if files is None:
            return ToolResult.say("The file workspace is not available.")
        from security.input_validation import safe_filename

        payload = {
            "exported_at": time.time(),
            "stats": db.stats(),
            "turns": db.recent_turns(limit=1000),
            "summaries": db.summaries(limit=1000),
            "facts": db.all_facts(),
            "documents": db.indexed_paths(),
        }
        fname = f"memory-{time.strftime('%Y%m%d-%H%M%S')}-{safe_filename(name, 32)}.json"
        path = files.write_text(Path(files.workspace) / fname, json.dumps(payload, indent=2))
        return ToolResult(True, speech=f"Memory exported to {path.name}.", data={"path": str(path)})

    @reg.tool("memory_delete", "Delete stored memory: a turn by id, or clear the conversation/memory.", Capability.FS_WRITE, {"scope": str, "turn_id": int}, dangerous=True)
    def memory_delete(scope: str = "conversation", turn_id: int | None = None) -> ToolResult:
        if db is None:
            return ToolResult.say("Memory is disabled.")
        scope = (scope or "conversation").strip().lower()
        if scope in ("conversation", "convo", "history"):
            db.clear_conversation()
            return ToolResult(True, speech="Conversation cleared.")
        if scope in ("memory", "all-memory"):
            db.clear_memory()
            return ToolResult(True, speech="Memory cleared (notes and grants kept).")
        if scope in ("everything", "all"):
            db.clear_all()
            return ToolResult(True, speech="Everything cleared.")
        if scope in ("turn", "turns") and turn_id is not None:
            if db.delete_turn(int(turn_id)):
                return ToolResult(True, speech="Turn deleted.")
            return ToolResult.fail("No such turn id.")
        return ToolResult.say("Use scope conversation, memory, everything, or turn with turn_id.")

    @reg.tool("cloud_status", "Report whether cloud AI is enabled and what the privacy mode is.", Capability.FS_READ, {})
    def cloud_status() -> ToolResult:
        granted = reg.broker.is_granted(Capability.CLOUD_AI)
        privacy = bool(getattr(ctx.settings, "privacy", None) and ctx.settings.privacy.privacy_mode)
        offline = bool(getattr(ctx.settings, "privacy", None) and ctx.settings.privacy.offline_mode)
        return ToolResult(
            True,
            speech=f"Cloud AI is {enabled_word(granted)}." + (" Privacy mode is on." if privacy else "") + (" Offline mode is on." if offline else ""),
            data={"cloud_ai": granted, "privacy_mode": privacy, "offline_mode": offline},
        )

    @reg.tool("cloud_consent", "Enable or disable cloud AI. Enabling always needs your explicit confirmation.", Capability.FS_READ, {"allow": bool, "reason": str})
    def cloud_consent(allow: bool = False, reason: str = "") -> ToolResult:
        if not allow:
            reg.broker.revoke(Capability.CLOUD_AI)
            return ToolResult(True, speech="Cloud AI disabled. Everything stays local.", data={"cloud_ai": False})
        decision = reg.broker.request(Capability.CLOUD_AI, summary=f"Enable cloud AI ({reason or 'requested'})")
        if decision.needs_confirmation:
            approved = reg.confirm(decision.summary or "Enable cloud AI?", Capability.CLOUD_AI.value)
            if not approved:
                return ToolResult.say("Cloud AI not enabled - consent was not given.")
            reg.broker.grant(Capability.CLOUD_AI)
        return ToolResult(
            True,
            speech="Cloud AI enabled with your consent. Local files are never uploaded automatically.",
            data={"cloud_ai": True},
        )


def enabled_word(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"
