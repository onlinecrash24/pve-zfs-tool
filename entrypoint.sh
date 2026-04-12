#!/bin/bash
set -e

SSH_DIR="/root/.ssh"
KEY_FILE="$SSH_DIR/id_ed25519"

if [ ! -f "$KEY_FILE" ]; then
    echo "==> Generating SSH key pair..."
    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    ssh-keygen -t ed25519 -f "$KEY_FILE" -N "" -C "zfs-tool@docker"
    chmod 600 "$KEY_FILE"
    chmod 644 "$KEY_FILE.pub"
    echo "==> SSH key pair generated."
else
    echo "==> SSH key pair already exists, skipping generation."
fi

echo "==> Public Key:"
cat "$KEY_FILE.pub"
echo ""

echo "==> Starting ZFS Tool..."
exec gunicorn --bind 0.0.0.0:5000 --workers 1 --worker-class sync --timeout 300 --preload --log-level info app.main:app
