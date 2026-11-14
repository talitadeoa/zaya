#!/usr/bin/env bash
# Prepara a Zaya para rodar no Ubuntu (desktop ou tablet), x86_64 ou arm64.
#
#   ./scripts/install-linux.sh              # instala tudo
#   ./scripts/install-linux.sh --no-apt     # sem sudo/apt (deps já instaladas)
#   ./scripts/install-linux.sh --no-launcher
#
# Instala: dependências de sistema (ffmpeg, PortAudio, clipboard), uv, Python
# 3.12, a Zaya em um venv local, o comando `zaya` em ~/.local/bin e um atalho
# com ícone para abrir a interface em tela cheia no tablet.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_APT=0
SKIP_LAUNCHER=0

for arg in "$@"; do
  case "$arg" in
    --no-apt) SKIP_APT=1 ;;
    --no-launcher) SKIP_LAUNCHER=1 ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "opção desconhecida: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;35m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64|arm64) ;;
  *) warn "arquitetura $ARCH não foi testada (o esperado é x86_64 ou aarch64)." ;;
esac

if ! command -v apt-get >/dev/null 2>&1; then
  warn "apt-get não encontrado: esta receita é para Ubuntu/Debian."
  warn "Instale à mão: ffmpeg, libportaudio2, libsndfile1, wl-clipboard, Python 3.12."
elif [ "$SKIP_APT" -eq 0 ]; then
  say "Instalando dependências do sistema (vai pedir a senha do sudo)..."
  $SUDO apt-get update -y
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git ffmpeg libportaudio2 portaudio19-dev libsndfile1 \
    wl-clipboard xclip
else
  say "Pulando o apt (--no-apt)."
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Instalando uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv não ficou disponível no PATH."

say "Garantindo Python 3.12..."
uv python install 3.12

say "Criando o ambiente e instalando a Zaya..."
cd "$REPO_DIR"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
launcher="$BIN_DIR/zaya"
if [ -e "$launcher" ] && ! grep -q "talita/zaya" "$launcher" 2>/dev/null; then
  warn "$launcher já existe e não parece ser da Zaya; escrevendo em zaya-local."
  launcher="$BIN_DIR/zaya-local"
fi
cat > "$launcher" <<EOF
#!/usr/bin/env bash
# Launcher da Zaya (gerado por scripts/install-linux.sh)
exec "$REPO_DIR/.venv/bin/zaya" "\$@"
EOF
chmod +x "$launcher"
say "Comando instalado em $launcher"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR não está no PATH. Adicione ao ~/.bashrc:"
     warn '  export PATH="$HOME/.local/bin:$PATH"' ;;
esac

CONFIG_FILE="${ZAYA_CONFIG:-$HOME/.config/zaya/config.toml}"
if [ ! -f "$CONFIG_FILE" ]; then
  say "Criando a configuração inicial..."
  "$launcher" config --init || true
fi

if [ "$SKIP_LAUNCHER" -eq 0 ]; then
  DETECTED=""
  for candidate in gnome-terminal kgx konsole xfce4-terminal xterm; do
    if command -v "$candidate" >/dev/null 2>&1; then DETECTED="$candidate"; break; fi
  done
  case "$DETECTED" in
    gnome-terminal) TERM_EXEC="gnome-terminal --maximize -- zaya" ;;
    kgx)            TERM_EXEC="kgx --maximize -- zaya" ;;
    konsole)        TERM_EXEC="konsole --fullscreen -e zaya" ;;
    xfce4-terminal) TERM_EXEC="xfce4-terminal --maximize -e zaya" ;;
    xterm)          TERM_EXEC="xterm -e zaya" ;;
    *)              TERM_EXEC="" ;;
  esac
  if [ -n "$TERM_EXEC" ]; then
    APPS="$HOME/.local/share/applications"
    mkdir -p "$APPS"
    cat > "$APPS/zaya.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Zaya
Comment=Assistente pessoal
Exec=$TERM_EXEC
Icon=utilities-terminal
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
    say "Atalho criado: procure \"Zaya\" nos aplicativos."
  else
    warn "Nenhum terminal conhecido encontrado; rode 'zaya' manualmente."
  fi
fi

say "Conferindo o ambiente..."
"$launcher" doctor || true

say "Pronto. Para conversar: zaya"
