"""Optional cloud brain: Google Gemini through the official `google-genai` SDK.

Rules enforced here:
* only used when the user put a key in .env AND allow_cloud is True;
* the key is read from the environment - never stored in config.json or logs;
* timeouts, HTTP errors, safety blocks and quota (429) all return a soft
  `Completion(error=...)` so the assistant can fall back to Ollama;
* Google Search *grounding* is requested when available; if the model ID does
  not support it we retry once without it instead of failing.

Model availability changes over time, so `probe()` asks the API which models
exist rather than assuming.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ai.base_provider import Completion

log = logging.getLogger("jarvis.gemini")

# Stable ids listed in Google's model documentation; probe() checks this key's
# actual available list. Do not guess that a preview/alias remains available.
CANDIDATE_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", temperature: float = 0.4) -> None:
        self._api_key = api_key or ""     # never logged, never written to disk
        self.model = model
        self.temperature = temperature
        self._client = None
        self.last_error: str | None = None
        self.supports_grounding = True
        self.server_models: list[str] = []

    # ---- wiring ------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):  # noqa: ANN202
        if self._client is not None:
            return self._client
        try:
            from google import genai  # type: ignore
        except ImportError:
            self.last_error = "google-genai SDK not installed. Run: pip install google-genai"
            return None
        try:
            from google.genai import types  # type: ignore

            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=8000),  # milliseconds
            )
        except Exception as exc:
            self.last_error = f"GenAI client init failed: {self._mask(str(exc))[:160]}"
            return None
        return self._client

    def probe(self) -> tuple[bool, str]:
        if not self.configured:
            return False, f"No API key in the environment (variable GEMINI_API_KEY). Cloud AI stays off."
        client = self._get_client()
        if client is None:
            return False, self.last_error or "Gemini SDK unavailable."
        try:
            page = client.models.list()
            names: list[str] = []
            for i, m in enumerate(page):
                if i >= 100:
                    break
                raw = getattr(m, "name", "") or ""
                names.append(raw.removeprefix("models/"))
            self.server_models = names
        except Exception as exc:
            msg = self._mask(str(exc))
            self.last_error = _humanise(msg)
            return False, self.last_error
        for candidate in dict.fromkeys([self.model, *CANDIDATE_MODELS]):
            if candidate in names:
                self.model = candidate
                return True, f"Gemini reachable ({self.model}) - free-tier limits still apply."
        return False, f"None of the expected models are available for this key. Found: {', '.join(names[:5]) or 'none'}"

    def models(self) -> list[str]:
        return self.server_models or [self.model]

    def _mask(self, text: str) -> str:
        return text.replace(self._api_key, "[redacted]") if self._api_key else text

    # ---- generation --------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 400,
        timeout: float = 45.0,
        expect_json: bool = False,
        search_grounding: bool = False,
    ) -> Completion:
        client = self._get_client()
        if client is None:
            return Completion(error=self.last_error, provider="gemini")
        t0 = time.monotonic()

        try:
            from google.genai import types  # type: ignore
        except ImportError:
            return Completion(error="google-genai SDK not installed.", provider="gemini")

        system, contents = _split_messages(messages)
        cfg_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "http_options": types.HttpOptions(timeout=int(timeout * 1000)),
        }
        if expect_json:
            cfg_kwargs["response_mime_type"] = "application/json"
        # Search grounding can consume paid quota. Never enable it on normal
        # chat or router calls just because no function tools were supplied.
        grounding = bool(search_grounding and self.supports_grounding and not expect_json)
        if grounding:
            try:
                cfg_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            except AttributeError:
                self.supports_grounding = False
                grounding = False

        last_exc: Exception | None = None
        for model in dict.fromkeys([self.model, *CANDIDATE_MODELS]):
            if self.server_models and model not in self.server_models:
                continue
            last_exc = None
            for attempt in range(2 if grounding else 1):
                try:
                    response = client.models.generate_content(
                        model=model, contents=contents,
                        config=_build_config(types, cfg_kwargs, system),
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    text = self._mask(str(exc))
                    if "not found" in text.lower() or "404" in text:
                        break  # only try another ID when this ID is unavailable
                    if "429" in text or "quota" in text.lower() or "rate limit" in text.lower():
                        self.last_error = "Gemini quota reached; using the local model instead."
                        return Completion(error=self.last_error, provider="gemini", latency=time.monotonic() - t0)
                    if attempt == 0 and grounding and ("google_search" in text.lower() or "unsupported" in text.lower()):
                        self.supports_grounding = False
                        cfg_kwargs.pop("tools", None)
                        grounding = False
                        continue  # retry SAME model without grounding
                    self.last_error = _humanise(text)
                    return Completion(error=self.last_error, provider="gemini", latency=time.monotonic() - t0)
            else:
                continue
            if last_exc is not None and ("not found" in self._mask(str(last_exc)).lower() or "404" in str(last_exc)):
                continue

            try:
                text = getattr(response, "text", None) or ""
            except (ValueError, AttributeError):
                text = ""
            sources = _extract_sources(response)
            if not text.strip():
                block = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
                self.last_error = f"Model returned no text{f' (blocked: {block})' if block else ''}."
                return Completion(error=self.last_error, provider="gemini", latency=time.monotonic() - t0)
            self.model = model
            return Completion(
                text=text.strip(),
                provider="gemini",
                model=model,
                latency=time.monotonic() - t0,
                grounded=grounding and bool(sources),
                sources=sources,
            )
        return Completion(
            error=self.last_error or f"Gemini request failed: {self._mask(str(last_exc))[:160]}",
            provider="gemini",
            latency=time.monotonic() - t0,
        )


def _build_config(types: Any, kwargs: dict[str, Any], system: str | None) -> Any:  # noqa: ANN401
    cfg = types.GenerateContentConfig(**{k: v for k, v in kwargs.items() if k != "model"})
    if system:
        cfg.system_instruction = system
    return cfg


def _split_messages(messages: list[dict[str, str]]) -> tuple[str | None, str]:
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    turns = [m for m in messages if m.get("role") != "system"]
    if not turns:
        return (" ".join(system_parts) or None), ""
    # Simple, reliable shape for 1-part content: send the whole dialogue as text.
    prefix = "\n".join(f"{t.get('role', 'user').upper()}: {t.get('content', '')}" for t in turns[:-1])
    last = turns[-1].get("content", "")
    user = f"{prefix}\n{last}".strip() if prefix else last
    return (" ".join(system_parts) or None), user


def _extract_sources(response: Any) -> list[dict[str, str]]:  # noqa: ANN401
    out: list[dict[str, str]] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        searcher = getattr(candidates[0], "grounding_metadata", None) if candidates else None
        for chunk in getattr(searcher, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web is not None:
                url = getattr(web, "uri", "") or ""
                if url.startswith("https://"):
                    out.append({"title": getattr(web, "title", "") or "", "url": url})
    except Exception:  # metadata layout differs between SDK versions
        log.debug("source extraction failed", exc_info=True)
    return out


def _humanise(text: str) -> str:
    low = text.lower()
    if "api key not valid" in low or "invalid api key" in low or "403" in low:
        return "Gemini rejected the API key. Check GEMINI_API_KEY in .env."
    if "429" in low or "quota" in low:
        return "Gemini free-tier quota hit. The local model will be used until it resets."
    if "timeout" in low or "timed out" in low:
        return "Gemini request timed out (slow connection?)."
    if "503" in low or "unavailable" in low:
        return "Gemini service unavailable right now."
    return f"Gemini error: {text[:220]}"
