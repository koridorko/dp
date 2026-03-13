#!/usr/bin/env bash
# Spustí Asterisk v Podman kontajneri. Predtým spusti bridge: poetry run python -m sip_bridge
# Linphone: SIP server = host IP (alebo 127.0.0.1 pri --network host), volaj napr. sip:111@127.0.0.1
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
CONTAINER_NAME="${ASTERISK_CONTAINER_NAME:-asterisk-matrix}"
IMAGE_NAME="${ASTERISK_IMAGE_NAME:-docker.io/andrius/asterisk:20}"
CONFIG_DIR="$REPO_ROOT/config/asterisk"

if [[ ! -d "$CONFIG_DIR" ]]; then
  echo "Chýba $CONFIG_DIR"
  exit 1
fi

if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
  echo "Zastavujem $CONTAINER_NAME..."
  podman stop "$CONTAINER_NAME" 2>/dev/null || true
  podman rm "$CONTAINER_NAME" 2>/dev/null || true
fi

echo "Spúšťam Asterisk (--network host, ARI na 8088)..."
podman run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  -v "$CONFIG_DIR:/etc/asterisk:ro,z" \
  "$IMAGE_NAME" \
  asterisk -f -U asterisk -vvv

sleep 2
if ! podman ps --filter "name=^${CONTAINER_NAME}$" --format "{{.Names}}" | grep -q .; then
  echo "Kontajner nebeží. Logy:"
  podman logs "$CONTAINER_NAME" 2>&1 || true
  exit 1
fi
echo "Asterisk beží. ARI: http://127.0.0.1:8088/ari (user: matrix_bridge). Logy: podman logs -f $CONTAINER_NAME"
echo "Linphone: Server 127.0.0.1:5061 (Domain: 127.0.0.1:5061), volaj sip:111@127.0.0.1 (podľa register.yaml)"
