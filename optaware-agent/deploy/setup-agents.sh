#!/usr/bin/env bash
# setup-agents.sh — Create OptAware service accounts, directories, and permissions.
#
# Usage:
#   sudo bash deploy/setup-agents.sh            # apply changes
#   sudo bash deploy/setup-agents.sh --dry-run   # preview only
#
set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "=== DRY RUN MODE — no changes will be made ==="
fi

run() {
    echo "  [RUN] $*"
    if [[ "$DRY_RUN" == "false" ]]; then
        "$@"
    fi
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       OptAware Multi-Agent Setup                    ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ------------------------------------------------------------------
# 1. Create system group
# ------------------------------------------------------------------
echo "--- Creating system group: optaware ---"
if getent group optaware >/dev/null 2>&1; then
    echo "  Group 'optaware' already exists."
else
    run groupadd --system optaware
fi

# ------------------------------------------------------------------
# 2. Create service accounts
# ------------------------------------------------------------------
AGENTS=("optaware" "optaware-observer" "optaware-planner" "optaware-executor" "optaware-auditor")

for agent in "${AGENTS[@]}"; do
    echo "--- Creating user: $agent ---"
    if id "$agent" >/dev/null 2>&1; then
        echo "  User '$agent' already exists."
    else
        run useradd --system --no-create-home --shell /usr/sbin/nologin --gid optaware "$agent"
    fi
done

# Executor needs a real shell for sudo
echo "--- Setting executor shell to /bin/bash ---"
run usermod --shell /bin/bash optaware-executor

# ------------------------------------------------------------------
# 3. Create directories with proper ownership
# ------------------------------------------------------------------
echo ""
echo "--- Creating directories ---"

# Config directory
run install -d -m 750 -o root -g optaware /etc/optaware

# Runtime directory (IPC sockets)
run install -d -m 770 -o optaware -g optaware /run/optaware

# Log directory (auditor-writable)
run install -d -m 770 -o optaware-auditor -g optaware /var/log/optaware

# Data directory
run install -d -m 770 -o optaware -g optaware /data/optaware
run install -d -m 770 -o optaware -g optaware /data/optaware/logs
run install -d -m 770 -o optaware-executor -g optaware /data/optaware/rollback
run install -d -m 770 -o optaware -g optaware /data/optaware/knowledge

# ------------------------------------------------------------------
# 4. Install sudoers file
# ------------------------------------------------------------------
echo ""
echo "--- Installing sudoers rules ---"

SUDOERS_SRC="$(dirname "$0")/sudoers.d/optaware-executor"
SUDOERS_DST="/etc/sudoers.d/optaware-executor"

if [[ -f "$SUDOERS_SRC" ]]; then
    run install -m 0440 -o root -g root "$SUDOERS_SRC" "$SUDOERS_DST"
    if [[ "$DRY_RUN" == "false" ]]; then
        echo "  Validating sudoers syntax..."
        if visudo -c -f "$SUDOERS_DST" 2>/dev/null; then
            echo "  Sudoers file is valid."
        else
            echo "  WARNING: sudoers syntax check failed! Review $SUDOERS_DST"
        fi
    fi
else
    echo "  WARNING: $SUDOERS_SRC not found — skipping."
fi

# ------------------------------------------------------------------
# 5. Install systemd units
# ------------------------------------------------------------------
echo ""
echo "--- Installing systemd units ---"

UNIT_DIR="$(dirname "$0")/systemd"
for unit in "$UNIT_DIR"/*.service; do
    if [[ -f "$unit" ]]; then
        name="$(basename "$unit")"
        echo "  Installing $name"
        run install -m 0644 "$unit" "/etc/systemd/system/$name"
    fi
done

if [[ "$DRY_RUN" == "false" ]]; then
    echo "  Reloading systemd daemon..."
    systemctl daemon-reload

    echo "  Enabling services..."
    for agent in coordinator observer planner executor auditor; do
        systemctl enable "optaware-$agent.service" 2>/dev/null || true
    done
fi

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                    ║"
echo "║                                                     ║"
echo "║  Next steps:                                        ║"
echo "║  1. Edit /etc/optaware/optaware.yaml                ║"
echo "║     Set agents.enabled: true                        ║"
echo "║     Set agents.signing_secret: <random secret>      ║"
echo "║  2. Start: systemctl start optaware-coordinator     ║"
echo "║  3. Status: systemctl status 'optaware-*'           ║"
echo "╚══════════════════════════════════════════════════════╝"
