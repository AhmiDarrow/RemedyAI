"""web_fetch SSRF guard."""

from remedy.core.agent_web_tools import _host_is_blocked


def test_blocks_localhost_and_private():
    assert _host_is_blocked("localhost")
    assert _host_is_blocked("127.0.0.1")
    assert _host_is_blocked("10.0.0.1")
    assert _host_is_blocked("192.168.1.1")
    assert _host_is_blocked("169.254.169.254")
    assert _host_is_blocked("metadata.google.internal")


def test_allows_public_literal_ip():
    assert not _host_is_blocked("8.8.8.8")
    assert not _host_is_blocked("1.1.1.1")
