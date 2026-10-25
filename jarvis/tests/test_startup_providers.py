"""Mocked configuration and provider tests; no cloud key or model required."""

import json
import sys
import types
from types import SimpleNamespace

import requests

from ai.base_provider import Completion
from ai.gemini_provider import GeminiProvider, _extract_sources
from ai.ollama_provider import OllamaProvider
from config import settings as config
from core.assistant import ProviderChain


def test_environment_switches_and_invalid_config(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-not-saved")
    monkeypatch.setenv("ALLOW_CLOUD", "1")
    s = config.Settings.load()
    assert s.ai.allow_cloud and s.gemini_api_key == "fake-test-key-not-saved"
    s.save()
    assert "fake-test-key" not in config.CONFIG_PATH.read_text()
    monkeypatch.setenv("PRIVACY_MODE", "1")
    assert not config.Settings.load().gemini_api_key
    monkeypatch.delenv("PRIVACY_MODE")
    config.CONFIG_PATH.write_text('{"ai":{"ollama_host":"http://evil.example:11434"}}')
    assert config.Settings.load().ai.ollama_host == "http://127.0.0.1:11434"
    assert "Invalid settings" in caplog.text


def test_ollama_connection_and_missing_model(monkeypatch):
    provider = OllamaProvider()

    def offline(*args, **kwargs):
        raise requests.ConnectionError("not running")

    monkeypatch.setattr(provider._session, "get", offline)
    available, msg = provider.probe()
    assert not available and "Ollama" in msg

    class Response:
        status_code = 200

        def json(self):
            return {"models": []}

    monkeypatch.setattr(provider._session, "get", lambda *a, **k: Response())
    available, msg = provider.probe()
    assert not available and "ollama pull qwen2.5:1.5b" in msg


def test_ollama_error_falls_back_to_installed_model(monkeypatch):
    provider = OllamaProvider()
    tried = []

    class Response:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text

        def json(self):
            return {"message": {"content": "Hello from local"}, "eval_count": 8}

    def post(url, *, json, timeout, allow_redirects):
        tried.append(json["model"])
        assert not allow_redirects
        return Response(404 if len(tried) == 1 else 200)

    monkeypatch.setattr(provider._session, "post", post)
    result = provider.complete([{"role": "user", "content": "hi"}])
    assert result.ok and result.used_fallback and tried == ["qwen2.5:1.5b", "llama3.2:1b"]


def _fake_sdk(monkeypatch):
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = SimpleNamespace(
        HttpOptions=lambda timeout: SimpleNamespace(timeout=timeout),
        GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        Tool=lambda **kwargs: SimpleNamespace(**kwargs),
        GoogleSearch=lambda: SimpleNamespace(),
    )
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)


def test_gemini_timeout_units_and_grounding_off_by_default(monkeypatch):
    _fake_sdk(monkeypatch)
    captured = []

    def generate_content(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(text="cloud reply", candidates=[])

    provider = GeminiProvider("fake-key", model="gemini-2.5-flash")
    provider._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    result = provider.complete([{"role": "user", "content": "hi"}], timeout=12)
    assert result.ok and result.text == "cloud reply"
    assert captured[0]["config"].http_options.timeout == 12000
    assert not hasattr(captured[0]["config"], "tools")


def test_gemini_quota_uses_local_provider(monkeypatch):
    _fake_sdk(monkeypatch)

    def quota(**kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota")

    gemini = GeminiProvider("fake-key")
    gemini._client = SimpleNamespace(models=SimpleNamespace(generate_content=quota))

    class Local:
        name = "local"

        def complete(self, messages, **kwargs):
            return Completion(text="local answer", provider="local")

    result = ProviderChain([gemini, Local()]).complete([{"role": "user", "content": "hi"}])
    assert result.ok and result.provider == "local"


def test_gemini_probe_only_reports_models_returned_by_api():
    gemini = GeminiProvider("fake-key", model="not-a-real-model")
    gemini._client = SimpleNamespace(models=SimpleNamespace(list=lambda: [SimpleNamespace(name="models/gemini-2.5-flash")]))
    available, msg = gemini.probe()
    assert available and gemini.model == "gemini-2.5-flash"


def test_grounding_sources_use_candidates_metadata():
    response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
        grounding_chunks=[SimpleNamespace(web=SimpleNamespace(title="Docs", uri="https://example.com/doc"))]
    ))])
    assert _extract_sources(response) == [{"title": "Docs", "url": "https://example.com/doc"}]