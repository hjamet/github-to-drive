#!/usr/bin/env bash
set -euo pipefail

# =========================================================================
# GitHub → Drive Sync — Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/hjamet/github-to-drive/main/install.sh | bash
# =========================================================================

REPO_RAW="https://raw.githubusercontent.com/hjamet/github-to-drive/main"
INSTALL_DIR="$HOME/.local/share/github-to-drive"
CONFIG_DIR="$HOME/.config/github-to-drive"
SERVICE_NAME="github-to-drive"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# When piped via curl, stdin is the pipe — use /dev/tty for prompts
ask()      { echo -en "${YELLOW}$1${NC}" > /dev/tty; read -r REPLY < /dev/tty; echo "$REPLY"; }
ask_secret() { echo -en "${YELLOW}$1${NC}" > /dev/tty; read -rs REPLY < /dev/tty; echo "" > /dev/tty; echo "$REPLY"; }

# ── 1. Prerequisites ─────────────────────────────────────────────────────
info "Checking prerequisites…"
command -v python3 >/dev/null 2>&1 || die "python3 is required."
python3 -c "import venv" 2>/dev/null   || die "python3-venv is required (apt install python3-venv)."
command -v curl   >/dev/null 2>&1 || die "curl is required."
ok "Prerequisites satisfied"

# ── 2. Stop existing service (no zombies) ─────────────────────────────────
if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    warn "Existing service running — stopping…"
    systemctl --user stop "$SERVICE_NAME"
    ok "Service stopped"
fi

# ── 3. Create directories ────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

# ── 4. Download project files ────────────────────────────────────────────
info "Downloading latest files…"
curl -fsSL "$REPO_RAW/sync.py"           -o "$INSTALL_DIR/sync.py"
curl -fsSL "$REPO_RAW/requirements.txt"  -o "$INSTALL_DIR/requirements.txt"
ok "Files downloaded to $INSTALL_DIR"

# ── 5. Python venv + dependencies ────────────────────────────────────────
info "Setting up Python virtual environment…"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
ok "Virtual environment ready"

# ── 6. GitHub token ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo -e "${BOLD}  Step 1/2 — GitHub Configuration${NC}"
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo ""
echo "You need a Personal Access Token with the 'repo' scope."
echo "Create one at: https://github.com/settings/tokens/new"
echo ""
GH_TOKEN=$(ask_secret "Paste your GitHub token: ")
[ -z "$GH_TOKEN" ] && die "GitHub token is required."

# Validate
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: token $GH_TOKEN" https://api.github.com/user)
[ "$HTTP_CODE" != "200" ] && die "Invalid GitHub token (HTTP $HTTP_CODE)."
GH_USER=$(curl -s -H "Authorization: token $GH_TOKEN" https://api.github.com/user | python3 -c "import sys,json; print(json.load(sys.stdin)['login'])")
ok "Authenticated as $GH_USER"

# Save config
cat > "$CONFIG_DIR/config.json" <<EOF
{
    "github_token": "$GH_TOKEN"
}
EOF
chmod 600 "$CONFIG_DIR/config.json"

# ── 7. Google Drive OAuth2 ───────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo -e "${BOLD}  Step 2/2 — Google Drive Setup${NC}"
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo ""
echo "Follow these steps to enable Google Drive access:"
echo ""
echo "  1. Go to ${BLUE}https://console.cloud.google.com${NC}"
echo "  2. Create a new project (or use an existing one)"
echo "  3. Enable the ${BOLD}Google Drive API${NC}:"
echo "     → APIs & Services → Library → search 'Google Drive API' → Enable"
echo "  4. Create ${BOLD}OAuth2 credentials${NC}:"
echo "     → APIs & Services → Credentials"
echo "     → Create Credentials → OAuth client ID"
echo "     → Application type: ${BOLD}Desktop app${NC}"
echo ""
echo "  Then copy the Client ID and Client Secret shown on screen."
echo ""
CLIENT_ID=$(ask "Client ID: ")
[ -z "$CLIENT_ID" ] && die "Client ID is required."
CLIENT_SECRET=$(ask_secret "Client Secret: ")
[ -z "$CLIENT_SECRET" ] && die "Client Secret is required."

# Generate credentials.json from client_id + client_secret
cat > "$CONFIG_DIR/credentials.json" <<EOF
{
    "installed": {
        "client_id": "$CLIENT_ID",
        "client_secret": "$CLIENT_SECRET",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": ["http://localhost"]
    }
}
EOF
chmod 600 "$CONFIG_DIR/credentials.json"
ok "OAuth2 credentials configured"

info "Starting Google Drive authorization…"
echo ""
echo "A URL will appear below. Open it in any browser (on this machine or another)."
echo "After authorizing, copy the redirect URL and paste it back here."
echo ""
"$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/sync.py" --setup < /dev/tty
ok "Google Drive authorized — folder 'github' ready"

# ── 8. Install systemd user service ──────────────────────────────────────
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/$SERVICE_NAME.service" <<EOF
[Unit]
Description=GitHub to Google Drive Sync
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/sync.py
Restart=on-failure
RestartSec=60
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user start  "$SERVICE_NAME"
ok "Service installed, enabled, and started"

# Enable lingering so the user service survives logout
loginctl enable-linger "$(whoami)" 2>/dev/null || true

# ── Done ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}✅ Installation complete!${NC}"
echo ""
echo "  Service status:  systemctl --user status $SERVICE_NAME"
echo "  View logs:       journalctl --user -u $SERVICE_NAME -f"
echo "  Config dir:      $CONFIG_DIR"
echo "  Install dir:     $INSTALL_DIR"
echo ""
echo "  The service will sync your repos every hour."
echo "  Your Markdown files will appear in the 'github' folder on Google Drive."
echo ""
