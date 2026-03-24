"""
Asterisk ARI client: WebSocket events + REST. Stasis app 'matrix-bridge'.
On StasisStart we create External Media channel, bridge it with the SIP channel,
and notify the bridge so it can send m.call.invite to Matrix.
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _ari_url(base: str, path: str, query: dict | None = None) -> str:
    # ARI is always under /ari (e.g. /ari/channels/externalMedia)
    path = path if path.startswith("/") else "/" + path
    if not path.startswith("/ari"):
        path = "/ari" + path
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def ari_http(
    base: str,
    method: str,
    path: str,
    user: str,
    password: str,
    body: dict | None = None,
    query: dict | None = None,
) -> dict | list | None:
    """Sync HTTP request to ARI. Returns JSON or None."""
    url = _ari_url(base, path, query)
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    cred = urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe="")
    # Basic auth
    import base64
    req.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
    )
    data = None
    if body is not None and method in ("POST", "PUT", "PATCH"):
        data = json.dumps(body).encode()
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            if not raw:
                return None
            return json.loads(raw)
    except Exception as e:
        logger.warning("ARI HTTP %s %s failed: %s", method, path, e)
        return None


async def ari_http_async(
    base: str,
    method: str,
    path: str,
    user: str,
    password: str,
    body: dict | None = None,
    query: dict | None = None,
    loop=None,
) -> dict | list | None:
    """Run ari_http in executor (blocking)."""
    loop = loop or asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: ari_http(base, method, path, user, password, body, query),
    )


def create_external_media_channel(
    base: str,
    user: str,
    password: str,
    app: str,
    external_host: str,
    external_port: int,
    format_codec: str = "ulaw",
) -> dict | None:
    """POST /ari/channels/externalMedia. Returns channel dict or None."""
    host_port = f"{external_host}:{external_port}"
    query = {
        "app": app,
        "external_host": host_port,
        "format": format_codec,
    }
    return ari_http(base, "POST", "/channels/externalMedia", user, password, query=query)


def create_bridge(base: str, user: str, password: str, bridge_type: str = "mixing") -> dict | None:
    """POST /ari/bridges. Returns bridge dict or None."""
    return ari_http(base, "POST", "/bridges", user, password, body={"type": bridge_type})


def add_channel_to_bridge(
    base: str,
    user: str,
    password: str,
    bridge_id: str,
    channel_id: str,
) -> bool:
    """POST /ari/bridges/{bridgeId}/addChannel."""
    result = ari_http(
        base,
        "POST",
        f"/bridges/{bridge_id}/addChannel",
        user,
        password,
        query={"channel": channel_id},
    )
    return result is not None


def delete_channel(base: str, user: str, password: str, channel_id: str) -> bool:
    """DELETE /ari/channels/{channelId} (hangup)."""
    try:
        url = _ari_url(base, f"/channels/{channel_id}")
        req = urllib.request.Request(url, method="DELETE")
        import base64
        req.add_header(
            "Authorization",
            "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def answer_channel(base: str, user: str, password: str, channel_id: str) -> bool:
    """POST /ari/channels/{channelId}/answer (for incoming channel)."""
    result = ari_http(base, "POST", f"/channels/{channel_id}/answer", user, password)
    return result is not None


def channel_still_exists(base: str, user: str, password: str, channel_id: str) -> bool:
    """GET /ari/channels/{id}. False if 404 (channel hung up); True on 200 or on transient errors (avoid false hangup)."""
    import base64

    if not base or not channel_id:
        return True
    url = _ari_url(base, f"/channels/{channel_id}")
    req = urllib.request.Request(url, method="GET")
    req.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        logger.warning("ARI GET /channels/%s HTTP %s", channel_id, e.code)
        return True
    except Exception as e:
        logger.warning("ARI GET /channels/%s failed: %s", channel_id, e)
        return True


async def run_ari_websocket(
    base_http: str,
    user: str,
    password: str,
    app_name: str,
    our_rtp_host: str,
    our_rtp_port: int,
    on_incoming_call: "Callable[[str, str], None]",
    loop=None,
    format_codec: str = "ulaw",
    on_sip_channel_destroyed=None,
) -> None:
    """
    Connect to ARI WebSocket (events), handle StasisStart.
    When a channel enters Stasis(matrix-bridge, EXTEN):
      - Create External Media channel (RTP to our_rtp_host:our_rtp_port, format_codec)
      - Create bridge, add SIP channel and External Media channel
      - Call on_incoming_call(sip_channel_id, extension) so bridge can send m.call.invite.
    base_http: e.g. http://127.0.0.1:8088
    on_incoming_call: callback(sip_channel_id, extension) - extension is the dialed number (e.g. "111").
    on_sip_channel_destroyed: optional async callback(channel_id) when a channel is destroyed (SIP hangup).
    """
    loop = loop or asyncio.get_event_loop()
    ws_url = base_http.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    # subscribeAll: after PJSIP + External Media join a native bridge, they leave Stasis and the
    # app has no channels — app-only subscription stops delivering ChannelDestroyed. We need
    # subscribeAll to see SIP hangup; still filter StasisStart by application name.
    ws_url += (
        f"/ari/events?app={urllib.parse.quote(app_name)}"
        f"&subscribeAll=true"
    )
    import base64
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()

    try:
        import websockets
    except ImportError:
        logger.error("websockets not installed. pip install websockets")
        return

    async def _run():
        # websockets 14.0+: parameter je additional_headers (nie extra_headers)
        async for ws in websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Basic {auth}"},
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ):
            try:
                async for raw in ws:
                    ev = json.loads(raw)
                    ev_type = ev.get("type")
                    if ev_type == "ChannelDestroyed":
                        ch = ev.get("channel") or {}
                        destroyed_id = ch.get("id")
                        if destroyed_id and on_sip_channel_destroyed:
                            try:
                                await on_sip_channel_destroyed(destroyed_id)
                            except Exception as e:
                                logger.exception("on_sip_channel_destroyed failed: %s", e)
                        continue
                    if ev_type == "StasisStart":
                        if (ev.get("application") or "") != app_name:
                            continue
                        channel_id = (ev.get("channel") or {}).get("id")
                        channel_name = (ev.get("channel") or {}).get("name") or ""
                        args = ev.get("args") or []
                        if not channel_id:
                            continue
                        # args[0] = extension (e.g. "111") from Stasis(matrix-bridge, 111)
                        extension = str(args[0]) if args else ""
                        # Only handle channels that look like SIP (PJSIP) - the incoming call
                        if "PJSIP" in channel_name or "SIP" in channel_name or extension.isdigit():
                            logger.info("StasisStart channel=%s extension=%s", channel_id, extension)
                            # Answer the channel so we get media
                            await loop.run_in_executor(
                                None,
                                lambda: answer_channel(base_http, user, password, channel_id),
                            )
                            # Create External Media channel (Asterisk will send RTP to us)
                            ext_ch = await loop.run_in_executor(
                                None,
                                lambda: create_external_media_channel(
                                    base_http,
                                    user,
                                    password,
                                    app_name,
                                    our_rtp_host,
                                    our_rtp_port,
                                    format_codec,
                                ),
                            )
                            if not ext_ch:
                                logger.warning("Failed to create External Media channel")
                                continue
                            ext_id = ext_ch.get("id")
                            if not ext_id:
                                continue
                            # Create bridge and add both channels
                            bridge = await loop.run_in_executor(
                                None,
                                lambda: create_bridge(base_http, user, password),
                            )
                            if not bridge:
                                continue
                            bridge_id = bridge.get("id")
                            if bridge_id:
                                await loop.run_in_executor(
                                    None,
                                    lambda: add_channel_to_bridge(
                                        base_http, user, password, bridge_id, channel_id
                                    ),
                                )
                                await loop.run_in_executor(
                                    None,
                                    lambda: add_channel_to_bridge(
                                        base_http, user, password, bridge_id, ext_id
                                    ),
                                )
                            # Notify bridge: (sip_channel_id, extension)
                            try:
                                on_incoming_call(channel_id, extension)
                            except Exception as e:
                                logger.exception("on_incoming_call failed: %s", e)
            except websockets.ConnectionClosed as e:
                logger.warning("ARI WebSocket closed: %s; reconnecting...", e)
                await asyncio.sleep(2)
            except Exception as e:
                logger.exception("ARI WebSocket error: %s", e)
                await asyncio.sleep(2)

    await _run()

