#!/usr/bin/env bash
# Run the crew cockpit feeder on the host, at the lowest priority.
#
#   scripts/cockpit-feed/run.sh            watch firstmate and keep fleet.json fresh
#   scripts/cockpit-feed/run.sh --once     write fleet.json once and exit
#
# It only reads firstmate's records (FIRSTMATE_HOME, default ~/firstmate) and
# writes OPENSENECA_COCKPIT_DIR (default ~/.local/share/openseneca/cockpit).
# Live terminal mirrors use tmux capture-pane -p only, for panes a browser
# tab asked for through OPENSENECA_COCKPIT_DEMAND_DIR.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
feed="$here/../../src/openjarvis/cockpit/feed.py"
mkdir -p "${OPENSENECA_COCKPIT_DIR:-$HOME/.local/share/openseneca/cockpit}"
# Seneca's container (another uid) drops empty marker files here to ask for a
# live terminal mirror: others may create entries, not list or delete them.
demand="${OPENSENECA_COCKPIT_DEMAND_DIR:-$HOME/.local/share/openseneca/cockpit-demand}"
mkdir -p "$demand" && chmod 1733 "$demand"
if command -v ionice >/dev/null 2>&1; then
  exec nice -n 19 ionice -c3 python3 -I "$feed" "$@"
fi
exec nice -n 19 python3 -I "$feed" "$@"
