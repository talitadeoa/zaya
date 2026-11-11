#!/bin/zsh
export ZAYA_CONFIG="$HOME/zaya/config/config.toml"
export ZAYA_DOTENV="$HOME/zaya/config/.env"
export ZAYA_DATA_DIR="$HOME/zaya/data"
cd "$HOME/zaya" || exit 1
exec zaya "$@"
