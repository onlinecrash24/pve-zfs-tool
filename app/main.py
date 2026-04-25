import os
import time
import secrets
import functools
import hashlib
import hmac
import logging
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response

from app.ssh_manager import (
    load_hosts, add_host, remove_host, test_connection, get_public_key,
    rotate_ssh_keys,
)
from app.zfs_commands import (
    get_pools, get_pool_status, get_pool_iostat, scrub_pool, get_pool_history,
    check_pool_upgrade, upgrade_pool, start_scrub_monitor,
    get_datasets, get_dataset_properties, set_dataset_property,
    create_dataset, destroy_dataset,
    get_snapshots, create_snapshot, destroy_snapshot,
    rollback_snapshot, clone_snapshot, diff_snapshot,
    get_auto_snapshot_status, get_auto_snapshot_property, set_auto_snapshot,
    get_pve_vms, get_pve_cts, get_vm_snapshots,
    snapshot_mount, snapshot_unmount, snapshot_browse,
    snapshot_read_file, snapshot_restore_file, snapshot_restore_dir,
    zvol_snapshot_mount, zvol_partition_mount, zvol_unmount,
    zvol_list_active, zvol_cleanup_all,
    estimate_send_size, estimate_incremental_size,
    get_arc_stats, get_zfs_events, get_smart_status,
    get_snapshot_ages,
)
from app.notifications import (
    load_config as load_notify_config,
    save_config as save_notify_config,
    send_notification, test_telegram, test_gotify, test_matrix, test_email,
)
from app.ai_reports import (
    load_config_masked as load_ai_config,
    save_config_unmasked as save_ai_config,
    test_connection as test_ai_connection,
    generate_report as generate_ai_report,
    collect_host_data,
    load_reports as load_ai_reports,
    chat as ai_chat,
    start_scheduler as start_ai_scheduler,
    list_ollama_models,
    get_active_schedules,
)
from app.database import init_db
from app.metrics import (
    start_sampler as start_metrics_sampler,
    query_pool_series, list_pools as list_metric_pools,
    summary as metrics_summary, sample_host as metrics_sample_host,
)
from app.audit import log_action as audit_log, query as audit_query, count as audit_count, distinct_actions as audit_actions
from app import cache as ssh_cache

# Initialise shared SQLite DB (metrics + audit) once at import time
init_db()

log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")
app.permanent_session_lifetime = timedelta(hours=8)

# Support running behind a reverse proxy (NPM, nginx, Caddy, Traefik)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Secure cookie settings (effective when behind HTTPS reverse proxy)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("FORCE_HTTPS", "").lower() in ("1", "true", "yes"):
    app.config["SESSION_COOKIE_SECURE"] = True

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "password")

# Startup security checks
if app.secret_key == "dev-key-change-me":
    log.critical("SECRET_KEY is using the insecure default! Set a strong SECRET_KEY environment variable.")
    # Auto-generate a random key so sessions are at least unpredictable
    app.secret_key = secrets.token_hex(32)
    log.warning("Auto-generated random SECRET_KEY for this session (will change on restart).")
if ADMIN_USER == "admin" and ADMIN_PASSWORD == "password":
    log.warning("Using default credentials (admin/password)! Change ADMIN_USER and ADMIN_PASSWORD environment variables.")

# Rate limiting for login attempts
_login_attempts = {}  # IP -> {"count": int, "last": float}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            # API requests get 401, page requests get redirect
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
    return response


@app.before_request
def check_auth():
    # Allow login page, login API, and static files without auth
    allowed = ("/login", "/api/login", "/static/")
    if any(request.path.startswith(p) for p in allowed):
        return
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not authenticated"}), 401
        return redirect(url_for("login_page"))

    # CSRF protection for state-changing requests
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        token = request.headers.get("X-CSRF-Token", "")
        if not token or not hmac.compare_digest(token, session.get("csrf_token", "")):
            return jsonify({"error": "CSRF token invalid"}), 403


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    client_ip = request.remote_addr or "unknown"

    # Rate limiting
    now = time.time()
    info = _login_attempts.get(client_ip, {"count": 0, "last": 0})
    if info["count"] >= MAX_LOGIN_ATTEMPTS and (now - info["last"]) < LOGIN_LOCKOUT_SECONDS:
        remaining = int(LOGIN_LOCKOUT_SECONDS - (now - info["last"]))
        return jsonify({"success": False, "error": f"Too many attempts. Try again in {remaining}s"}), 429

    # Reset counter if lockout period has passed
    if (now - info["last"]) >= LOGIN_LOCKOUT_SECONDS:
        info = {"count": 0, "last": now}

    data = request.json or {}
    username = data.get("username", "")
    password = data.get("password", "")

    # Timing-safe comparison to prevent timing attacks
    user_ok = hmac.compare_digest(username.encode(), ADMIN_USER.encode())
    pass_ok = hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())

    if user_ok and pass_ok:
        # Reset attempts on successful login
        _login_attempts.pop(client_ip, None)
        # Rotate session ID to prevent session fixation attacks
        session.clear()
        session["authenticated"] = True
        session["username"] = username
        session["csrf_token"] = secrets.token_hex(32)
        session.permanent = True
        audit_log("login.success", target=username, success=True,
                  user=username, ip=client_ip)
        return jsonify({"success": True, "csrf_token": session["csrf_token"]})

    # Track failed attempt
    info["count"] += 1
    info["last"] = now
    _login_attempts[client_ip] = info
    audit_log("login.failure", target=username or "?", success=False,
              user="?", ip=client_ip,
              details={"attempts": info["count"]})
    return jsonify({"success": False, "error": "Invalid credentials"}), 401


