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
# Worker model rationale:
#   --workers 1        Single worker process. The app keeps state in-process
#                      (in-memory async task registry in app/tasks.py, the
#                      AI-report scheduler's last-run map, cached SSH data).
#                      A second worker would split that state across processes
#                      and break task-status polling, so we stay at one.
#   --worker-class gthread + --threads 8
#                      Serve requests on a thread pool instead of a single
#                      blocking sync worker. ZFS queries go over SSH (paramiko)
#                      which releases the GIL while waiting on the socket, so a
#                      slow / cache-miss request no longer freezes the whole UI
#                      for every other client.
#   (no --preload)     IMPORTANT: preload imports the app in the gunicorn
#                      MASTER, then forks the worker -- and fork does not carry
#                      over running threads. Our background threads (metrics
#                      sampler, AI-report scheduler, replication monitor) start
#                      at import time, so under --preload they ran in the
#                      master while a config-save re-armed a SECOND copy in the
#                      worker => duplicate scheduled reports. Without preload
#                      the app is imported in the worker, so those threads
#                      start exactly once, in the process that also serves
#                      requests and runs the async tasks.
exec gunicorn --bind 0.0.0.0:5000 \
    --workers 1 \
    --worker-class gthread \
    --threads 8 \
    --timeout 300 \
    --log-level info \
    app.main:app
