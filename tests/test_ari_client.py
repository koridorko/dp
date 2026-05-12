"""Tests for sip_bridge.ari_client: URL building, channel variables, caller identity (mocked HTTP)."""

import os
import sys
from unittest.mock import patch

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from sip_bridge.ari_client import (_ari_url, get_channel_variable,
                                   resolve_caller_sip_identity)


def test_ari_url_prepends_ari_and_encodes_query():
    url = _ari_url(
        "http://127.0.0.1:8088",
        "/channels/ch-abc/variable",
        {"variable": "CHANNEL(pjsip,remote_uri)"},
    )
    assert url.startswith("http://127.0.0.1:8088/ari/channels/ch-abc/variable?")
    assert "variable=" in url
    assert "CHANNEL" in url


@patch("sip_bridge.ari_client.ari_http")
def test_get_channel_variable_returns_stripped_value(mock_http):
    mock_http.return_value = {"value": "  sip:kori@127.0.0.1  "}
    out = get_channel_variable(
        "http://127.0.0.1:8088", "u", "p", "PJSIP/foo-0001", "CALLERID(num)"
    )
    assert out == "sip:kori@127.0.0.1"
    mock_http.assert_called_once()
    call = mock_http.call_args
    assert call[0][1] == "GET"
    assert "/channels/PJSIP/foo-0001/variable" in call[0][2]
    assert call[1]["query"] == {"variable": "CALLERID(num)"}


@patch("sip_bridge.ari_client.ari_http")
def test_get_channel_variable_none_on_error_or_empty(mock_http):
    mock_http.return_value = None
    assert get_channel_variable("http://x", "u", "p", "c", "X") is None

    mock_http.return_value = {}
    assert get_channel_variable("http://x", "u", "p", "c", "X") is None

    mock_http.return_value = {"value": "   "}
    assert get_channel_variable("http://x", "u", "p", "c", "X") is None


@patch("sip_bridge.ari_client.get_channel_variable")
def test_resolve_caller_sip_identity_fallback_sip_with_domain(mock_gcv):
    def fake(b, u, p, cid, var):
        if var == "CHANNEL(pjsip,remote_uri)":
            return ""
        if var == "CALLERID(num)":
            return "222"
        return None

    mock_gcv.side_effect = fake
    out = resolve_caller_sip_identity(
        "http://127.0.0.1:8088", "u", "pw", "chan-1", "pbx.example.com"
    )
    assert out == "sip:222@pbx.example.com"


@patch("sip_bridge.ari_client.get_channel_variable")
def test_resolve_caller_sip_identity_no_double_sip_prefix(mock_gcv):
    """If CALLERID is already a sip: URI, do not prepend sip: or @domain."""

    def fake(b, u, p, cid, var):
        if var == "CHANNEL(pjsip,remote_uri)":
            return None
        if var == "CALLERID(num)":
            return "sip:user@host"
        return None

    mock_gcv.side_effect = fake
    out = resolve_caller_sip_identity(
        "http://127.0.0.1:8088", "u", "pw", "chan-1", "other.domain"
    )
    assert out == "sip:user@host"


@patch("sip_bridge.ari_client.get_channel_variable")
def test_resolve_caller_sip_identity_bare_number_no_domain(mock_gcv):
    mock_gcv.side_effect = lambda b, u, p, cid, var: (
        None if "remote_uri" in var else "333" if "CALLERID" in var else None
    )
    out = resolve_caller_sip_identity("http://127.0.0.1:8088", "u", "pw", "chan-1", "")
    assert out == "333"
