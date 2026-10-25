"""Permission enforcement tests - the security boundary must not leak."""

from __future__ import annotations

from pathlib import Path

import pytest

from security.permissions import Capability, PermissionBroker


@pytest.fixture()
def sandbox(tmp_path: Path) -> tuple[PermissionBroker, Path]:
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "notes.txt").write_text("hello", encoding="utf-8")
    private = tmp_path / "secrets"
    private.mkdir()
    (private / "tax.txt").write_text("income", encoding="utf-8")
    return PermissionBroker([str(allowed)]), private


def test_default_deny_until_granted(sandbox):
    broker, _ = sandbox
    decision = broker.request(Capability.APP_LAUNCH, summary="launch calc")
    assert not decision.allowed
    assert decision.needs_confirmation


def test_grant_then_allow(sandbox):
    broker, _ = sandbox
    broker.grant(Capability.APP_LAUNCH)
    assert broker.request(Capability.APP_LAUNCH).allowed


def test_path_traversal_outside_root_rejected(sandbox):
    broker, private = sandbox
    inside = broker.allowed_dirs[0] / "notes.txt"
    assert broker.check_path(inside).allowed
    escape = inside.parent.parent / "secrets" / "tax.txt"
    assert not broker.check_path(escape).allowed
    tricky = str(inside) + "/../../secrets/tax.txt"
    assert not broker.check_path(tricky).allowed


def test_write_outside_root_rejected(sandbox):
    broker, private = sandbox
    target = private / "new.txt"
    assert not broker.check_path(target, write=True).allowed


def test_dangerous_capability_always_needs_confirmation(sandbox):
    broker, _ = sandbox
    broker.grant(Capability.POWER)
    decision = broker.request(Capability.POWER, summary="shutdown")
    assert not decision.allowed
    assert decision.needs_confirmation


def test_offline_mode_blocks_network(sandbox):
    broker, _ = sandbox
    broker.grant(Capability.NET_SEARCH)
    assert broker.request(Capability.NET_SEARCH, offline=True).allowed is False


def test_privacy_mode_blocks_cloud(sandbox):
    broker, _ = sandbox
    broker.grant(Capability.CLOUD_AI)
    assert broker.request(Capability.CLOUD_AI, privacy_mode=True).allowed is False


def test_url_allowlist_exact_and_subdomain(sandbox):
    broker, _ = sandbox
    sites = ["https://www.google.com", "https://github.com"]
    assert broker.check_url("https://google.com/search?q=x", sites).allowed
    assert broker.check_url("https://gist.github.com/x", sites).allowed
    assert not broker.check_url("https://evil.com/google.com", sites).allowed
    assert not broker.check_url("file:///C:/Windows/win.ini", sites).allowed
    assert not broker.check_url("https://github.com.evil.io/x", sites).allowed


def test_revoke_stops_access(sandbox):
    broker, _ = sandbox
    broker.grant(Capability.FS_WRITE)
    assert broker.is_granted(Capability.FS_WRITE)
    broker.revoke(Capability.FS_WRITE)
    assert not broker.is_granted(Capability.FS_WRITE)


def test_reset_clears_everything(sandbox):
    broker, _ = sandbox
    for cap in (Capability.APP_LAUNCH, Capability.NET_SEARCH, Capability.FS_READ):
        broker.grant(cap)
    broker.reset()
    assert broker.scopes() == {}
