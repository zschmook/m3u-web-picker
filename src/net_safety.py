#!/usr/bin/env python3
"""SSRF guardrails for fetching third-party URLs (logos, artwork).

Provider playlists and sports-data APIs are semi-trusted, easy-to-poison
inputs: a tvg-logo attribute or a team-logo URL from either one is followed
by this server to fetch bytes. Without a check here, a malicious or merely
compromised upstream could point the server at an internal-only address
(cloud metadata, an internal admin panel, a LAN device) and have the
response relayed back through this app's own logo-cache endpoints.

assert_public_http_url() rejects a URL whose host resolves only to
private/loopback/link-local/reserved addresses. open_safely() additionally
re-checks every redirect hop before following it, since a URL that starts
out public can still redirect somewhere internal.

This does not defend against DNS rebinding (re-resolving the same hostname
to a different address between the check and the TCP connect a moment
later) - closing that fully requires pinning the validated IP for the
actual socket connection. That's deliberately out of scope here: the
threat model is a misbehaving data source embedding a bad URL, not an
active network attacker racing DNS responses.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.request
import urllib.parse


class UnsafeUrlError(ValueError):
    """A URL is not an http(s) address that resolves to a public host."""


def _resolves_only_to_public_addresses(hostname: str, port: int) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return True


def assert_public_http_url(url: str) -> None:
    """Raise UnsafeUrlError unless `url` is http(s) and resolves only to public hosts."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("URL must be an absolute http:// or https:// address.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not _resolves_only_to_public_addresses(parsed.hostname, port):
        raise UnsafeUrlError(f"Refusing to fetch a non-public address: {parsed.hostname}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirectHandler)


def open_safely(request: urllib.request.Request, *, timeout: float):
    """Like urllib.request.urlopen(), but validates the URL and every redirect hop."""
    assert_public_http_url(request.full_url)
    return _opener.open(request, timeout=timeout)
