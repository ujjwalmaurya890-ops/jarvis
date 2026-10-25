"""Command router.

Turns one utterance into one decision:

    control  -> handled locally (stop, mute, sleep model, forget memory)
    tool     -> deterministic local command (open youtube, write a note, ...)
    research -> web search + grounded answer
    chat     -> ask the model

Two layers make it robust:
1. A deterministic regex layer that works with NO model and NO internet, so the
   assistant still controls the PC when Ollama is down.
2. A model layer that must return `{"tool": ..., "arguments": {...}}`. Every
   argument is validated and permission-checked before execution; a webpage can
   never steer it because page text is wrapped in <source> tags and stripped of
   instruction-shaped phrases upstream.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ai.base_provider import AIProvider, Completion, extract_json

log = logging.getLogger("jarvis.router")


@dataclass
class Route:
    kind: str = "chat"                      # chat | tool | control | research | error
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    say: str = ""
    confidence: float = 0.0
    source: str = "model"                   # model | regex | control
    error: str | None = None
    needs_confirmation: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


TIME_WORDS = ("time", "what is the time", "samay")
GREETING = re.compile(r"^\s*(hi|hey|hello|yo|good (morning|afternoon|evening))\b[\s\.,!]*$", re.I)
JARVIS_ASK = re.compile(r"^(jarvis[,\s]*)?(please\s*)?", re.I)


class Router:
    def __init__(self, provider: AIProvider | None, registry: Any, settings: Any, control_words: dict[str, str] | None = None) -> None:
        self.provider = provider
        self.registry = registry
        self.settings = settings
        self.control_words = control_words or {}

    # ---- public ---------------------------------------------------------------
    def route(self, text: str, *, history: list[dict[str, str]] | None = None) -> Route:
        # Every utterance is untrusted: strip control characters and cap the
        # length before it reaches any regex or tool argument.
        from security.input_validation import sanitize_text

        utter = sanitize_text(text, max_len=2000).strip()
        if not utter:
            return Route(kind="control", say="I didn't catch that. Try again?", source="local")

        control = self._match_control(utter)
        if control:
            return control

        deterministic = self._match_tools(utter)
        if deterministic and deterministic.confidence >= 0.75:
            return deterministic

        if not self.provider:
            if deterministic:
                return deterministic
            return Route(
                kind="error",
                say=(
                    "No AI model is available. Start Ollama and run "
                    f"`ollama pull {self.settings.ai.ollama_model}`, or add a Gemini key to .env."
                ),
                error="no provider",
            )

        try:
            model_route = self._ask_model(utter, history or [])
        except Exception as exc:  # never let the router take the app down
            log.exception("routing failed")
            model_route = None
            fallback_reason = str(exc)
        else:
            fallback_reason = ""

        if model_route is not None:
            # If the model was vague but regex was confident, prefer regex.
            if model_route.kind == "chat" and deterministic and deterministic.confidence >= 0.5:
                return deterministic
            return model_route

        if deterministic:
            deterministic.source = "regex-fallback"
            return deterministic
        return Route(
            kind="error",
            say=f"The assistant model did not answer. {fallback_reason or 'Check the provider status panel.'}",
            error=fallback_reason or "model unavailable",
        )

    # ---- layer 1: control words ----------------------------------------------
    def _match_control(self, text: str) -> Route | None:
        low = text.lower().strip(" .!?,")
        if low in {"cancel shutdown", "abort shutdown", "cancel restart", "abort restart"}:
            return Route(kind="tool", tool="cancel_power", source="local", confidence=1.0, needs_confirmation=True)
        table = {
            "stop": ("cancelled", "Okay, stopped."),
            "cancel": ("cancelled", "Cancelled."),
            "shut up": ("mute", "Muted."),
            "quiet": ("mute", "Muted."),
            "mute": ("mute", "Muted."),
            "unmute": ("unmute", "Voice output on."),
            "start talking": ("unmute", "Voice output on."),
            "never mind": ("cancelled", "Never mind, then."),
            "go to sleep": ("unload", "Putting the model to sleep."),
            "what can you do": ("help", ""),
            "help": ("help", ""),
        }
        for word, (action, say) in table.items():
            if low == word or low.startswith(word + " "):
                return Route(kind="control", tool=action, say=say or "On it.", source="local", confidence=1.0, meta={"control": action})
        m = re.fullmatch(r"(?:forget|delete)\s+(?:my\s+|all\s+)?(?:memory|history)(?:\s+please)?", low)
        if m:
            return Route(kind="tool", tool="clear_memory", confidence=1.0, source="local", needs_confirmation=True)
        return None

    # ---- layer 2: deterministic tool patterns ---------------------------------
    def _match_tools(self, text: str) -> Route | None:
        low = JARVIS_ASK.sub("", text.lower().strip(" .!?,"))
        now = datetime.now()

        if low in TIME_WORDS or re.search(r"\b(?:what(?:'s| is) the )?time\b", low):
            return Route("control", say=f"It's {now.strftime('%I:%M %p')}.", source="regex", confidence=1.0, meta={"local": "time"})
        if re.search(r"\b(?:what(?:'s| is) )?(?:the )?date\b|today['’]?s date", low):
            return Route("control", say=f"Today is {now.strftime('%A, %d %B %Y')}.", source="regex", confidence=1.0, meta={"local": "date"})
        if re.search(r"\bweather\b", low):
            return Route("research", arguments={"query": _clean(re.sub(r".*weather", "weather", low)) or "weather today"}, source="regex", confidence=0.8)

        m = re.search(r"\b(?:open|launch|start|run)\s+(?:the\s+)?([a-z0-9 _.\-]{2,40})$", low)
        if m:
            target = m.group(1).strip()
            target = re.sub(r"\b(app|application|program|for me|please)\b", "", target).strip()
            if re.search(r"\b(youtube|google|github|stackoverflow|gmail|drive|maps|website|site|url|http)\b", low) or target.startswith("http"):
                return Route("tool", tool="open_website", arguments={"url": _url_for(target)}, source="regex", confidence=0.9)
            return Route("tool", tool="launch_app", arguments={"name": target}, source="regex", confidence=0.9, needs_confirmation=False)

        m = re.search(r"\b(?:search|google|look)\s+(?:for\s+|up\s+)?(?:the\s+)?web\s+for\s+(.+)$", low) or re.search(r"\bsearch\s+(.+)$", low)
        if m:
            return Route("research", arguments={"query": _clean(m.group(1))}, source="regex", confidence=0.85)

        m = re.search(r"\b(?:make|create|write|add)\s+(?:a\s+)?note\s*(?::|that|saying)?\s*(.*)$", low)
        if m and len(_clean(m.group(1))) > 2:
            body = _clean(m.group(1))
            return Route("tool", tool="write_note", arguments={"title": body[:40].title(), "body": body}, source="regex", confidence=0.9)

        m = re.search(r"\bfind\s+(?:me\s+)?(?:a\s+|some\s+|the\s+)?file[s]?\s+(?:named\s+|called\s+|matching\s+)?(.+)$", low)
        if m:
            return Route("tool", tool="find_files", arguments={"pattern": _clean(m.group(1))}, source="regex", confidence=0.9)

        m = re.search(r"\b(?:read|open|show)\s+(?:me\s+)?(?:the\s+)?file\s+(.+)$", low)
        if m:
            return Route("tool", tool="read_file", arguments={"path": m.group(1).strip().strip('"')}, source="regex", confidence=0.85, needs_confirmation=False)

        m = re.search(r"\bsummar(?:ise|ize|y)\s+(?:this\s+)?(?:page|url|link)\s*(.*)$", low)
        if m and m.group(1).strip():
            return Route("tool", tool="read_url", arguments={"url": m.group(1).strip()}, source="regex", confidence=0.9)
        m = re.search(r"\bsummar(?:ise|ize|y)\s+(?:my\s+)?(?:document|file|notes?)\s+(.+)$", low)
        if m:
            return Route("tool", tool="summarise_document", arguments={"path_or_query": _clean(m.group(1))}, source="regex", confidence=0.85)

        m = re.search(r"\b(?:shut\s?down|turn\s?off|power\s?off|restart|reboot)\b", low)
        if m:
            action = "restart" if "restart" in low or "reboot" in low else "shutdown"
            return Route(
                "tool",
                tool="system_power",
                arguments={"action": action, "delay_seconds": 60},
                source="regex",
                confidence=0.95,
                needs_confirmation=True,
                say="That needs your confirmation.",
            )

        m = re.search(r"\bremember\s+that\s+(.+)$", low)
        if m:
            return Route("tool", tool="remember_fact", arguments={"key": _clean(m.group(1))[:40], "value": _clean(m.group(1))}, source="regex", confidence=0.9)
        m = re.search(r"\bwhat(?:'s| is| do you know about)\s+(?:my\s+)?(.+)$", low)
        if m and re.search(r"\bmy\b|recall|remember", low):
            return Route("tool", tool="recall_fact", arguments={"key": _clean(m.group(1))}, source="regex", confidence=0.7)

        m = re.search(r"\bremind me to\s+(.+)$", low)
        if m:
            return Route("tool", tool="add_reminder", arguments={"text": _clean(m.group(1)), "minutes": 60}, source="regex", confidence=0.9)

        m = re.search(r"\b(?:add|create|make)\s+(?:a\s+)?task\s+(?:that\s+|to\s+)?(.+)$", low)
        if m:
            return Route("tool", tool="add_task", arguments={"title": _clean(m.group(1))[:80]}, source="regex", confidence=0.9)

        if re.search(r"\b(?:what are my|show my|list my)\s+tasks\b", low) or low == "task dashboard":
            return Route("tool", tool="task_dashboard", arguments={}, source="regex", confidence=0.9)

        if re.search(r"\b(?:what are my|show my|list my)\s+reminders\b", low):
            return Route("tool", tool="list_reminders", arguments={}, source="regex", confidence=0.9)

        if re.search(r"\b(?:pause|play)\s+(?:the\s+)?(?:music|media|playback|song)\b", low):
            return Route("tool", tool="control_media", arguments={"action": "play_pause"}, source="regex", confidence=0.85)
        if re.search(r"\bnext\s+track\b", low):
            return Route("tool", tool="control_media", arguments={"action": "next"}, source="regex", confidence=0.85)
        if re.search(r"\b(?:previous|prev)\s+track\b", low):
            return Route("tool", tool="control_media", arguments={"action": "previous"}, source="regex", confidence=0.85)

        m = re.search(r"\bdelete\s+(?:the\s+)?file\s+(?:called\s+|named\s+)?(.+)$", low)
        if m:
            return Route("tool", tool="delete_file", arguments={"path": _clean(m.group(1))}, source="regex", confidence=0.85, needs_confirmation=True)

        m = re.search(r"\b(?:how much|whats the|what is the) ?(?:ram|memory|cpu|disk|battery)\b", low)
        if m:
            return Route("tool", tool="system_stats", arguments={}, source="regex", confidence=0.9)
        return None

    # ---- layer 3: ask the model ------------------------------------------------
    def _ask_model(self, text: str, history: list[dict[str, str]]) -> Route | None:
        lines = []
        for name, tool in (getattr(self.registry, "tools", None) or {}).items():
            params = getattr(tool, "parameters", None) or {}
            rendered = ", ".join(f"{k}={getattr(v, '__name__', str(v))}" for k, v in params.items())
            lines.append(f"- {name}: {rendered or 'no arguments'}")
        schema_hint = "\n".join(lines)
        system = (
            self.settings.ai.system_prompt
            + "\n\nDecide ONE action for the user's request.\n"
            "Reply with JSON only, one of:\n"
            '{"tool":"<name>","arguments":{...},"say":"<optional short spoken line>"}\n'
            '{"action":"chat","reply":"<your answer>"}\n'
            '{"action":"research","query":"<search terms>"}\n\nAvailable tools:\n'
            + schema_hint
            + "\n\nIf the request is dangerous or unclear, choose chat and ask what they want.\n"
            "Never invent a tool name. Never include secrets."
        )
        messages = [{"role": "system", "content": system}, *history[-6:], {"role": "user", "content": text}]
        completion: Completion = self.provider.complete(  # type: ignore[union-attr]
            messages,
            tools=self.registry.schemas(),
            temperature=self.settings.ai.temperature,
            max_tokens=self.settings.ai.max_tokens,
            timeout=self.settings.ai.request_timeout,
            expect_json=True,
        )
        if completion.error:
            log.info("provider error, will use regex: %s", completion.error)
            return None

        # Native tool calls win when the provider supports them.
        if completion.tool_calls:
            call = completion.tool_calls[0]
            return Route(
                "tool",
                tool=call.name,
                arguments=call.arguments,
                confidence=0.9,
                source="model-tools",
                needs_confirmation=call.name in {"system_power"},
                meta={"provider": completion.provider, "model": completion.model},
            )

        data = extract_json(completion.text)
        if not data:
            return Route(
                "chat",
                say=completion.text.strip(),
                source="model-plain",
                confidence=0.5,
                meta={"provider": completion.provider, "model": completion.model, "grounded": completion.grounded, "sources": completion.sources},
            )
        if data.get("tool"):
            name = str(data["tool"])
            if name not in self.registry.tools:
                log.info("model invented tool '%s'; ignoring", name)
                return Route("chat", say=completion.text.strip()[:400], confidence=0.3, source="model-hallucination")
            args = data.get("arguments") or data.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            return Route(
                "tool",
                tool=name,
                arguments=args,
                say=str(data.get("say") or ""),
                confidence=0.8,
                source="model",
                needs_confirmation=name in {"system_power", "run_command"},
                meta={"provider": completion.provider, "model": completion.model},
            )
        action = str(data.get("action") or "chat")
        if action == "research":
            return Route("research", arguments={"query": str(data.get("query") or text)[:200]}, confidence=0.75, source="model")
        return Route(
            "chat",
            say=str(data.get("reply") or data.get("answer") or completion.text)[:1500],
            confidence=0.7,
            source="model",
            meta={"provider": completion.provider, "model": completion.model, "grounded": completion.grounded, "sources": completion.sources},
        )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip(" .,!?\"'")).strip()


def _url_for(target: str) -> str:
    t = _clean(target)
    if t.startswith("http"):
        return t
    known = {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "github": "https://github.com",
        "stackoverflow": "https://stackoverflow.com",
        "gmail": "https://mail.google.com",
        "drive": "https://drive.google.com",
        "maps": "https://maps.google.com",
        "whatsapp": "https://web.whatsapp.com",
    }
    return known.get(t.lower(), f"https://{t.replace(' ', '')}.com" if "." in t else f"https://duckduckgo.com/?q={t.replace(' ', '+')}")
