from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

import net_safety


def _fake_addrinfo(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]


class NetSafetyTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        with self.assertRaises(net_safety.UnsafeUrlError):
            net_safety.assert_public_http_url("file:///etc/passwd")

    def test_rejects_url_without_a_host(self):
        with self.assertRaises(net_safety.UnsafeUrlError):
            net_safety.assert_public_http_url("http:///no-host")

    def test_rejects_loopback_link_local_and_private_literals(self):
        for bad_url in (
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://192.168.1.50/logo.png",
            "http://10.0.0.5/logo.png",
        ):
            with self.assertRaises(net_safety.UnsafeUrlError):
                net_safety.assert_public_http_url(bad_url)

    def test_allows_a_hostname_that_resolves_to_a_public_address(self):
        with patch("net_safety.socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34")):
            net_safety.assert_public_http_url("http://example.com/logo.png")

    def test_rejects_a_hostname_that_resolves_to_a_private_address(self):
        with patch("net_safety.socket.getaddrinfo", return_value=_fake_addrinfo("10.0.0.9")):
            with self.assertRaises(net_safety.UnsafeUrlError):
                net_safety.assert_public_http_url("http://sneaky.example/logo.png")

    def test_rejects_when_dns_resolution_fails(self):
        with patch("net_safety.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with self.assertRaises(net_safety.UnsafeUrlError):
                net_safety.assert_public_http_url("http://does-not-resolve.invalid/")

    def test_redirect_handler_revalidates_the_redirect_target(self):
        handler = net_safety._SafeRedirectHandler()
        with patch("net_safety.socket.getaddrinfo", return_value=_fake_addrinfo("169.254.169.254")):
            with self.assertRaises(net_safety.UnsafeUrlError):
                handler.redirect_request(
                    None, None, 302, "Found", {}, "http://169.254.169.254/redirected"
                )


if __name__ == "__main__":
    unittest.main()
