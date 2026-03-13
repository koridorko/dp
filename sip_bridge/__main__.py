# Spustenie: poetry run python -m sip_bridge
# Asterisk + Matrix bridge (bez Kamailio a RTPEngine). Predtým spusti Asterisk: ./scripts/run_asterisk.sh
from .asterisk_bridge_server import run
run()
