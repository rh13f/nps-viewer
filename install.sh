#!/usr/bin/env bash
# NPS Viewer install script
# Supports: Debian/Ubuntu (apt) and RHEL/Rocky/CentOS (dnf/yum)
set -euo pipefail

INSTALL_DIR="/opt/nps-api"
CONFIG_DIR="/etc/nps-api"
SERVICE_USER="nps-api"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== NPS Viewer Install Script ==="

# ── Detect package manager ────────────────────────────────────────────────────
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
elif command -v yum &>/dev/null; then
    PKG_MGR="yum"
else
    echo "ERROR: Unsupported OS — no apt, dnf, or yum found." >&2
    exit 1
fi
echo "Detected package manager: $PKG_MGR"

# ── Check Python 3.11+ ────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ is required but not found." >&2
    echo "Install it with: $PKG_MGR install python3.11" >&2
    exit 1
fi
echo "Using Python: $PYTHON ($($PYTHON --version))"

# ── Create service user ───────────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "Created system user: $SERVICE_USER"
fi

# ── Install nps-api ───────────────────────────────────────────────────────────
echo "Installing nps-api to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/app" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

$PYTHON -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Write config ──────────────────────────────────────────────────────────────
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config.yaml.example" "$CONFIG_DIR/config.yaml"
    echo "Config written to $CONFIG_DIR/config.yaml — edit before use."
else
    echo "Config already exists at $CONFIG_DIR/config.yaml — not overwritten."
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"

# ── Install systemd unit ──────────────────────────────────────────────────────
cp "$SCRIPT_DIR/nps-api.service" /lib/systemd/system/nps-api.service
systemctl daemon-reload
systemctl enable nps-api
systemctl restart nps-api
echo "nps-api service started."

# ── Install Grafana ───────────────────────────────────────────────────────────
if command -v grafana-server &>/dev/null; then
    echo "Grafana already installed — skipping."
else
    echo "Installing Grafana OSS 10.x ..."
    if [ "$PKG_MGR" = "apt" ]; then
        apt-get install -y apt-transport-https software-properties-common wget gnupg
        wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | tee /etc/apt/keyrings/grafana.gpg > /dev/null
        echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
            > /etc/apt/sources.list.d/grafana.list
        apt-get update -q
        apt-get install -y grafana
    else
        cat > /etc/yum.repos.d/grafana.repo <<'EOF'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF
        $PKG_MGR install -y grafana
    fi
fi

# ── Install Grafana plugins ───────────────────────────────────────────────────
echo "Installing Grafana plugins ..."
grafana-cli plugins install marcusolsson-json-datasource || true
grafana-cli plugins install grafana-opensearch-datasource || true

# ── Provision dashboards ──────────────────────────────────────────────────────
echo "Copying Grafana provisioning files ..."
mkdir -p /etc/grafana/provisioning/datasources
mkdir -p /etc/grafana/provisioning/dashboards/json

cp "$SCRIPT_DIR/grafana/provisioning/datasources/nps.yaml" \
    /etc/grafana/provisioning/datasources/nps.yaml
cp "$SCRIPT_DIR/grafana/provisioning/dashboards/nps.yaml" \
    /etc/grafana/provisioning/dashboards/nps.yaml
cp "$SCRIPT_DIR/grafana/provisioning/dashboards/json/"*.json \
    /etc/grafana/provisioning/dashboards/json/

systemctl enable grafana-server
systemctl restart grafana-server
echo "Grafana started."

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Install complete ==="
echo "  nps-api:  http://localhost:8000/health"
echo "  Grafana:  http://$(hostname -I | awk '{print $1}'):3000  (admin/admin — change on first login)"
echo "  Config:   $CONFIG_DIR/config.yaml"
echo ""
echo "Next step: edit $CONFIG_DIR/config.yaml to point at your OpenSearch instance,"
echo "then restart with: systemctl restart nps-api"
