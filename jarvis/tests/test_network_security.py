"""No real network: mock DNS and socket layer to prove SSRF guards."""

from types import SimpleNamespace

import pytest

from security.network import NetworkPolicyError, check_url_shape, fetch_public_bytes, validate_public_url
from tools.browser import BrowserManager


def public_resolver(host, port, **kwargs):
    return [(2, 1, 0, "", ("93.184.216.34", port))]


@pytest.mark.parametrize("url", [
    "http://localhost", "https://127.0.0.1", "https://[::1]/", "http://169.254.169.254/latest/meta-data/",
    "https://10.8.0.3/x", "http://192.168.0.5", "file:///etc/passwd", "https://api.example.com:8080/x",
    "https://user:pass@google.com/x", "https://metadata.google.internal/x", "https://private.local/x",
    "http://example.com\nHost: metadata", "https://[bad:ipv6",
])
def test_private_or_malformed_urls_blocked(url):
    with pytest.raises(NetworkPolicyError):
        check_url_shape(url)


def test_allowlist_rejects_host_confusion_and_accepts_subdomain():
    sites = ["https://www.google.com", "https://github.com"]
    assert check_url_shape("https://google.com/x", sites) == "https://google.com/x"
    assert check_url_shape("https://gist.github.com/hey", sites).endswith("/hey")
    for bad in ("https://github.com.evil.io/", "https://github.com@evil.com/", "https://evil.com/github.com"):
        with pytest.raises(NetworkPolicyError):
            check_url_shape(bad, sites)


def test_dns_private_answer_blocks_before_connect():
    def private(*args, **kwargs):
        return [(2, 1, 0, "", ("169.254.169.254", 443))]

    with pytest.raises(NetworkPolicyError, match="private"):
        validate_public_url("https://public-looking.example/", resolver=private)


def test_mixed_dns_answers_fail_closed():
    def mixed(*args, **kwargs):
        return [(2, 1, 0, "", ("93.184.216.34", 443)), (2, 1, 0, "", ("127.0.0.1", 443))]

    with pytest.raises(NetworkPolicyError):
        validate_public_url("https://example.com/", resolver=mixed)


def test_redirect_to_private_address_never_connects(monkeypatch):
    connections = []

    class FakeConnection:
        def __init__(self, target, timeout):
            connections.append(target.addresses[0])

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return SimpleNamespace(status=302, getheader=lambda key, default=None: "http://127.0.0.1/private")

        def close(self):
            pass

    monkeypatch.setattr("security.network._PinnedHTTPS", FakeConnection)
    with pytest.raises(NetworkPolicyError):
        fetch_public_bytes("https://example.com/", resolver=public_resolver)
    assert connections == ["93.184.216.34"]


def test_fetch_pins_validated_ip_and_preserves_response_url(monkeypatch):
    connections = []

    class FakeResponse:
        status = 200

        def getheader(self, key, default=""):
            return {"Content-Type": "text/html"}.get(key, default)

        def read(self, limit):
            return b"<html><p>ok</p></html>"

    class FakeConnection:
        def __init__(self, target, timeout):
            connections.append(target)

        def request(self, method, path, headers):
            assert method == "GET" and path == "/doc?q=yes"

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr("security.network._PinnedHTTPS", FakeConnection)
    raw, ctype, url = fetch_public_bytes("https://example.com/doc?q=yes", resolver=public_resolver)
    assert raw.startswith(b"<html>") and ctype == "text/html"
    assert url == "https://example.com/doc?q=yes"
    assert connections[0].addresses == ("93.184.216.34",)


def test_browser_refuses_private_page_before_playwright():
    browser = BrowserManager()
    result = browser.read_page("http://127.0.0.1:8000/admin")
    assert not result.ok and "Blocked URL" in result.error
    assert browser.running is False


def test_browser_start_failure_closes_without_deadlocking(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("playwright.sync_api")

    class Manager:
        def start(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("no chromium"))), stop=lambda: None)

    fake.sync_playwright = Manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    manager = BrowserManager()
    assert manager.start() is False  # pre-fix this deadlocked inside close()
    assert manager.error and not manager.running