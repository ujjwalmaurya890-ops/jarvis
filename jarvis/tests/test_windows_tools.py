"""Windows tools tests: media keys and safe destructive file operations."""

from __future__ import annotations

from config.settings import Settings
from core.router import Router
from security.permissions import Capability, PermissionBroker
from tools.file_manager import FileManager, PathError
from tools.media_control import VK_MEDIA_NEXT, VK_MEDIA_PLAY_PAUSE, media_action
from tools.registry import ToolRegistry
from tools.system_tools import ToolContext, register_system_tools


class _StubRegistry:
    tools: dict = {}

    def schemas(self):
        return []


# --------------------------------------------------------------------- media
def test_media_action_maps_aliases_and_presses_correct_vk(monkeypatch):
    import tools.media_control as mc

    monkeypatch.setattr(mc.platform, "system", lambda: "Windows")
    seen = []
    monkeypatch.setattr(mc, "_press", lambda vk: seen.append(vk) or True)
    result = media_action("play")
    assert result.ok and result.action == "play_pause" and seen == [VK_MEDIA_PLAY_PAUSE]
    seen.clear()
    result = media_action("next track")
    assert result.ok and seen == [VK_MEDIA_NEXT]


def test_media_action_rejects_unknown_action():
    result = media_action("rewind")
    assert not result.ok and "Unknown media action" in result.message


def test_media_action_reports_non_windows(monkeypatch):
    import tools.media_control as mc

    monkeypatch.setattr(mc.platform, "system", lambda: "Linux")
    result = media_action("next")
    assert not result.ok and "Windows" in result.message


def test_media_action_handles_press_failure(monkeypatch):
    import tools.media_control as mc

    monkeypatch.setattr(mc.platform, "system", lambda: "Windows")

    def boom(vk):
        raise OSError("denied")

    result = media_action("stop", _press_fn=boom)
    assert not result.ok and "failed" in result.message


# --------------------------------------------------------------------- delete
def _fm(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    workspace = tmp_path / "workspace"
    return FileManager([str(allowed)], workspace), allowed


def test_delete_is_soft_by_default(tmp_path):
    fm, allowed = _fm(tmp_path)
    f = allowed / "report.txt"
    f.write_text("data", encoding="utf-8")
    target = fm.delete(f)
    assert not f.exists()
    assert target.exists() and ".trash" in str(target)


def test_delete_permanent_removes_file(tmp_path):
    fm, allowed = _fm(tmp_path)
    f = allowed / "old.txt"
    f.write_text("data", encoding="utf-8")
    fm.delete(f, permanent=True)
    assert not f.exists()


def test_delete_directory_is_refused(tmp_path):
    fm, allowed = _fm(tmp_path)
    d = allowed / "folder"
    d.mkdir()
    try:
        fm.delete(d)
    except PathError as exc:
        assert "directory" in str(exc).lower()
    else:
        assert False, "Expected a refusal to delete a directory"


def test_delete_outside_allowed_folder_is_denied(tmp_path):
    fm, allowed = _fm(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        fm.delete(outside)
    except PathError:
        pass
    else:
        assert False, "Expected a sandbox refusal"


# --------------------------------------------------------------------- routing
def test_router_maps_media_and_delete_phrases():
    router = Router(provider=None, registry=_StubRegistry(), settings=Settings())
    pause = router.route("pause the music")
    assert pause.tool == "control_media" and pause.arguments["action"] == "play_pause"
    nxt = router.route("next track")
    assert nxt.arguments["action"] == "next"
    dele = router.route("delete file old report.txt")
    assert dele.tool == "delete_file" and dele.needs_confirmation


# --------------------------------------------------------------------- tools
def _registry(tmp_path, files) -> ToolRegistry:
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    broker = PermissionBroker([str(tmp_path)])
    broker.grant(Capability.FS_READ)
    broker.grant(Capability.FS_WRITE)
    broker.grant(Capability.MEDIA_CONTROL)
    reg = ToolRegistry(broker, confirm=lambda *a: True)
    register_system_tools(reg, ToolContext(settings=settings, launcher=None, files=files,
                                           notes=None, memory=None, browser=None, index=None))
    return reg


def test_control_media_tool_succeeds_when_key_sent(tmp_path, monkeypatch):
    import tools.media_control as mc

    monkeypatch.setattr(mc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(mc, "_press", lambda vk: True)
    reg = _registry(tmp_path, None)
    result = reg.execute("control_media", {"action": "next"})
    assert result.ok and result.data["action"] == "next"


def test_delete_file_tool_soft_deletes(tmp_path):
    fm, allowed = _fm(tmp_path)
    f = allowed / "draft.txt"
    f.write_text("draft", encoding="utf-8")
    reg = _registry(tmp_path, fm)
    result = reg.execute("delete_file", {"path": str(f)})
    assert result.ok and not f.exists()


def test_delete_file_tool_refuses_outside_folder(tmp_path):
    fm, allowed = _fm(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    reg = _registry(tmp_path, fm)
    result = reg.execute("delete_file", {"path": str(outside)})
    assert not result.ok
