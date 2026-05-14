# Author: Štefan Gajdošík <xgajdo30@stud.fit.vut.cz>
"""SDP and SIP header helpers for the bridge. All bridge-generated SDP uses Opus 48 kHz (no PCMU)."""

import re

# m= line format: m=audio <port> <proto> <fmt> ... – strip this prefix to get port and rest
M_AUDIO_PREFIX = "m=audio "


def extract_uri_from_from_header(from_header: str) -> str:
    """Extract bare SIP URI from From header (no display name). E.g. '"kori" <sip:kori@[127.0.0.1:5061]>' -> 'sip:kori@[127.0.0.1:5061]'."""
    if not from_header:
        return ""
    s = from_header.strip()
    if "<" in s and ">" in s:
        start = s.index("<") + 1
        end = s.index(">", start)
        return s[start:end].strip()
    return s.split(";")[0].strip()


def parse_tag_from_header(header_value: str) -> str:
    """Extract tag parameter from SIP header (e.g. From: ...;tag=abc -> abc)."""
    if not header_value:
        return "default"
    for part in header_value.split(";"):
        part = part.strip()
        if part.lower().startswith("tag="):
            return part[4:].strip() or "default"
    return "default"


def parse_via_host_port(via_header: str, default_port: int = 5061) -> tuple[str, int]:
    """Parse first Via (e.g. SIP/2.0/UDP 127.0.0.1:5060;branch=...) -> (host, port)."""
    if not via_header:
        return ("127.0.0.1", default_port)
    first = via_header.strip().split(",")[0].strip()
    parts = first.split()
    for i, p in enumerate(parts):
        if ":" in p and not p.startswith("SIP/"):
            host_port = p.split(";")[0].strip()
            if ":" in host_port:
                host, _, port_str = host_port.partition(":")
                try:
                    return (host.strip(), int(port_str.strip()))
                except ValueError:
                    return (host.strip(), default_port)
            return (host_port, default_port)
    return ("127.0.0.1", default_port)


def parse_invite_rtp_addr(sip_message) -> tuple[str, int] | None:
    """Parse INVITE SDP for first c= and m=audio; return (ip, port) or None."""
    raw = getattr(sip_message, "body_str", None) or ""
    return parse_sdp_rtp_endpoint(raw)


def parse_sdp_rtp_endpoint(sdp: str) -> tuple[str, int] | None:
    """Parse raw SDP: first c= IN IP4 and first m=audio port -> (ip, port) or None."""
    if not sdp:
        return None
    ip, port = None, None
    for line in sdp.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith("c=") and ip is None:
            parts = line[2:].strip().split()
            if len(parts) >= 3:
                ip = parts[-1]
        if line.startswith(M_AUDIO_PREFIX) and port is None:
            parts = line[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 1:
                try:
                    port = int(parts[0])
                except ValueError:
                    pass
    if ip and port is not None:
        return (ip, port)
    return None


def replace_sdp_connection_with_host(sdp: str, host: str) -> str:
    """Rewrite address in c= and o= lines to host."""
    if not sdp or not host:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("c=IN IP4 "):
            out.append(f"c=IN IP4 {host}")
        elif s.startswith("o=") and " IN IP4 " in s:
            # o=<user> <sess-id> <sess-ver> IN IP4 <addr>
            idx = s.rfind(" IN IP4 ")
            if idx >= 0:
                out.append(s[: idx + 8] + host)
            else:
                out.append(line)
        else:
            out.append(line)
    return "\r\n".join(out)


def sdp_summary(sdp: str | None, max_lines: int = 20) -> str:
    """Short summary of SDP (c= and m= lines)."""
    if not sdp or not isinstance(sdp, str):
        return "(no SDP)"
    lines = sdp.replace("\r", "\n").strip().split("\n")
    key_lines = [ln.strip() for ln in lines if ln.strip().startswith(("c=", "m="))]
    if not key_lines:
        return "(no c=/m=) " + sdp[:200].replace("\n", " ")
    return " | ".join(key_lines[:10]) + (
        f" ... ({len(key_lines)} c/m lines)" if len(key_lines) > 10 else ""
    )


# Static RTP payload types (RFC 3551). Linphone sends these in m=audio but often omits a=rtpmap
_STATIC_RTPMAP = {
    0: "PCMU/8000",
    3: "GSM/8000",
    8: "PCMA/8000",
    9: "G722/8000/1",
    10: "L16/11025/2",
    11: "L16/8000/1",
    18: "G729/8000",
}


def inject_ice_candidates_into_sdp(sdp: str, candidates: list[str]) -> str:
    """Add a=candidate:... lines to first audio section of SDP (for RTPEngine / Element answer).
    candidates: list of strings from Matrix (candidate:... or already a=candidate:...).
    """
    if not sdp or not candidates:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    in_first_audio = False
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if line.strip().startswith("m=audio "):
            in_first_audio = True
        elif in_first_audio and not inserted:
            if line.strip().startswith("c=IN IP4 "):
                for c in candidates:
                    c = (c.strip() or "").strip()
                    if not c:
                        continue
                    if c.lower().startswith("a=candidate:"):
                        out.append(c)
                    elif c.lower().startswith("candidate:"):
                        out.append("a=" + c)
                    else:
                        out.append("a=candidate:" + c)
                inserted = True
        if line.strip().startswith("m=") and not line.strip().startswith("m=audio "):
            in_first_audio = False
    return "\r\n".join(out)


def _first_audio_payload_type_from_sdp(sdp: str) -> int:
    """From first m=audio line (m=audio <port> <proto> <fmt>...) return first payload type. If none, 96."""
    if not sdp:
        return 96
    for line in sdp.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith(M_AUDIO_PREFIX):
            parts = line[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 3:
                for p in parts[2:]:
                    try:
                        return int(p)
                    except ValueError:
                        continue
            break
    return 96


def _first_audio_port_from_sdp(sdp: str) -> int | None:
    """From first m=audio line in SDP return port (m=audio <port> <proto> <fmt>...). If m=audio 0 or missing, None."""
    if not sdp:
        return None
    for line in sdp.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith(M_AUDIO_PREFIX):
            parts = line[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 1:
                try:
                    port = int(parts[0])
                    return port if port > 0 else None
                except ValueError:
                    return None
            break
    return None
