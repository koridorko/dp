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
    # No angle brackets: take the part before first ; (tag, etc.)
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
    """Parse first Via (e.g. SIP/2.0/UDP 127.0.0.1:5060;branch=...) -> (host, port).
    Default 5061 = Kamailio listen port so BYE from bridge (Matrix hangup) reaches Kamailio and gets forwarded to Linphone.
    """
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
    """Rewrite address in c= and o= lines to host (issue #659: Linphone must get the address where RTPEngine is listening)."""
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


def sanitize_rtpengine_answer_sdp_for_linphone(sdp: str) -> str:
    """Fix SDP from RTPEngine when it returns broken SDP after 'List of codecs empty': duplicate a=rtpmap for same PT (e.g. 96 opus and 96 PCMU), or empty m= line. Keep first rtpmap per PT; set m=audio to list those PTs."""
    if not sdp:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # First audio section: collect port, proto, and first a=rtpmap per PT (in order)
    first_audio_port = None
    first_audio_proto = None
    rtpmap_first = {}
    rtpmap_order = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith(M_AUDIO_PREFIX):
            parts = s[len(M_AUDIO_PREFIX) :].strip().split()
            # m=audio <port> <proto> <fmt> [fmt...] – port=parts[0], proto=parts[1], payload types=parts[2:]
            if len(parts) >= 2 and first_audio_port is None:
                first_audio_port = parts[0]
                first_audio_proto = (
                    parts[1]
                    if len(parts) >= 2 and not parts[1].isdigit()
                    else "RTP/AVP"
                )
                i += 1
                while i < len(lines):
                    ln = lines[i]
                    sl = ln.strip()
                    if sl.startswith("m="):
                        break
                    if sl.startswith("a=rtpmap:"):
                        m = re.match(r"a=rtpmap:(\d+)\s+(.+)", sl)
                        if m:
                            pt = int(m.group(1))
                            if pt not in rtpmap_first:
                                rtpmap_first[pt] = ln.strip()
                                rtpmap_order.append(pt)
                    i += 1
                break
        i += 1

    if first_audio_port is None or first_audio_port == "0":
        return sdp

    # SDP m= line must have at least one format (RFC 4566). Only use PTs 0–127 (RTPEngine may echo port as PT).
    valid_pts = [p for p in rtpmap_order if 0 <= p <= 127]
    if not valid_pts:
        valid_pts = [96]
        rtpmap_first[96] = "a=rtpmap:96 opus/48000/2"

    out = []
    in_first_audio = False
    seen_pt = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith(M_AUDIO_PREFIX):
            if not in_first_audio and first_audio_port is not None:
                in_first_audio = True
                out.append(
                    f"m=audio {first_audio_port} {first_audio_proto} "
                    + " ".join(str(p) for p in valid_pts)
                )
                for pt in valid_pts:
                    if pt in rtpmap_first and pt not in seen_pt:
                        out.append(rtpmap_first[pt])
                        seen_pt.add(pt)
            else:
                out.append(line)
            i += 1
            continue
        if in_first_audio and s.startswith("a=rtpmap:"):
            m = re.match(r"a=rtpmap:(\d+)\s+", s)
            if m:
                pt = int(m.group(1))
                if pt in seen_pt or not (0 <= pt <= 127):
                    i += 1
                    continue
                seen_pt.add(pt)
                if pt in rtpmap_first:
                    out.append(rtpmap_first[pt])
            i += 1
            continue
        if in_first_audio and s.startswith("m="):
            in_first_audio = False
        out.append(line)
        i += 1
    return "\r\n".join(out)


def make_sip_ok_sdp(bind_host: str, bind_port: int) -> str:
    """Build minimal SIP 200 OK SDP (Opus 48 kHz) so Linphone sends RTP to (bind_host, bind_port)."""
    return (
        f"v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_host}\r\n"
        f"s=-\r\n"
        f"c=IN IP4 {bind_host}\r\n"
        f"t=0 0\r\n"
        f"m=audio {bind_port} RTP/AVP 96\r\n"
        f"a=rtpmap:96 opus/48000/2\r\n"
        f"a=ptime:20\r\n"
    )


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


