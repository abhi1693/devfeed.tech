#!/bin/sh
set -eu
# Configuration follows the image; sign-in/state survives in the dedicated volume.
config_tmp=$(mktemp "$CODEX_HOME/.config.XXXXXX")
trap 'rm -f "$config_tmp"' EXIT HUP INT TERM
cp /opt/devfeed/config.toml "$config_tmp"
chmod 600 "$config_tmp"
mv "$config_tmp" "$CODEX_HOME/config.toml"
if [ "${1:-}" = app-server ]; then
    exec node /opt/devfeed/server.cjs "$@"
fi
exec codex "$@"
