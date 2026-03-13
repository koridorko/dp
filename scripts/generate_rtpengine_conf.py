#!/usr/bin/env python3
"""
Read config/rtpengine.yaml and generate rtpengine.conf (ini format) for RTPEngine daemon.
Used in scripts/run_rtpengine.sh before starting the container.
If interface in YAML is "0.0.0.0", it is replaced with this machine's IP (RTPEngine does not accept 0.0.0.0).
"""
import socket
from pathlib import Path
import sys

try:
    import yaml
except ImportError:
    print("PyYAML missing: poetry install", file=sys.stderr)
    sys.exit(1)


def _detect_interface_ip() -> str:
    """This machine's IP (on which RTPEngine should listen so Element can send media)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        s.close()
        if addr and addr != "0.0.0.0":
            return addr
    except OSError:
        pass
    return "127.0.0.1"

# Map YAML keys to option names in rtpengine.conf (long option names)
KEY_TO_OPT = {
    "interface": "interface",
    "listen_ng": "listen-ng",
    "port_min": "port-min",
    "port_max": "port-max",
    "timeout": "timeout",
    "silent_timeout": "silent-timeout",
    "delete_delay": "delete-delay",
    "table": "table",  # -1 = userspace only (required in container)
    "foreground": "foreground",
    "log_stderr": "log-stderr",
    "log_level": "log-level",
}


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    yaml_path = repo_root / "config" / "rtpengine.yaml"
    out_path = repo_root / "config" / "rtpengine.conf"

    if not yaml_path.exists():
        print(f"Missing {yaml_path}", file=sys.stderr)
        sys.exit(1)

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    section = data.get("rtpengine") or data
    lines = ["[rtpengine]", ""]
    for key, opt in KEY_TO_OPT.items():
        if key not in section:
            continue
        val = section[key]
        if key == "interface" and str(val).strip() == "0.0.0.0":
            val = _detect_interface_ip()
            print(f"Interface 0.0.0.0 -> {val} (so Element can send media)", file=sys.stderr)
        if isinstance(val, bool):
            val = "true" if val else "false"
        lines.append(f"{opt}={val}")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Generated: {out_path}")


if __name__ == "__main__":
    main()
