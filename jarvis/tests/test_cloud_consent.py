"""Cloud consent and private-document isolation; all providers are mocked."""

from ai.base_provider import Completion
from config.settings import Settings
from core.assistant import Jarvis
from memory.database import MemoryDB
from security.permissions import Capability
from tools.registry import Tool, ToolResult


class Provider:
    def __init__(self, name, reply):
        self.name = name
        self.reply = reply
        self.calls = []

    def probe(self):
        return True, "mock"

    def models(self):
        return ["mock"]

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return Completion(text=self.reply, provider=self.name)


def _assistant(tmp_path, providers):
    settings = Settings()
    settings.ai.provider = "gemini"
    settings.ai.allow_cloud = True
    settings.automation.allowed_dirs = [str(tmp_path)]
    jarvis = Jarvis(settings)
    jarvis.boot({"db": MemoryDB(tmp_path / "memory.db"), "providers": providers})
    return jarvis


def test_cloud_not_called_without_user_approval(tmp_path):
    cloud = Provider("gemini", "cloud")
    local = Provider("ollama", "local")
    jarvis = _assistant(tmp_path, [cloud, local])
    jarvis.request_confirmation = lambda *args: False
    try:
        result = jarvis.provider.complete([{"role": "user", "content": "hello"}])
        assert result.text == "local" and not cloud.calls
        assert not jarvis.broker.is_granted(Capability.CLOUD_AI)
    finally:
        jarvis.shutdown()


def test_cloud_approval_allows_questions_but_not_credentials(tmp_path):
    cloud = Provider("gemini", "cloud")
    local = Provider("ollama", "local")
    jarvis = _assistant(tmp_path, [cloud, local])
    jarvis.request_confirmation = lambda *args: True
    try:
        result = jarvis.provider.complete([{"role": "user", "content": "hello"}])
        assert result.text == "cloud" and jarvis.broker.is_granted(Capability.CLOUD_AI)
        result = jarvis.provider.complete([{"role": "user", "content": "my password is hunter2"}])
        assert result.text == "local" and len(cloud.calls) == 1
    finally:
        jarvis.shutdown()


def test_private_document_summarization_uses_ollama_only(tmp_path):
    cloud = Provider("gemini", "should never see document")
    local = Provider("ollama", "Summary from local model.")
    jarvis = _assistant(tmp_path, [cloud, local])
    jarvis.broker.grant(Capability.FS_READ)
    jarvis.broker.grant(Capability.CLOUD_AI)
    jarvis.registry.register(Tool(
        "summarise_document", "mock file", Capability.FS_READ, {"path_or_query": str},
        lambda path_or_query: ToolResult(ok=True, data={"text": "Private academic notes"}, display="source text"),
    ))
    try:
        answer = jarvis.handle_text("summarise document notes", spoken=False)
        assert "Summary from local model" in answer and "no contents sent to Gemini" in answer
        assert not cloud.calls and len(local.calls) == 1
    finally:
        jarvis.shutdown()