# Static RTP payload types (RFC 3551). Linphone sends these in m=audio but often omits a=rtpmap; RTPEngine needs explicit rtpmap to avoid "unknown codec".
_STATIC_RTPMAP = {
    0: "PCMU/8000",
    3: "GSM/8000",
    8: "PCMA/8000",
    9: "G722/8000/1",
    10: "L16/11025/2",
    11: "L16/8000/1",
    18: "G729/8000",
}


def ensure_linphone_offer_has_rtpmap(sdp: str) -> str:
    """Add a=rtpmap for every PT in m=audio when missing so RTPEngine recognizes codec on SIP leg (avoids 'unknown codec', no audio). Includes 0/8 (PCMU/PCMA) and dynamic >=96."""
    if not sdp:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith(M_AUDIO_PREFIX):
            out.append(line)
            parts = s[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 3:
                to_add = []
                for pt_str in parts[2:]:
                    try:
                        pt = int(pt_str)
                    except ValueError:
                        continue
                    if any(f"a=rtpmap:{pt} " in ln for ln in (out + to_add)):
                        continue
                    if pt in _STATIC_RTPMAP:
                        to_add.append(f"a=rtpmap:{pt} {_STATIC_RTPMAP[pt]}")
                    elif pt == 101:
                        to_add.append("a=rtpmap:101 telephone-event/8000")
                    elif pt >= 96:
                        to_add.append(f"a=rtpmap:{pt} opus/48000/2")
                for a in to_add:
                    out.append(a)
            continue
        out.append(line)
    return "\r\n".join(out)


def fix_rtpengine_sdp_line_glue(sdp: str) -> str:
    """Fix glued lines from RTPEngine (e.g. tmmbr + a=rtpmap -> tmmbra=rtpmap)."""
    if not sdp:
        return sdp
    # RTPEngine sometimes returns one line instead of two: a=rtcp-fb:* ccm tmmbr + a=rtpmap:96 ...
    return sdp.replace("tmmbra=rtpmap:", "tmmbr\r\na=rtpmap:")


def ensure_offer_has_amid(sdp: str) -> str:
    """Add a=mid:0 to first audio section if SDP has no a=mid (for Element/Firefox/Chrome)."""
    if not sdp or "a=mid" in sdp:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    added = False
    for i, line in enumerate(lines):
        out.append(line)
        if not added and line.strip().startswith("m=audio "):
            out.append("a=mid:0")
            added = True
    return "\r\n".join(out) if out else sdp


def parse_ice_candidates_from_sdp(sdp: str) -> list[dict]:
    """Extract a=candidate lines from SDP into format for Matrix m.call.candidates.
    Returns list of dicts with keys candidate, sdpMid, sdpMLineIndex (0-based m= section index).
    """
    if not sdp:
        return []
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    out = []
    mline_index = -1
    for line in lines:
        line = line.strip()
        if line.startswith("m="):
            mline_index += 1
        if line.startswith("a=candidate:"):
            # Value is the full string after "a=" (Matrix wants SDP 'a' line without "a=" prefix)
            cand = line[2:].strip()
            if not cand:
                continue
            out.append(
                {
                    "candidate": cand,
                    "sdpMid": "0",
                    "sdpMLineIndex": mline_index if mline_index >= 0 else 0,
                }
            )
    return out


def make_host_candidate_from_sdp(sdp: str) -> list[dict]:
    """If SDP has no a=candidate, build one host candidate from c= and first m=audio (for Matrix)."""
    if not sdp or "a=candidate" in sdp:
        return []
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
    if ip and port is not None and ip != "0.0.0.0":
        # ICE host candidate format (simplified): foundation 1 udp priority ip port typ host
        cand = f"candidate:0 1 UDP 2130706431 {ip} {port} typ host generation 0"
        return [{"candidate": cand, "sdpMid": "0", "sdpMLineIndex": 0}]
    return []


def parse_first_candidate_ip_port(
    candidates: list[str], component_rtp: int = 1
) -> tuple[str, int] | None:
    """From first candidate (SDP format candidate:foundation component protocol priority ip port typ ...) return (ip, port).
    component_rtp=1 means RTP (default). Returns None if no valid candidate or invalid format.
    """
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if c.lower().startswith("a=candidate:"):
            c = c[12:].strip()
        elif c.lower().startswith("candidate:"):
            c = c[10:].strip()
        parts = c.split()
        # candidate: foundation component protocol priority ip port typ type [generation 0]
        if len(parts) >= 6:
            try:
                comp = int(parts[1])
                if comp != component_rtp:
                    continue
                return (parts[4], int(parts[5]))
            except (ValueError, IndexError):
                continue
    return None


def apply_answerer_address_in_sdp(
    sdp: str, ip: str, port: int, payload_type: int = 96
) -> str:
    """Replace in answer SDP c=0.0.0.0 and m=audio 0 with answerer address (ip, port). Used when we have ICE candidates from Element.
    When forcing PT 96 for RTPEngine, we must send a=rtpmap:96 opus/48000/2 (our offer had 96=Opus), not 96=PCMU from Element's a=rtpmap:0.
    """
    if not sdp or not ip or port <= 0:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("c=IN IP4 "):
            out.append(f"c=IN IP4 {ip}")
        elif s.startswith(M_AUDIO_PREFIX):
            parts = s[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 3:
                out.append(f"m=audio {port} {parts[1]} {payload_type}")
            else:
                out.append(f"m=audio {port} RTP/SAVPF {payload_type}")
        elif s.startswith("a=rtpmap:0 ") and payload_type != 0:
            if payload_type == 96:
                out.append("a=rtpmap:96 opus/48000/2")
            else:
                out.append(f"a=rtpmap:{payload_type} " + s[11:].strip())
        else:
            out.append(line)
    return "\r\n".join(out)


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


def strip_amid_from_answer_for_rtpengine(sdp: str) -> str:
    """Remove a=mid from answer SDP so RTPEngine does not report 'media ID when no media ID was offered'."""
    if not sdp:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = [ln for ln in lines if not ln.strip().startswith("a=mid")]
    return "\r\n".join(out)


def set_answer_first_audio_port_from_offer(sdp: str, offer_sdp: str) -> str:
    """Set first m=audio port in answer to the port from our offer. RTPEngine expects answerer to use the offered port; ICE candidates carry the real address."""
    if not sdp or not offer_sdp:
        return sdp
    offer_port = _first_audio_port_from_sdp(offer_sdp)
    if offer_port is None:
        return sdp
    pt = _first_audio_payload_type_from_sdp(offer_sdp)
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    done = False
    for line in lines:
        s = line.strip()
        if not done and s.startswith(M_AUDIO_PREFIX):
            parts = s[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 2:
                out.append(f"m=audio {offer_port} {' '.join(parts[1:])}")
            else:
                out.append(f"m=audio {offer_port} RTP/SAVPF {pt}")
            done = True
        else:
            out.append(line)
    return "\r\n".join(out)


def dedupe_ice_candidates_in_sdp(sdp: str) -> str:
    """Keep first occurrence of each a=candidate line (by full value). Avoids RTPEngine 'Priority collision between candidate pairs ... ICE will likely fail'."""
    if not sdp:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    seen: set[str] = set()
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("a=candidate:"):
            key = s[12:].strip()
            if key in seen:
                continue
            seen.add(key)
        out.append(line)
    return "\r\n".join(out)


def ensure_answer_has_rtpmap_for_rtpengine(
    sdp: str, pt_from_offer: int | None = None
) -> str:
    """For each PT in answer's first m=audio that has no a=rtpmap, add it. Avoids 'List of codecs empty' in RTPEngine.
    m=audio is <port> <proto> <fmt> [fmt...]. If m= has no PT (len<3), add PT 96 and a=rtpmap:96.
    """
    if not sdp:
        return sdp
    lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    has_rtpmap = {
        int(m.group(1))
        for m in (re.match(r"a=rtpmap:(\d+)\s+", ln) for ln in lines)
        if m
    }
    out = []
    first_audio_done = False
    for line in lines:
        s = line.strip()
        if s.startswith(M_AUDIO_PREFIX) and not first_audio_done:
            first_audio_done = True
            parts = s[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) < 3:
                # No payload type – RTPEngine will say "List of codecs empty". Append PT 96 and rtpmap.
                out.append(line.rstrip() + " 96")
                if 96 not in has_rtpmap:
                    out.append("a=rtpmap:96 opus/48000/2")
            else:
                out.append(line)
                for pt_str in parts[2:]:
                    try:
                        pt = int(pt_str)
                    except ValueError:
                        continue
                    if pt < 96 or pt in has_rtpmap:
                        continue
                    if pt == 101:
                        out.append("a=rtpmap:101 telephone-event/8000")
                    else:
                        out.append(f"a=rtpmap:{pt} opus/48000/2")
            continue
        out.append(line)
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


def build_minimal_answer_sdp_for_rtpengine(
    placeholder_port: int = 9,
    placeholder_ip: str = "127.0.0.1",
    offer_sdp: str | None = None,
) -> str:
    """Minimal answer SDP for RTPEngine (no WebRTC attributes). PT from offer. Used when Element
    sends placeholder m=audio 0 – we send a whole new clean SDP instead of editing theirs.
    """
    pt = _first_audio_payload_type_from_sdp(offer_sdp) if offer_sdp else 96
    return (
        f"v=0\r\n"
        f"o=- 0 0 IN IP4 {placeholder_ip}\r\n"
        f"s=-\r\n"
        f"t=0 0\r\n"
        f"m=audio {placeholder_port} RTP/SAVPF {pt}\r\n"
        f"c=IN IP4 {placeholder_ip}\r\n"
        f"a=rtpmap:{pt} opus/48000/2\r\n"
        f"a=ptime:20\r\n"
    )


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


def fix_element_answer_for_rtpengine(
    element_answer_sdp: str,
    our_offer_sdp: str,
    advertised_host: str,
) -> str:
    """Adapt Element answer (m=audio 0, c=0.0.0.0) for RTPEngine: c= and m= from our offer,
    rest (fingerprint, ICE, rtpmap, …) from Element. RTPEngine then returns valid SDP for Linphone.
    """
    if not element_answer_sdp:
        return element_answer_sdp
    port = _first_audio_port_from_sdp(our_offer_sdp)
    if port is None:
        port = 9
    pt = _first_audio_payload_type_from_sdp(our_offer_sdp)
    lines = element_answer_sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("c=IN IP4 "):
            out.append(f"c=IN IP4 {advertised_host}")
        elif s.startswith(M_AUDIO_PREFIX):
            parts = s[len(M_AUDIO_PREFIX) :].strip().split()
            if len(parts) >= 3:
                out.append(f"m=audio {port} {parts[1]} {pt}")
            else:
                out.append(f"m=audio {port} RTP/SAVPF {pt}")
        elif s.startswith("a=rtpmap:0 ") and pt != 0:
            out.append(f"a=rtpmap:{pt} " + s[11:].strip())
        elif s.strip() == "a=inactive":
            out.append("a=sendrecv")
        else:
            out.append(line)
    return "\r\n".join(out)


def make_answer_rewritable_for_rtpengine(
    sdp: str,
    placeholder_port: int = 9,
    placeholder_ip: str = "127.0.0.1",
    offer_sdp: str | None = None,
) -> str:
    """If Element sends answer with m=audio 0 / c=0.0.0.0, RTPEngine often fails on WebRTC attributes.
    Return a whole new minimal answer SDP (no fingerprint/ice/msid), otherwise only adjust port/IP.
    """
    if not sdp:
        return sdp
    if "m=audio 0" in sdp or "0.0.0.0" in sdp:
        return build_minimal_answer_sdp_for_rtpengine(
            placeholder_port=placeholder_port,
            placeholder_ip=placeholder_ip,
            offer_sdp=offer_sdp,
        )
    return sdp
