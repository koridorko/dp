"""Tests for sip_bridge.sdp_utils: SIP header parsing and SDP helpers (no network)."""

import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from sip_bridge.sdp_utils import (
    extract_uri_from_from_header,
    parse_tag_from_header,
    parse_via_host_port,
    parse_sdp_rtp_endpoint,
    replace_sdp_connection_with_host,
)


def test_extract_uri_from_from_header_angled():
    h = '"kori" <sip:kori@[127.0.0.1:5061]>;tag=abc'
    assert extract_uri_from_from_header(h) == "sip:kori@[127.0.0.1:5061]"


def test_extract_uri_from_from_header_plain_uri():
    assert extract_uri_from_from_header("sip:user@host;tag=x") == "sip:user@host"


def test_extract_uri_from_from_header_empty():
    assert extract_uri_from_from_header("") == ""


def test_parse_tag_from_header():
    assert (
        parse_tag_from_header('sip:a@b;tag=hello')
        == "hello"
    )
    assert parse_tag_from_header("no tag here") == "default"


def test_parse_via_host_port_udp():
    via = "SIP/2.0/UDP 10.0.0.2:5099;branch=z9hG4bK-1"
    host, port = parse_via_host_port(via, default_port=5060)
    assert host == "10.0.0.2"
    assert port == 5099


def test_parse_via_host_port_no_colon_uses_default():
    host, port = parse_via_host_port("", default_port=5061)
    assert host == "127.0.0.1"
    assert port == 5061


def test_parse_sdp_rtp_endpoint():
    sdp = (
        "v=0\r\n"
        "o=- 0 0 IN IP4 192.168.1.1\r\n"
        "s=-\r\n"
        "c=IN IP4 203.0.113.10\r\n"
        "t=0 0\r\n"
        "m=audio 40000 RTP/AVP 0 8\r\n"
    )
    ep = parse_sdp_rtp_endpoint(sdp)
    assert ep == ("203.0.113.10", 40000)


def test_parse_sdp_rtp_endpoint_invalid_returns_none():
    assert parse_sdp_rtp_endpoint("") is None
    assert parse_sdp_rtp_endpoint("v=0") is None


def test_replace_sdp_connection_with_host():
    sdp = (
        "v=0\r\n"
        "o=- 0 0 IN IP4 10.0.0.1\r\n"
        "c=IN IP4 10.0.0.1\r\n"
        "m=audio 10000 RTP/AVP 0\r\n"
    )
    out = replace_sdp_connection_with_host(sdp, "192.168.50.1")
    assert "c=IN IP4 192.168.50.1" in out
    assert "IN IP4 192.168.50.1" in out
    assert "10.0.0.1" not in out
