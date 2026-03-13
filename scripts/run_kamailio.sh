#!/usr/bin/env bash
# Spustí Kamailio v Podman kontajneri. Bridge (Python HTTP) musí bežať na hoste na porte 8080.
# Pred spustením: 1) RTPEngine (./scripts/run_rtpengine.sh), 2) bridge (poetry run python -m sip_bridge.http_bridge_server)
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
CONTAINER_NAME="${KAMAILIO_CONTAINER_NAME:-kamailio}"
# Oficiálne images: ghcr.io/kamailio/kamailio (tagy napr. 5.8.7-focal)
IMAGE_NAME="${KAMAILIO_IMAGE_NAME:-ghcr.io/kamailio/kamailio:5.8.7-focal}"

# URL na HTTP bridge (Python). Pri --network host stačí 127.0.0.1:8080.
BRIDGE_HTTP_URL="${BRIDGE_HTTP_URL:-http://127.0.0.1:8080/}"

# Vygenerovať config s nahradeným BRIDGE_HTTP_URL
GEN_CFG="$REPO_ROOT/config/kamailio/kamailio_generated.cfg"
mkdir -p "$(dirname "$GEN_CFG")"
sed "s|BRIDGE_HTTP_URL|$BRIDGE_HTTP_URL|g" \
  "$REPO_ROOT/config/kamailio/kamailio.cfg" > "$GEN_CFG"
echo "Config: $GEN_CFG (bridge URL: $BRIDGE_HTTP_URL)"

# Zastaviť a odstrániť starý kontajner
if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
  echo "Zastavujem $CONTAINER_NAME..."
  podman stop "$CONTAINER_NAME" 2>/dev/null || true
  podman rm "$CONTAINER_NAME" 2>/dev/null || true
fi

echo "Spúšťam Kamailio (--network host)..."
podman run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  -v "$GEN_CFG:/etc/kamailio/kamailio.cfg:ro,z" \
  $IMAGE_NAME \
  kamailio -f /etc/kamailio/kamailio.cfg -DDD -E

if ! podman ps --filter "name=^${CONTAINER_NAME}$" --format "{{.Names}}" | grep -q .; then
  echo "Kontajner nebeží. Logy:"
  podman logs "$CONTAINER_NAME" 2>&1 || true
  exit 1
fi
echo "Kamailio beží. Logy: podman logs -f $CONTAINER_NAME"
echo "Linphone: SIP server 127.0.0.1:5061 (ako pôvodný Python server), REGISTER a volanie na URI z register.yaml"
