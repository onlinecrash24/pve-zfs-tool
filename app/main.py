import os
import time
import functools
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

from app.ssh_manager import (
    load_hosts, add_host, remove_host, test_connection, get_public_key,
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
    estimate_send_size, estimate_incremental_size,
    get_arc_stats, get_zfs_events, get_smart_status,
)
from app.notifications import (
    load_config as load_notify_config,
    save_config as save_notify_config,
    send_notification, test_telegram, test_gotify, test_matrix,
)
from app.ai_reports import (
    load_config_masked as load_ai_config,
    save_config_unmasked as save_ai_config,
    test_connection as test_ai_connection,
    generate_report as generate_ai_report,
    load_reports as load_ai_reports,
    chat as ai_chat,
    start_scheduler as start_ai_scheduler,
    list_ollama_models,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "password")


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


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        session["authenticated"] = True
        session.permanent = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
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
    return jsonify({"success": ok, "message": msg})


@app.route("/api/hosts", methods=["DELETE"])
def api_remove_host():
    data = request.json
    ok, msg = remove_host(data.get("address", ""))
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
    result = upgrade_pool(host, data.get("pool", ""))
    if result.get("success"):
        send_notification("pool_error", "Pool Upgraded",
                          f"Pool: {data.get('pool')}\nHost: {host['name']} ({host['address']})")
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
    return jsonify(set_dataset_property(host, data["dataset"], data["property"], data["value"]))


@app.route("/api/datasets/create", methods=["POST"])
def api_create_dataset():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    opts = data.get("options")
    return jsonify(create_dataset(host, data["name"], opts))


@app.route("/api/datasets/destroy", methods=["POST"])
def api_destroy_dataset():
    data = request.json
    host = _find_host(data.get("host", ""))
    if not host:
        return jsonify({"error": "Host not found"}), 404
    return jsonify(destroy_dataset(host, data["name"], data.get("recursive", False)))


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
    result = create_snapshot(host, data["dataset"], snap_name, data.get("recursive", False))
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
    result = destroy_snapshot(host, data["snapshot"], data.get("recursive", False))
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
    return jsonify(clone_snapshot(host, data["snapshot"], data["clone_name"]))


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
    return jsonify(set_auto_snapshot(
        host, data["dataset"],
        enabled=data.get("enabled", True),
        label=data.get("label"),
    ))


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
    if result.get("success"):
        send_notification("rollback", "Directory Restored",
                          f"Dir: {data.get('dir_path')}\nTo: {data.get('dest_path')}\nHost: {host['name']}",
                          priority=5)
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


# ---------------------------------------------------------------------------
# API: Notifications
# ---------------------------------------------------------------------------

@app.route("/api/notifications/config", methods=["GET"])
def api_notify_config():
    return jsonify(load_notify_config())


@app.route("/api/notifications/config", methods=["POST"])
def api_save_notify_config():
    data = request.json
    save_notify_config(data)
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
    result = test_matrix(
        data.get("homeserver", ""),
        data.get("access_token", ""),
        data.get("room_id", ""),
    )
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
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500

    from flask import Response
    filename = f"ZFS_Report_{report['timestamp'].replace(' ', '_').replace(':', '-')}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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

# Start AI report scheduler if configured
try:
    start_ai_scheduler()
except Exception:
    pass

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