@app.route("/api/csrf-token")
def api_csrf_token():
    """Return the CSRF token for the current session (creates one if missing)."""
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_hex(32)
    return jsonify({"csrf_token": session["csrf_token"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    user = session.get("username", "")
    session.clear()
    audit_log("logout", target=user, user=user or "anonymous")
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_host(address):
    for h in load_hosts():
        if h["address"] == address:
            return h
    return None


def _require_host():
    address = request.args.get("host") or request.json.get("host")
    if not address:
        return None, jsonify({"error": "Missing host parameter"}), 400
    host = _find_host(address)
    if not host:
        return None, jsonify({"error": "Host not found"}), 404
    return host, None, None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: SSH Key
# ---------------------------------------------------------------------------

@app.route("/api/ssh-key/rotate", methods=["POST"])
@login_required
def api_rotate_ssh_key():
    """Generate a new SSH key and deploy it to all configured hosts.

    Failures are surfaced per-host. Old key is kept on any host where the
    new key does not verify, so the operator can fix the situation without
    being locked out.
    """
    result = rotate_ssh_keys()
    audit_log(
        "ssh.key.rotate",
        target="all_hosts",
        success=result.get("success", False),
        details={
            "host_count": len(result.get("results", [])),
            "verified_all": result.get("success", False),
            "cleanup_all": result.get("old_pubkey_removed_on_all", False),
            "error": result.get("error"),
        },
    )
    return jsonify(result)


@app.route("/api/public-key")
def api_public_key():
    key = get_public_key()
    return jsonify({"key": key})


# ---------------------------------------------------------------------------
# API: Host management
# ---------------------------------------------------------------------------

@app.route("/api/hosts", methods=["GET"])
def api_hosts():
    hosts = load_hosts()
    return jsonify(hosts)


@app.route("/api/hosts", methods=["POST"])
def api_add_host():
    data = request.json
    ok, msg = add_host(
        data.get("name", ""),
        data.get("address", ""),
        data.get("port", 22),
        data.get("user", "root"),
    )
    audit_log("host.add", target=data.get("address", ""), success=ok,
              host=data.get("address", ""),
              details={"name": data.get("name", ""), "user": data.get("user", "root")})
    return jsonify({"success": ok, "message": msg})


@app.route("/api/hosts", methods=["DELETE"])
def api_remove_host():
    data = request.json
    addr = data.get("address", "")
    ok, msg = remove_host(addr)
    audit_log("host.remove", target=addr, host=addr, success=ok)
    ssh_cache.invalidate_host(addr)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/hosts/test", methods=["POST"])
def api_test_host():
    data = request.json
    host = _find_host(data.get("address", ""))
    if not host:
        return jsonify({"success": False, "message": "Host not found"}), 404
    ok = test_connection(host)
    return jsonify({"success": ok, "message": "Connection OK" if ok else "Connection failed"})


# ---------------------------------------------------------------------------
# API: Pools
# ---------------------------------------------------------------------------

@app.route("/api/pools")
def api_pools():
    host, err, code = _require_host()
    if err:
        return err, code
    return jsonify(get_pools(host))


@app.route("/api/pools/status")
def api_pool_status():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool", "")
    return jsonify(get_pool_status(host, pool))


@app.route("/api/pools/iostat")
def api_pool_iostat():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool", "")
    return jsonify(get_pool_iostat(host, pool))


@app.route("/api/pools/scrub", methods=["POST"])
def api_pool_scrub():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    pool_name = data.get("pool", "")
    result = scrub_pool(host, pool_name)
    audit_log("pool.scrub", target=pool_name, host=host["address"],
              success=result.get("success", False))
    if result.get("success"):
        send_notification("scrub_started", "Scrub Started",
                          f"Pool: {pool_name}\nHost: {host['name']} ({host['address']})")
        # Start background monitor to detect scrub completion
        start_scrub_monitor(host, pool_name)
    return jsonify(result)


@app.route("/api/pools/history")
def api_pool_history():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool", "")
    return jsonify(get_pool_history(host, pool))


@app.route("/api/pools/check-upgrade")
def api_pool_check_upgrade():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool", "")
    return jsonify(check_pool_upgrade(host, pool))


@app.route("/api/pools/upgrade", methods=["POST"])
def api_pool_upgrade():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    pool = data.get("pool", "")
    result = upgrade_pool(host, pool)
    audit_log("pool.upgrade", target=pool, host=host["address"],
              success=result.get("success", False))
    if result.get("success"):
        send_notification("pool_error", "Pool Upgraded",
                          f"Pool: {pool}\nHost: {host['name']} ({host['address']})")
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Datasets
# ---------------------------------------------------------------------------

@app.route("/api/datasets")
def api_datasets():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool")
    return jsonify(get_datasets(host, pool))


@app.route("/api/datasets/properties")
def api_dataset_props():
    host, err, code = _require_host()
    if err:
        return err, code
    ds = request.args.get("dataset", "")
    return jsonify(get_dataset_properties(host, ds))


@app.route("/api/datasets/property", methods=["POST"])
def api_set_dataset_prop():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = set_dataset_property(host, data["dataset"], data["property"], data["value"])
    audit_log("dataset.set_property", target=data["dataset"], host=host["address"],
              success=result.get("success", False),
              details={"property": data["property"], "value": data["value"]})
    return jsonify(result)


@app.route("/api/datasets/create", methods=["POST"])
def api_create_dataset():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    opts = data.get("options")
    result = create_dataset(host, data["name"], opts)
    audit_log("dataset.create", target=data["name"], host=host["address"],
              success=result.get("success", False), details={"options": opts} if opts else None)
    return jsonify(result)


@app.route("/api/datasets/destroy", methods=["POST"])
def api_destroy_dataset():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    recursive = data.get("recursive", False)
    result = destroy_dataset(host, data["name"], recursive)
    audit_log("dataset.destroy", target=data["name"], host=host["address"],
              success=result.get("success", False),
              details={"recursive": recursive})
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Snapshots
# ---------------------------------------------------------------------------

@app.route("/api/snapshots")
def api_snapshots():
    host, err, code = _require_host()
    if err:
        return err, code
    ds = request.args.get("dataset")
    return jsonify(get_snapshots(host, ds))


@app.route("/api/snapshots/create", methods=["POST"])
def api_create_snapshot():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    snap_name = data.get("name", f"manual-{int(time.time())}")
    recursive = data.get("recursive", False)
    result = create_snapshot(host, data["dataset"], snap_name, recursive)
    audit_log("snapshot.create", target=f"{data['dataset']}@{snap_name}",
              host=host["address"], success=result.get("success", False),
              details={"recursive": recursive})
    if result.get("success"):
        send_notification("snapshot_created", "Snapshot Created",
                          f"Dataset: {data['dataset']}@{snap_name}\nHost: {host['name']} ({host['address']})")
    return jsonify(result)


@app.route("/api/snapshots/destroy", methods=["POST"])
def api_destroy_snapshot():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    recursive = data.get("recursive", False)
    result = destroy_snapshot(host, data["snapshot"], recursive)
    audit_log("snapshot.destroy", target=data["snapshot"], host=host["address"],
              success=result.get("success", False),
              details={"recursive": recursive})
    if result.get("success"):
        send_notification("snapshot_deleted", "Snapshot Deleted",
                          f"Snapshot: {data['snapshot']}\nHost: {host['name']} ({host['address']})")
    return jsonify(result)


@app.route("/api/snapshots/rollback", methods=["POST"])
def api_rollback_snapshot():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = rollback_snapshot(
        host, data["snapshot"],
        force=data.get("force", False),
        destroy_recent=data.get("destroy_recent", False),
        stop_guest=data.get("stop_guest", False),
        vmid=data.get("vmid"),
        vm_type=data.get("vm_type"),
    )
    audit_log("snapshot.rollback", target=data["snapshot"], host=host["address"],
              success=result.get("success", False),
              details={k: data.get(k) for k in
                       ("force", "destroy_recent", "stop_guest", "vmid", "vm_type")
                       if data.get(k) is not None})
    if result.get("success"):
        send_notification("rollback", "Rollback Performed",
                          f"Snapshot: {data['snapshot']}\nHost: {host['name']} ({host['address']})",
                          priority=8)
    return jsonify(result)


@app.route("/api/snapshots/clone", methods=["POST"])
def api_clone_snapshot():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = clone_snapshot(host, data["snapshot"], data["clone_name"])
    audit_log("snapshot.clone", target=data["snapshot"], host=host["address"],
              success=result.get("success", False),
              details={"clone_name": data["clone_name"]})
    return jsonify(result)


@app.route("/api/snapshots/clone-targets")
def api_clone_targets():
    host, err, code = _require_host()
    if err:
        return err, code
    from app.zfs_commands import get_clone_targets
    return jsonify(get_clone_targets(host))


@app.route("/api/snapshots/diff")
def api_diff_snapshot():
    host, err, code = _require_host()
    if err:
        return err, code
    snap1 = request.args.get("snapshot1", "")
    snap2 = request.args.get("snapshot2")
    return jsonify(diff_snapshot(host, snap1, snap2))


@app.route("/api/snapshots/send-size")
def api_send_size():
    host, err, code = _require_host()
    if err:
        return err, code
    snap = request.args.get("snapshot", "")
    return jsonify(estimate_send_size(host, snap))


# ---------------------------------------------------------------------------
# API: Auto-snapshot
# ---------------------------------------------------------------------------

@app.route("/api/auto-snapshot/status")
def api_auto_snap_status():
    host, err, code = _require_host()
    if err:
        return err, code
    return jsonify(get_auto_snapshot_status(host))


@app.route("/api/auto-snapshot/property")
def api_auto_snap_prop():
    host, err, code = _require_host()
    if err:
        return err, code
    ds = request.args.get("dataset", "")
    prop = get_auto_snapshot_property(host, ds)
    return jsonify({"dataset": ds, "value": prop["value"], "source": prop["source"]})


@app.route("/api/auto-snapshot/set", methods=["POST"])
def api_set_auto_snap():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    enabled = data.get("enabled", True)
    label = data.get("label")
    result = set_auto_snapshot(host, data["dataset"], enabled=enabled, label=label)
    audit_log("auto_snapshot.set", target=data["dataset"], host=host["address"],
              success=result.get("success", False),
              details={"enabled": enabled, "label": label})
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Proxmox VMs/CTs
# ---------------------------------------------------------------------------

@app.route("/api/pve/guests")
def api_pve_guests():
    host, err, code = _require_host()
    if err:
        return err, code
    vms = get_pve_vms(host)
    cts = get_pve_cts(host)
    return jsonify({"vms": vms, "cts": cts})


@app.route("/api/pve/guest-snapshots")
def api_pve_guest_snapshots():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool", "rpool")
    vmid = request.args.get("vmid", "")
    vm_type = request.args.get("type", "qemu")
    return jsonify(get_vm_snapshots(host, pool, vmid, vm_type))


# ---------------------------------------------------------------------------
# API: File-Level Restore (LXC)
# ---------------------------------------------------------------------------

@app.route("/api/restore/mount", methods=["POST"])
def api_restore_mount():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = snapshot_mount(host, data.get("snapshot", ""))
    return jsonify(result)


@app.route("/api/restore/unmount", methods=["POST"])
def api_restore_unmount():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = snapshot_unmount(host, data.get("clone_ds", ""))
    return jsonify(result)


@app.route("/api/restore/clones")
def api_restore_clones():
    host, err, code = _require_host()
    if err:
        return err, code
    from app.zfs_commands import list_restore_clones
    return jsonify(list_restore_clones(host))


@app.route("/api/restore/cleanup", methods=["POST"])
def api_restore_cleanup():
    host, err, code = _require_host()
    if err:
        return err, code
    from app.zfs_commands import cleanup_restore_clones
    return jsonify(cleanup_restore_clones(host))


@app.route("/api/restore/browse")
def api_restore_browse():
    host, err, code = _require_host()
    if err:
        return err, code
    mount_path = request.args.get("mount_path", "")
    subpath = request.args.get("path", "")
    return jsonify(snapshot_browse(host, mount_path, subpath))


@app.route("/api/restore/preview")
def api_restore_preview():
    host, err, code = _require_host()
    if err:
        return err, code
    mount_path = request.args.get("mount_path", "")
    file_path = request.args.get("file", "")
    return jsonify(snapshot_read_file(host, mount_path, file_path))


@app.route("/api/restore/file", methods=["POST"])
def api_restore_file():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = snapshot_restore_file(
        host,
        data.get("mount_path", ""),
        data.get("file_path", ""),
        data.get("dest_path", ""),
    )
    audit_log("restore.file", target=data.get("file_path", ""), host=host["address"],
              success=result.get("success", False),
              details={"dest": data.get("dest_path", "")})
    if result.get("success"):
        send_notification("rollback", "File Restored",
                          f"File: {data.get('file_path')}\nTo: {data.get('dest_path')}\nHost: {host['name']}",
                          priority=5)
    return jsonify(result)


@app.route("/api/restore/directory", methods=["POST"])
def api_restore_dir():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = snapshot_restore_dir(
        host,
        data.get("mount_path", ""),
        data.get("dir_path", ""),
        data.get("dest_path", ""),
    )
    audit_log("restore.dir", target=data.get("dir_path", ""), host=host["address"],
              success=result.get("success", False),
              details={"dest": data.get("dest_path", "")})
    if result.get("success"):
        send_notification("rollback", "Directory Restored",
                          f"Dir: {data.get('dir_path')}\nTo: {data.get('dest_path')}\nHost: {host['name']}",
                          priority=5)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Zvol File-Level Restore (VM volumes)
# ---------------------------------------------------------------------------

@app.route("/api/restore/zvol/mount", methods=["POST"])
@login_required
def api_zvol_mount():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = zvol_snapshot_mount(host, data.get("snapshot", ""))
    audit_log("zvol.mount", target=data.get("snapshot", ""), host=host["address"],
              success=result.get("success", False))
    return jsonify(result)


@app.route("/api/restore/zvol/partition", methods=["POST"])
@login_required
def api_zvol_partition_mount():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = zvol_partition_mount(host, data.get("device", ""), data.get("fstype", ""))
    return jsonify(result)


@app.route("/api/restore/zvol/unmount", methods=["POST"])
@login_required
def api_zvol_unmount():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    result = zvol_unmount(host, data.get("mount_path", ""), data.get("zvol_dev", ""))
    audit_log("zvol.unmount", target=data.get("mount_path", ""), host=host["address"],
              success=result.get("success", False))
    return jsonify(result)


@app.route("/api/restore/zvol/active")
@login_required
def api_zvol_active():
    host, err, code = _require_host()
    if err:
        return err, code
    return jsonify(zvol_list_active(host))


@app.route("/api/restore/zvol/cleanup", methods=["POST"])
@login_required
def api_zvol_cleanup():
    host, err, code = _require_host()
    if err:
        return err, code
    result = zvol_cleanup_all(host)
    audit_log("zvol.cleanup_all", target=host["address"], host=host["address"],
              success=result.get("success", False),
              details={"total_cleaned": result.get("total_cleaned", 0)})
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Health / Monitoring
# ---------------------------------------------------------------------------

@app.route("/api/health/arc")
def api_arc_stats():
    host, err, code = _require_host()
    if err:
        return err, code
    return jsonify(get_arc_stats(host))


@app.route("/api/health/events")
def api_zfs_events():
    host, err, code = _require_host()
    if err:
        return err, code
    return jsonify(get_zfs_events(host))


@app.route("/api/health/smart")
def api_smart():
    host, err, code = _require_host()
    if err:
        return err, code
    pool = request.args.get("pool", "") or None
    return jsonify(get_smart_status(host, pool))


@app.route("/api/health/snapshot-check")
@login_required
def api_snapshot_check():
    host, err, code = _require_host()
    if err:
        return err, code
    from app.snapshot_analysis import analyze_snapshots
    snap_age_data = get_snapshot_ages(host)
    auto_snap = get_auto_snapshot_status(host)
    retention_cfg = auto_snap.get("retention_policy", {})
    analysis = analyze_snapshots(snap_age_data, retention_cfg)
    analysis["retention_policy"] = retention_cfg
    return jsonify(analysis)


# ---------------------------------------------------------------------------
# API: Notifications
# ---------------------------------------------------------------------------

def _mask_secret(val):
    if not val or len(val) < 6:
        return val
    return val[:2] + "..." + val[-2:]


@app.route("/api/notifications/config", methods=["GET"])
def api_notify_config():
    cfg = load_notify_config()
    # Mask sensitive fields for the frontend
    if cfg.get("email", {}).get("smtp_password"):
        cfg["email"]["smtp_password"] = _mask_secret(cfg["email"]["smtp_password"])
    if cfg.get("telegram", {}).get("bot_token"):
        cfg["telegram"]["bot_token"] = _mask_secret(cfg["telegram"]["bot_token"])
    if cfg.get("gotify", {}).get("token"):
        cfg["gotify"]["token"] = _mask_secret(cfg["gotify"]["token"])
    if cfg.get("matrix", {}).get("access_token"):
        cfg["matrix"]["access_token"] = _mask_secret(cfg["matrix"]["access_token"])
    return jsonify(cfg)


@app.route("/api/notifications/config", methods=["POST"])
def api_save_notify_config():
    data = request.json or {}
    # Preserve masked secrets (when UI sends back "xx...yy", keep existing value)
    existing = load_notify_config()
    for section, field in (
        ("email", "smtp_password"),
        ("telegram", "bot_token"),
        ("gotify", "token"),
        ("matrix", "access_token"),
    ):
        new_val = (data.get(section) or {}).get(field, "")
        if new_val and "..." in new_val and len(new_val) < 32:
            data.setdefault(section, {})[field] = existing.get(section, {}).get(field, "")
    save_notify_config(data)
    audit_log("config.notifications.save", target="notifications", success=True,
              details={"channels": [k for k in ("email", "telegram", "gotify", "matrix")
                                    if (data.get(k) or {}).get("enabled")]})
    return jsonify({"success": True, "message": "Configuration saved"})


@app.route("/api/notifications/test/telegram", methods=["POST"])
def api_test_telegram():
    data = request.json
    result = test_telegram(data.get("bot_token", ""), data.get("chat_id", ""))
    return jsonify(result)


@app.route("/api/notifications/test/gotify", methods=["POST"])
def api_test_gotify():
    data = request.json
    result = test_gotify(data.get("url", ""), data.get("token", ""))
    return jsonify(result)


@app.route("/api/notifications/test/matrix", methods=["POST"])
def api_test_matrix():
    data = request.json
    # Allow masked token: resolve to stored value
    token = data.get("access_token", "")
    if token and "..." in token and len(token) < 32:
        token = load_notify_config().get("matrix", {}).get("access_token", "")
    result = test_matrix(
        data.get("homeserver", ""),
        token,
        data.get("room_id", ""),
    )
    return jsonify(result)


@app.route("/api/notifications/test/email", methods=["POST"])
def api_test_email():
    data = request.json or {}
    # Allow masked password: resolve to stored value
    pw = data.get("smtp_password", "")
    if pw and "..." in pw and len(pw) < 32:
        data["smtp_password"] = load_notify_config().get("email", {}).get("smtp_password", "")
    result = test_email(data)
    return jsonify(result)


@app.route("/api/notifications/send", methods=["POST"])
def api_send_notification():
    data = request.json
    result = send_notification(
        data.get("event_type", ""),
        data.get("title", "Test"),
        data.get("message", "Test notification"),
        data.get("priority", 5),
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: AI Reports
# ---------------------------------------------------------------------------

@app.route("/api/ai/config", methods=["GET"])
def api_ai_config():
    return jsonify(load_ai_config())


@app.route("/api/ai/config", methods=["POST"])
def api_save_ai_config_route():
    data = request.json
    save_ai_config(data)
    audit_log("config.ai.save", target="ai_reports", success=True,
              details={"provider": (data or {}).get("provider"),
                       "model": (data or {}).get("model")})
    start_ai_scheduler()
    return jsonify({"success": True, "message": "Configuration saved"})


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    result = test_ai_connection()
    return jsonify(result)


@app.route("/api/ai/ollama-models", methods=["POST"])
def api_ai_ollama_models():
    data = request.json or {}
    result = list_ollama_models(data.get("base_url"))
    return jsonify(result)


@app.route("/api/ai/report", methods=["POST"])
def api_ai_generate_report():
    data = request.json or {}
    host_address = data.get("host")
    lang = data.get("lang")
    result = generate_ai_report(host_address, lang)
    return jsonify(result)


@app.route("/api/ai/reports", methods=["GET"])
def api_ai_reports():
    return jsonify(load_ai_reports())


@app.route("/api/ai/schedules", methods=["GET"])
@login_required
def api_ai_schedules():
    """Return active AI report schedules with next-run times (for Health page)."""
    return jsonify({"schedules": get_active_schedules()})


@app.route("/api/ai/raw-data")
@login_required
def api_ai_raw_data():
    """Export the raw data that would be sent to the AI as JSON."""
    host_address = request.args.get("host", "")
    data = collect_host_data(host_address if host_address else None)
    if not data:
        return jsonify({"error": "No data collected"}), 404
    import json
    json_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    response = make_response(json_str)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=zfs-raw-data-{data.get('collected_at', 'export').replace(' ', '_').replace(':', '-')}.json"
    return response


@app.route("/api/ai/report/pdf/<report_id>")
def api_ai_report_pdf(report_id):
    """Generate a PDF from a stored AI report."""
    reports = load_ai_reports()
    report = None
    for r in reports:
        if r.get("id") == report_id:
            report = r
            break
    if not report:
        return jsonify({"error": "Report not found"}), 404

    try:
        from app.ai_pdf import generate_pdf
        pdf_bytes = generate_pdf(report)
    except Exception as e:
        import logging, traceback
        logging.getLogger(__name__).error("PDF generation failed: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500

    from flask import send_file
    from io import BytesIO
    filename = f"ZFS_Report_{report['timestamp'].replace(' ', '_').replace(':', '-')}.pdf"
    buf = BytesIO(bytes(pdf_bytes))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    data = request.json
    question = data.get("question", "")
    host_address = data.get("host")
    lang = data.get("lang")
    if not question.strip():
        return jsonify({"error": "Question required"}), 400
    result = ai_chat(question, host_address, lang)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Historical Metrics
# ---------------------------------------------------------------------------

@app.route("/api/metrics/pools")
@login_required
def api_metrics_pools():
    """List pools that have historical samples for a host."""
    host_addr = request.args.get("host", "")
    if not host_addr:
        return jsonify({"pools": []})
    return jsonify({"pools": list_metric_pools(host_addr)})


@app.route("/api/metrics/series")
@login_required
def api_metrics_series():
    """Return pool metric time-series. Query params: host, pool (optional), hours."""
    host_addr = request.args.get("host", "")
    pool = request.args.get("pool") or None
    try:
        hours = int(request.args.get("hours", "24"))
    except ValueError:
        hours = 24
    hours = max(1, min(hours, 24 * 365))  # clamp to 1h..1y
    if not host_addr:
        return jsonify({"error": "host required"}), 400
    rows = query_pool_series(host_addr, pool=pool, hours=hours)
    return jsonify({"host": host_addr, "pool": pool, "hours": hours, "data": rows})


@app.route("/api/metrics/summary")
@login_required
def api_metrics_summary():
    host_addr = request.args.get("host") or None
    return jsonify(metrics_summary(host_addr))


@app.route("/api/metrics/sample-now", methods=["POST"])
@login_required
def api_metrics_sample_now():
    """Trigger an immediate sample for a host (useful after adding a new host)."""
    data = request.json or {}
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    try:
        n = metrics_sample_host(host)
        return jsonify({"success": True, "pools_sampled": n})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Audit Log
# ---------------------------------------------------------------------------

@app.route("/api/audit")
@login_required
def api_audit():
    """Query the audit log. Filters: action, host, user, since, until, only_failures, limit, offset."""
    def _int(name, default):
        try:
            v = request.args.get(name)
            return int(v) if v not in (None, "") else default
        except ValueError:
            return default
    entries = audit_query(
        limit=_int("limit", 200),
        offset=_int("offset", 0),
        action=request.args.get("action") or None,
        host=request.args.get("host") or None,
        user=request.args.get("user") or None,
        since=_int("since", None),
        until=_int("until", None),
        only_failures=request.args.get("only_failures") in ("1", "true", "yes"),
    )
    total = audit_count(
        action=request.args.get("action") or None,
        host=request.args.get("host") or None,
        user=request.args.get("user") or None,
        since=_int("since", None),
        only_failures=request.args.get("only_failures") in ("1", "true", "yes"),
    )
    return jsonify({"entries": entries, "total": total,
                    "actions": audit_actions()})


# ---------------------------------------------------------------------------
# API: Cache (admin/ops visibility)
# ---------------------------------------------------------------------------

@app.route("/api/cache/stats")
@login_required
def api_cache_stats():
    return jsonify(ssh_cache.stats())


# ---------------------------------------------------------------------------
# API: Dashboard + Forecast
# ---------------------------------------------------------------------------

@app.route("/api/dashboard")
@login_required
def api_dashboard():
    from app.analytics import dashboard
    return jsonify(dashboard())


@app.route("/api/forecast")
@login_required
def api_forecast():
    from app.analytics import forecast_days_until_full
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    pool = request.args.get("pool")
    if not pool:
        return jsonify({"error": "pool parameter required"}), 400
    days = forecast_days_until_full(host["address"], pool)
    return jsonify({"host": host["address"], "pool": pool,
                    "days_until_full": days})


# ---------------------------------------------------------------------------
# Replication (bashclub-zsync)
# ---------------------------------------------------------------------------

@app.route("/api/replication/status")
@login_required
def api_replication_status():
    from app.replication import get_status
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    source = request.args.get("source") or None
    return jsonify(get_status(host, source=source))


@app.route("/api/replication/install", methods=["POST"])
@login_required
def api_replication_install():
    from app.replication import install
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    result = install(host)
    audit_log("replication.install", target=host["address"], host=host["address"],
              success=result["success"],
              details={"stderr_tail": (result.get("stderr") or "")[-500:]})
    return jsonify(result)


@app.route("/api/replication/config", methods=["GET"])
@login_required
def api_replication_config_get():
    from app.replication import read_config
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    source = request.args.get("source") or None
    cfg = read_config(host, source=source)
    return jsonify({
        "exists": cfg["exists"],
        "values": cfg["values"],
        "raw": cfg.get("raw", ""),
        "config_path": cfg.get("config_path"),
    })


@app.route("/api/replication/config", methods=["POST"])
@login_required
def api_replication_config_set():
    from app.replication import write_config
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    data = request.get_json(silent=True) or {}
    values = data.get("values") or {}
    if not isinstance(values, dict):
        return jsonify({"error": "values must be an object"}), 400
    # Coerce everything to string
    values = {str(k): ("" if v is None else str(v)) for k, v in values.items()}
    source = (data.get("source") or request.args.get("source") or values.get("source") or None)
    result = write_config(host, values, source=source)
    audit_log("replication.config.save", target=host["address"], host=host["address"],
              success=result["success"],
              details={"keys": sorted(values.keys()), "config_path": result.get("config_path")})
    return jsonify(result)


@app.route("/api/replication/run", methods=["POST"])
@login_required
def api_replication_run():
    """Trigger bashclub-zsync. Returns a task id; the client polls
    /api/replication/task?id=... for progress. The first sync of a large pool
    can run for hours, which would otherwise time out the HTTP request."""
    from app.replication import run_now_async
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    data = request.get_json(silent=True) or {}
    source = (data.get("source") or request.args.get("source") or None)
    task_id = run_now_async(host, source=source)
    audit_log("replication.run", target=host["address"], host=host["address"],
              success=True,
              details={"task_id": task_id, "source": source})
    return jsonify({"success": True, "task_id": task_id})


@app.route("/api/replication/task")
@login_required
def api_replication_task():
    from app.replication import get_task
    tid = (request.args.get("id") or "").strip()
    if not tid:
        return jsonify({"error": "id required"}), 400
    rec = get_task(tid)
    if not rec:
        return jsonify({"error": "task not found"}), 404
    # Strip the full log for the regular polling response; only return last few
    # entries so the wire size stays small.
    log_tail = (rec.get("log") or [])[-20:]
    return jsonify({
        "id": rec["id"],
        "name": rec.get("name"),
        "status": rec.get("status"),
        "progress": rec.get("progress"),
        "started_at": rec.get("started_at"),
        "finished_at": rec.get("finished_at"),
        "result": rec.get("result"),
        "error": rec.get("error"),
        "log_tail": log_tail,
    })


@app.route("/api/replication/bootstrap-ssh", methods=["POST"])
@login_required
def api_replication_bootstrap_ssh():
    from app.replication import bootstrap_ssh
    data = request.get_json(silent=True) or {}
    t_addr = (data.get("target") or "").strip()
    s_addr = (data.get("source") or "").strip()
    if not t_addr or not s_addr:
        return jsonify({"error": "target and source are required"}), 400
    if t_addr == s_addr:
        return jsonify({"error": "target and source must differ"}), 400
    target = _find_host(t_addr)
    source = _find_host(s_addr)
    if not target:
        return jsonify({"error": "Target host not found"}), 404
    if not source:
        return jsonify({"error": "Source host not found"}), 404
    result = bootstrap_ssh(target, source)
    audit_log("replication.bootstrap_ssh",
              target=f"{t_addr}<-{s_addr}", host=t_addr,
              success=result.get("success", False),
              details={"probe_ok": result.get("probe_ok"),
                       "key_generated": result.get("key_generated"),
                       "error": result.get("error")})
    return jsonify(result)


@app.route("/api/replication/create-target", methods=["POST"])
@login_required
def api_replication_create_target():
    from app.replication import create_target_dataset
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    data = request.get_json(silent=True) or {}
    dataset = (data.get("dataset") or "").strip()
    if not dataset:
        return jsonify({"error": "dataset is required"}), 400
    result = create_target_dataset(host, dataset)
    audit_log("replication.create_target", target=dataset, host=host["address"],
              success=result.get("success", False),
              details={"existed": result.get("existed"),
                       "created": result.get("created")})
    return jsonify(result)


@app.route("/api/replication/tagged-datasets")
@login_required
def api_replication_tagged_datasets():
    from app.replication import list_tagged_datasets
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    tag = request.args.get("tag") or "bashclub:zsync"
    return jsonify(list_tagged_datasets(host, tag))


@app.route("/api/replication/set-tags", methods=["POST"])
@login_required
def api_replication_set_tags():
    from app.replication import set_dataset_tags
    data = request.get_json(silent=True) or {}
    host_addr = (data.get("host") or "").strip()
    if not host_addr:
        return jsonify({"error": "host required"}), 400
    host = _find_host(host_addr)
    if not host:
        return jsonify({"error": "host not found"}), 404
    tag = (data.get("tag") or "bashclub:zsync").strip()
    value = (data.get("value") or "all").strip()
    enable = data.get("enable") or []
    disable = data.get("disable") or []
    if not isinstance(enable, list) or not isinstance(disable, list):
        return jsonify({"error": "enable and disable must be arrays"}), 400
    result = set_dataset_tags(host, tag, enable, disable, value)
    audit_log("replication.set_tags", target=host_addr, host=host_addr,
              success=result.get("success", False),
              details={"tag": tag, "enable": enable, "disable": disable})
    return jsonify(result)


@app.route("/api/replication/log")
@login_required
def api_replication_log():
    from app.replication import tail_log
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    try:
        lines = int(request.args.get("lines", 200))
    except (TypeError, ValueError):
        lines = 200
    return jsonify(tail_log(host, lines))


@app.route("/api/replication/config", methods=["DELETE"])
@login_required
def api_replication_config_delete():
    from app.replication import delete_config
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    source = request.args.get("source") or None
    purge = (request.args.get("purge") or "").lower() in ("1", "true", "yes")
    result = delete_config(host, source=source, purge_snapshots=purge)
    audit_log("replication.config.delete", target=host["address"], host=host["address"],
              success=result.get("success", False),
              details={"source": source, "purge_snapshots": purge,
                       "config_path": result.get("config_path"),
                       "target_dataset": result.get("target_dataset"),
                       "snapshots_purged": result.get("snapshots_purged"),
                       "error": result.get("error")})
    return jsonify(result)


@app.route("/api/replication/cron", methods=["GET"])
@login_required
def api_replication_cron_get():
    from app.replication import get_cron
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    source = request.args.get("source") or None
    return jsonify(get_cron(host, source=source))


@app.route("/api/replication/cron", methods=["POST"])
@login_required
def api_replication_cron_set():
    from app.replication import set_cron
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    data = request.get_json(silent=True) or {}
    schedule = (data.get("schedule") or "").strip()
    source = (data.get("source") or request.args.get("source") or None)
    log_path = (data.get("log_path") or "/var/log/bashclub-zsync.log").strip()
    if not schedule:
        return jsonify({"error": "schedule required"}), 400
    result = set_cron(host, schedule, source=source, log_path=log_path)
    audit_log("replication.cron.set", target=host["address"], host=host["address"],
              success=result.get("success", False),
              details={"schedule": schedule, "config_path": result.get("config_path")})
    return jsonify(result)


@app.route("/api/replication/cron", methods=["DELETE"])
@login_required
def api_replication_cron_delete():
    from app.replication import remove_cron
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    source = request.args.get("source") or None
    result = remove_cron(host, source=source)
    audit_log("replication.cron.remove", target=host["address"], host=host["address"],
              success=result.get("success", False))
    return jsonify(result)


@app.route("/api/replication/configs")
@login_required
def api_replication_configs():
    from app.replication import list_configs
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    return jsonify(list_configs(host))


@app.route("/api/replication/checkzfs")
@login_required
def api_replication_checkzfs():
    from app.replication import run_checkzfs
    host, err, code = _require_host()
    if err:
        return jsonify(err), code
    source = (request.args.get("source") or "").strip()
    if not source:
        return jsonify({"error": "source required"}), 400
    result = run_checkzfs(host, source)
    audit_log("replication.checkzfs", target=source, host=host["address"],
              success=result.get("success", False),
              details={"summary": result.get("summary", {})})
    return jsonify(result)


# ---------------------------------------------------------------------------
# Prometheus exporter — opt-in via PROMETHEUS_TOKEN env var
# ---------------------------------------------------------------------------

@app.route("/metrics")
def prometheus_endpoint():
    """Expose Prometheus text-format metrics.

    Disabled unless ``PROMETHEUS_TOKEN`` is set in the environment.
    The client must present ``Authorization: Bearer <token>`` or
    ``?token=<token>``. Compare in constant time.
    """
    token_cfg = os.environ.get("PROMETHEUS_TOKEN", "")
    if not token_cfg:
        return make_response("prometheus exporter disabled (set PROMETHEUS_TOKEN)\n",
                             404, {"Content-Type": "text/plain; charset=utf-8"})
    auth = request.headers.get("Authorization", "")
    supplied = ""
    if auth.startswith("Bearer "):
        supplied = auth[7:].strip()
    if not supplied:
        supplied = request.args.get("token", "")
    if not supplied or not hmac.compare_digest(supplied, token_cfg):
        return make_response("unauthorized\n", 401,
                             {"Content-Type": "text/plain; charset=utf-8"})

    from app.analytics import prometheus_metrics
    body = prometheus_metrics()
    return make_response(body, 200,
                         {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"})


@app.route("/api/cache/invalidate", methods=["POST"])
@login_required
def api_cache_invalidate():
    data = request.json or {}
    host = data.get("host")
    if host:
        ssh_cache.invalidate_host(host)
        audit_log("cache.invalidate", target=host, host=host)
    else:
        ssh_cache.invalidate_all()
        audit_log("cache.invalidate_all", target="*")
    return jsonify({"success": True})


# ---------------------------------------------------------------------------

# Start background services
try:
    start_ai_scheduler()
except Exception:
    pass
try:
    start_metrics_sampler()
except Exception:
    pass

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0")
