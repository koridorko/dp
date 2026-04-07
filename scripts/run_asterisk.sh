#!/usr/bin/env bash
# Runs Asterisk in a Podman container. 
# Linphone: SIP server = host IP (or 127.0.0.1 with --network host); dial e.g. sip:111@127.0.0.1
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
CONTAINER_NAME="${ASTERISK_CONTAINER_NAME:-asterisk-matrix}"
IMAGE_NAME="${ASTERISK_IMAGE_NAME:-docker.io/andrius/asterisk:20}"
CONFIG_DIR="$REPO_ROOT/config/asterisk"

if [[ ! -d "$CONFIG_DIR" ]]; then
  echo "Missing $CONFIG_DIR"
  exit 1
fi

if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
  echo "Stopping $CONTAINER_NAME..."
  podman stop "$CONTAINER_NAME" 2>/dev/null || true
  podman rm "$CONTAINER_NAME" 2>/dev/null || true
fi

echo "Starting Asterisk (--network host, ARI on 8088)..."
podman run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  -v "$CONFIG_DIR:/etc/asterisk:ro,z" \
  "$IMAGE_NAME" \
  asterisk -f -U asterisk -vvv

sleep 2
if ! podman ps --filter "name=^${CONTAINER_NAME}$" --format "{{.Names}}" | grep -q .; then
  echo "Container is not running. Logs:"
  podman logs "$CONTAINER_NAME" 2>&1 || true
  exit 1
fi
echo "Asterisk is running. ARI: http://127.0.0.1:8088/ari (user: matrix_bridge). Logs: podman logs -f $CONTAINER_NAME"
echo "Linphone: Server 127.0.0.1:5061 (Domain: 127.0.0.1:5061)"
