#!/usr/bin/env bash
# Spustí RTPEngine v Podman kontajneri s konfiguráciou z config/rtpengine.yaml.
# Po spustení nastav MEDIA_RELAY=rtpengine a RTPENGINE_URL=udp://127.0.0.1:2223 a reštartuj bridge.
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
CONTAINER_NAME="${RTPENGINE_CONTAINER_NAME:-rtpengine}"
IMAGE_NAME="${RTPENGINE_IMAGE_NAME:-dp-rtpengine}"

# 1) Vygenerovať rtpengine.conf z YAML
echo "Generujem config/rtpengine.conf z config/rtpengine.yaml..."
poetry run python scripts/generate_rtpengine_conf.py

# 2) Zbuildovať image (Alpine + rtpengine), ak ešte neexistuje
if ! podman image exists "$IMAGE_NAME" 2>/dev/null; then
  echo "Budujem Podman image $IMAGE_NAME..."
  podman build -f config/Dockerfile.rtpengine -t "$IMAGE_NAME" .
fi

# 3) Zastaviť a odstrániť starý kontajner (ak beží)
if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
  echo "Zastavujem existujúci kontajner $CONTAINER_NAME..."
  podman stop "$CONTAINER_NAME" 2>/dev/null || true
  podman rm "$CONTAINER_NAME" 2>/dev/null || true
fi

# 4) Spustiť RTPEngine
# --network host: kontajner zdieľa sieť s hostom, takže 127.0.0.1:10000 (bridge) je od RTPEngine
# dosiahnuteľné – inak by RTPEngine posielal Linphone RTP na svoj vlastný loopback a bridge by nič nedostal.
# Porty 2223 a 30000–30100 sú potom na hoste (žiadne -p).
echo "Spúšťam RTPEngine v kontajneri $CONTAINER_NAME (--network host)..."
# :z = SELinux kontext pre čítanie (Fedora)
podman run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  -v "$REPO_ROOT/config/rtpengine.conf:/etc/rtpengine.conf:ro,z" \
  "$IMAGE_NAME"

echo ""
# Rýchla kontrola
if ! podman ps --filter "name=^${CONTAINER_NAME}$" --format "{{.Names}}" | grep -q .; then
  echo "Varovanie: kontajner $CONTAINER_NAME nebeží. Posledné logy:"
  podman logs "$CONTAINER_NAME" 2>&1 || true
  echo ""
  echo "Pre zobrazenie chyby pri štarte spusti v popredí:"
  echo "  podman run --rm -v \"$REPO_ROOT/config/rtpengine.conf:/etc/rtpengine.conf:ro,z\" $IMAGE_NAME"
  exit 1
fi
if command -v ss >/dev/null 2>&1; then
  if ! ss -ulnp 2>/dev/null | grep -q 2223; then
    echo "Varovanie: port 2223 nepočúva. Skontroluj: podman logs $CONTAINER_NAME"
  fi
fi
echo "RTPEngine beží. Logy: podman logs -f $CONTAINER_NAME"
echo "Zastaviť: podman stop $CONTAINER_NAME"
echo ""
echo "Bridge spusti (v inom termináli) s:"
echo "  export MEDIA_RELAY=rtpengine"
echo "  export RTPENGINE_URL=udp://127.0.0.1:2223"
echo "  poetry run python -m sip_bridge"
echo ""
echo "Bridge číta config/rtpengine.conf (interface=) pre advertised host – Linphone musí posielať RTP na tú istú adresu."
echo ""
echo "Ak bridge hlási 'Ping error: timed out': kontajner beží? (podman ps) Port 2223 otvorený? (ss -ulnp | grep 2223)"
echo "Testovanie: pozri docs/TESTING_RTPENGINE.md"
