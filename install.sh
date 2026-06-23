#!/usr/bin/env bash
# install.sh — install mmb-autoscroll as a system service. Run with sudo.
#   sudo ./install.sh           # install / update binary + unit (+ conf if absent)
#   sudo ./install.sh --enable  # also enable + start now
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN=/usr/local/bin/mmb-autoscroll
UNIT=/etc/systemd/system/mmb-autoscroll.service
CONF=/etc/mmb-autoscroll.conf

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0 ${*:-}" >&2
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 missing" >&2; exit 1; }
python3 -c 'import evdev' 2>/dev/null || {
  echo "python3-evdev not installed. Run:  sudo apt install -y python3-evdev" >&2
  exit 1
}

install -m 0755 "$SRC_DIR/mmb_autoscroll.py" "$BIN"
install -m 0644 "$SRC_DIR/mmb-autoscroll.service" "$UNIT"
if [[ ! -e "$CONF" ]]; then
  install -m 0644 "$SRC_DIR/mmb-autoscroll.conf.sample" "$CONF"
  echo "wrote default config -> $CONF"
else
  echo "kept existing config -> $CONF (sample at $SRC_DIR/mmb-autoscroll.conf.sample)"
fi

systemctl daemon-reload
echo "installed: $BIN, $UNIT"

if [[ "${1:-}" == "--enable" ]]; then
  systemctl enable --now mmb-autoscroll
  systemctl --no-pager --full status mmb-autoscroll | sed -n '1,8p' || true
else
  echo "Not enabled. Start a trial:   sudo systemctl start mmb-autoscroll"
  echo "Enable at boot when happy:    sudo systemctl enable --now mmb-autoscroll"
fi
