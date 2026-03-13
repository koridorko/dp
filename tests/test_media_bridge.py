"""Tests for MediaBridge: Opus RTP helpers and bridge lifecycle."""

import os
import socket
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest

from MediaBridge import (
    MediaBridge,
    rtp_build,
    rtp_parse,
    RTP_HEADER_SIZE,
    RTP_PAYLOAD_TYPE_OPUS,
)


# ---------------------------------------------------------------------------
# RTP
# ---------------------------------------------------------------------------

def test_rtp_parse_valid():
    """Parse valid RTP packet returns (payload, pt, seq, ts, ssrc)."""
    payload = b"\x00\x01\x02"
    packet = rtp_build(payload, seq=100, ts=2000, ssrc=0x12345678)
    parsed = rtp_parse(packet)
    assert parsed is not None
    p, pt, seq, ts, ssrc = parsed
    assert p == payload
    assert pt == RTP_PAYLOAD_TYPE_OPUS
    assert seq == 100
    assert ts == 2000
    assert ssrc == 0x12345678


def test_rtp_parse_too_short():
    """Too short data returns None."""
    assert rtp_parse(b"") is None
    assert rtp_parse(b"\x80\x00\x00\x00\x00\x00\x00") is None


def test_rtp_build_header_length():
    """Built RTP packet has 12-byte header + payload."""
    payload = b"hello"
    packet = rtp_build(payload, 0, 0)
    assert len(packet) == RTP_HEADER_SIZE + len(payload)
    assert packet[RTP_HEADER_SIZE:] == payload


# ---------------------------------------------------------------------------
# MediaBridge lifecycle
# ---------------------------------------------------------------------------

def test_media_bridge_get_sip_rtp_bind_addr():
    """get_sip_rtp_bind_addr returns (host, port); 0.0.0.0 -> 127.0.0.1; advertised_host overrides."""
    bridge = MediaBridge(sip_rtp_port=19050, listen_host="0.0.0.0")
    host, port = bridge.get_sip_rtp_bind_addr()
    assert host == "127.0.0.1"
    assert port == 19050
    bridge2 = MediaBridge(sip_rtp_port=19051, listen_host="127.0.0.1")
    h2, p2 = bridge2.get_sip_rtp_bind_addr()
    assert h2 == "127.0.0.1" and p2 == 19051
    bridge3 = MediaBridge(sip_rtp_port=19052, listen_host="0.0.0.0", advertised_host="192.168.1.100")
    h3, p3 = bridge3.get_sip_rtp_bind_addr()
    assert h3 == "192.168.1.100" and p3 == 19052


def test_media_bridge_start_stop():
    """Start binds socket; stop closes it. Sending one RTP packet (Opus PT) does not crash."""
    pytest.importorskip("av", reason="MediaBridge async loop needs av (PyAV)")
    bridge = MediaBridge(sip_rtp_port=19060, listen_host="127.0.0.1")
    bridge.start()
    try:
        # RTP packet with Opus PT=96 (decode may return empty for fake payload; no crash)
        raw = bytes([0x80, 0x60, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01]) + b"\x00" * 40
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(raw, ("127.0.0.1", 19060))
        sock.close()
    finally:
        bridge.stop()
    assert bridge._sock is None
