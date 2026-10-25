"""Public-web-only URL validation and DNS-pinned HTTP fetching.

Ollama's loopback API is separate from this module. Webpages, search hits and
browser redirects must never connect to loopback, LAN, link-local, metadata or
nonstandard service ports. The plain-HTML fetch pins the *validated* address to
the socket so DNS cannot change between the check and the connection.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import zlib
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit


class NetworkPolicyError(ValueError):
    """The requested URL would cross the public-web boundary."""


@dataclass(frozen=True)
class PublicURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _host(url: str) -> tuple[str, int, str, str]:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise NetworkPolicyError("Invalid URL or control character in URL.")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise NetworkPolicyError(f"Malformed URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NetworkPolicyError("Only absolute HTTP(S) URLs are allowed.")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise NetworkPolicyError("URLs with embedded credentials are not allowed.")
    if "%" in parsed.netloc or "\\" in url:
        raise NetworkPolicyError("Encoded hostnames and backslashes are not allowed.")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, UnicodeError) as exc:
        raise NetworkPolicyError(f"Invalid host or port: {exc}") from exc
    if port != (443 if parsed.scheme == "https" else 80):
        raise NetworkPolicyError("Nonstandard web ports are blocked.")
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")):
        raise NetworkPolicyError("Local and metadata hostnames are blocked.")
    return host, port, parsed.scheme, urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def check_url_shape(url: str, allowed_sites: list[str] | None = None) -> str:
    """Cheap, network-free check for UI and allow-list editing."""
    host, _, _, normalised = _host(url)
    if allowed_sites is not None:
        allowed: set[str] = set()
        for site in allowed_sites:
            try:
                site_host, _, _, _ = _host(site if "://" in site else "https://" + site)
            except NetworkPolicyError:
                continue
            allowed.add(site_host)
            if site_host.startswith("www."):
                allowed.add(site_host[4:])
        if not any(host == h or host.endswith("." + h) for h in allowed):
            raise NetworkPolicyError(f"'{host}' is not in the allowed site list.")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not addr.is_global:
            raise NetworkPolicyError("Private, loopback and reserved IP addresses are blocked.")
    return normalised


def validate_public_url(
    url: str, allowed_sites: list[str] | None = None,
    resolver: Callable[..., list] | None = None,
) -> PublicURL:
    normalised = check_url_shape(url, allowed_sites)
    host, port, _, _ = _host(normalised)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    try:
        addresses = (
            (str(literal),) if literal else tuple(
                dict.fromkeys(str(info[4][0]) for info in (resolver or socket.getaddrinfo)(host, port, type=socket.SOCK_STREAM))
            )
        )
    except (OSError, ValueError) as exc:
        raise NetworkPolicyError(f"Host could not be resolved: {host}: {exc}") from exc
    if not addresses:
        raise NetworkPolicyError("Host resolved to no addresses.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise NetworkPolicyError("DNS returned an invalid IP address.") from exc
        if not ip.is_global:
            raise NetworkPolicyError("DNS resolved to a private, loopback or metadata address.")
        if isinstance(ip, ipaddress.IPv6Address):
            embedded = ip.ipv4_mapped or ip.sixtofour or ip.teredo and ip.teredo[1]
            if embedded is not None and not embedded.is_global:
                raise NetworkPolicyError("IPv6 address embeds a private IPv4 address.")
    return PublicURL(normalised, host, port, addresses)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, target: PublicURL, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout, context=ssl.create_default_context())
        self.address = target.addresses[0]

    def connect(self) -> None:
        raw = socket.create_connection((self.address, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, target: PublicURL, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout)
        self.address = target.addresses[0]

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), timeout=self.timeout)


def fetch_public_bytes(
    url: str, *, limit: int = 2_000_000, timeout: float = 15.0,
    allowed_sites: list[str] | None = None, max_redirects: int = 3,
    resolver: Callable[..., list] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:
    """GET public content with a pinned socket; validate EVERY redirect hop."""
    current = url
    for hop in range(max_redirects + 1):
        target = validate_public_url(current, allowed_sites, resolver=resolver)
        parsed = urlsplit(target.url)
        conn = (_PinnedHTTPS if parsed.scheme == "https" else _PinnedHTTP)(target, timeout)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            request_headers = {
                "Host": target.host,
                "User-Agent": "JARVIS/1.0 (local-first research)",
                "Accept": "text/html,application/json,text/plain;q=0.9",
                "Accept-Encoding": "identity",
            }
            request_headers.update(headers or {})  # trusted provider headers, never model text
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise NetworkPolicyError("Redirect has no Location header.")
                next_url = urljoin(target.url, location)
                if parsed.scheme == "https" and urlsplit(next_url).scheme != "https":
                    raise NetworkPolicyError("HTTPS-to-HTTP redirects are blocked.")
                current = next_url
                continue
            if response.status >= 400:
                raise NetworkPolicyError(f"Website returned HTTP {response.status}.")
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise NetworkPolicyError("Website response exceeded the read limit.")
            if response.getheader("Content-Encoding", "").lower() == "gzip":
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw = inflater.decompress(raw, limit + 1)
                if len(raw) > limit or not inflater.eof:
                    raise NetworkPolicyError("Decompressed website response is too large.")
            return raw, response.getheader("Content-Type", ""), target.url
        finally:
            conn.close()
    raise NetworkPolicyError("Too many redirects while loading the page.")