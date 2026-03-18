"""Tests for HTTP transport with Bearer token auth."""

import pytest

from head.transport.http import HTTPTransport


class TestHTTPTransport:
    """Unit tests for HTTPTransport."""

    def test_get_base_url(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="ccast_abc123")
        assert t.get_base_url() == "https://10.0.1.5:9100"

    def test_get_auth_headers(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="ccast_abc123")
        assert t.get_auth_headers() == {"Authorization": "Bearer ccast_abc123"}

    def test_peer_id(self):
        t = HTTPTransport(peer_id="my-gpu", address="10.0.1.5:9100", token="tok")
        assert t.peer_id == "my-gpu"

    def test_is_alive_before_connect(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="tok")
        assert t.is_alive() is False

    @pytest.mark.asyncio
    async def test_connect_creates_session(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="tok")
        await t.connect()
        assert t.is_alive() is True
        await t.close()

    @pytest.mark.asyncio
    async def test_close_marks_not_alive(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="tok")
        await t.connect()
        assert t.is_alive() is True
        await t.close()
        assert t.is_alive() is False

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="tok")
        await t.connect()
        await t.close()
        await t.close()  # should not raise
        assert t.is_alive() is False

    def test_tls_fingerprint_stored(self):
        t = HTTPTransport(
            peer_id="gpu",
            address="10.0.1.5:9100",
            token="tok",
            tls_fingerprint="sha256:abc123",
        )
        assert t._tls_fingerprint == "sha256:abc123"

    def test_verify_tls_default_false(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="tok")
        assert t._verify_tls is False

    def test_verify_tls_explicit_true(self):
        t = HTTPTransport(peer_id="gpu", address="10.0.1.5:9100", token="tok", verify_tls=True)
        assert t._verify_tls is True
