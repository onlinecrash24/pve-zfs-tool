"""ZFS command wrappers executed via SSH on remote Proxmox hosts."""

from app.ssh_manager import run_command


# ---------------------------------------------------------------------------
# Pool operations
# ---------------------------------------------------------------------------

def get_pools(host):
    result = run_command(host, "zpool list -H -o name,size,alloc,free,fragmentation,capacity,health,dedupratio")
    if not result["success"]:
        return []
    pools = []
    for line in result["stdout"].strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 8:
            pools.append({
                "name": parts[0],
                "size": parts[1],
                "alloc": parts[2],
                "free": parts[3],
                "frag": parts[4],
                "cap": parts[5],
                "health": parts[6],
                "dedup": parts[7],
            })
    return pools


def get_pool_status(host, pool_name):
    result = run_command(host, f"zpool status {pool_name}")
    return result


def get_pool_iostat(host, pool_name):
    result = run_command(host, f"zpool iostat -v {pool_name}")
    return result


def scrub_pool(host, pool_name):
    return run_command(host, f"zpool scrub {pool_name}")


def check_pool_upgrade(host, pool_name):
    """Check if a zpool feature upgrade is available."""
    result = run_command(host, f"zpool status {pool_name}")
    if not result["success"]:
        return {"upgradable": False, "detail": result.get("stderr", "")}
    # zpool status shows upgrade notice when features are available
    stdout = result["stdout"]
    upgradable = ("can be upgraded" in stdout
                  or "Enable all features" in stdout
                  or "action: Some supported features" in stdout.lower()
                  if stdout else False)
    # Also check zpool upgrade output directly
    upgrade_result = run_command(host, f"zpool upgrade {pool_name} -n 2>&1 || true")
    upgrade_out = upgrade_result.get("stdout", "") + upgrade_result.get("stderr", "")
    if "already enabled" in upgrade_out or "up-to-date" in upgrade_out:
        upgradable = False
    elif "can be upgraded" in upgrade_out or "enable the following features" in upgrade_out.lower():
        upgradable = True
    return {"upgradable": upgradable, "detail": upgrade_out.strip()}


def upgrade_pool(host, pool_name):
    """Upgrade a zpool to enable all available features."""
    return run_command(host, f"zpool upgrade {pool_name}")


def get_pool_history(host, pool_name, limit=50):
    result = run_command(host, f"zpool history {pool_name} | tail -n {limit}")
    return result


# ---------------------------------------------------------------------------
# Dataset operations
# ---------------------------------------------------------------------------

def get_datasets(host, pool_name=None):
    cmd = "zfs list -H -o name,used,avail,refer,mountpoint,type,compression,compressratio"
    if pool_name:
        cmd += f" -r {pool_name}"
    result = run_command(host, cmd)
    if not result["success"]:
        return []
    datasets = []
    for line in result["stdout"].strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 8:
            datasets.append({
                "name": parts[0],
                "used": parts[1],
                "avail": parts[2],
                "refer": parts[3],
                "mountpoint": parts[4],
                "type": parts[5],
                "compression": parts[6],
                "compressratio": parts[7],
            })
    return datasets


def get_dataset_properties(host, dataset):
    result = run_command(host, f"zfs get all {dataset}")
    return result


def set_dataset_property(host, dataset, prop, value):
    return run_command(host, f"zfs set {prop}={value} {dataset}")


def create_dataset(host, name, options=None):
    cmd = f"zfs create"
    if options:
        for k, v in options.items():
            cmd += f" -o {k}={v}"
    cmd += f" {name}"
    return run_command(host, cmd)


def destroy_dataset(host, name, recursive=False):
    cmd = f"zfs destroy"
    if recursive:
        cmd += " -r"
    cmd += f" {name}"
    return run_command(host, cmd)


# ---------------------------------------------------------------------------
# Snapshot operations
# ---------------------------------------------------------------------------

def get_snapshots(host, dataset=None):
    cmd = "zfs list -t snapshot -H -o name,used,refer,creation -S creation"
    if dataset:
        cmd += f" -r {dataset}"
    result = run_command(host, cmd)
    if not result["success"]:
        return []
    # Fetch dataset types so we can tag each snapshot as filesystem or volume
    ds_types = {}
    type_result = run_command(host, "zfs list -H -o name,type")
    if type_result["success"]:
        for line in type_result["stdout"].strip().splitlines():
            p = line.split("\t")
            if len(p) >= 2:
                ds_types[p[0]] = p[1]
    snapshots = []
    for line in result["stdout"].strip().splitlines():
        parts = line.split("\t", 3)
        if len(parts) >= 4:
            full_name = parts[0]
            ds, snap = full_name.rsplit("@", 1) if "@" in full_name else (full_name, "")
            snapshots.append({
                "full_name": full_name,
                "dataset": ds,
                "snapshot": snap,
                "ds_type": ds_types.get(ds, "unknown"),
                "used": parts[1],
                "refer": parts[2],
                "creation": parts[3].strip(),
            })
    return snapshots


def create_snapshot(host, dataset, snap_name, recursive=False):
    cmd = "zfs snapshot"
    if recursive:
        cmd += " -r"
    cmd += f" {dataset}@{snap_name}"
    return run_command(host, cmd)


def destroy_snapshot(host, full_name, recursive=False):
    cmd = "zfs destroy"
    if recursive:
        cmd += " -r"
    cmd += f" {full_name}"
    result = run_command(host, cmd)
    # If snapshot has dependent clones (e.g. restore clones), retry with -R
    if not result["success"] and "dependent clones" in result.get("stderr", ""):
        result = run_command(host, f"zfs destroy -R {full_name}")
    return result


def rollback_snapshot(host, full_name, force=False, destroy_recent=False, stop_guest=False, vmid=None, vm_type=None):
    results = {"stopped": False, "started": False}

    # Stop VM/LXC before rollback if requested
    if stop_guest and vmid:
        stop_cmd = f"qm stop {vmid}" if vm_type == "qemu" else f"pct stop {vmid}"
        stop_result = run_command(host, stop_cmd)
        results["stopped"] = stop_result.get("success", False)
        results["stop_output"] = stop_result.get("stderr", "") or stop_result.get("stdout", "")

    cmd = "zfs rollback"
    if destroy_recent:
        cmd += " -r"
    if force:
        cmd += " -f"
    cmd += f" {full_name}"
    result = run_command(host, cmd)

    # Start VM/LXC after rollback if it was stopped
    if stop_guest and vmid and results["stopped"]:
        start_cmd = f"qm start {vmid}" if vm_type == "qemu" else f"pct start {vmid}"
        start_result = run_command(host, start_cmd)
        results["started"] = start_result.get("success", False)
        results["start_output"] = start_result.get("stderr", "") or start_result.get("stdout", "")

    result["guest_actions"] = results
    return result


def clone_snapshot(host, full_name, clone_name):
    """Clone a snapshot. If clone_name is on a different pool, use send/recv."""
    snap_pool = full_name.split("/")[0]
    clone_pool = clone_name.split("/")[0]
    if snap_pool == clone_pool:
        return run_command(host, f"zfs clone {full_name} {clone_name}")
    else:
        # Cross-pool: send | recv, then promote
        result = run_command(host, f"zfs send {full_name} | zfs recv {clone_name}")
        if not result["success"]:
            return result
        # The received dataset is a dependent clone, promote it to be independent
        promote = run_command(host, f"zfs promote {clone_name} 2>/dev/null")
        result["promoted"] = promote.get("success", False)
        return result


def get_clone_targets(host):
    """Get all pools and their top-level datasets as potential clone targets."""
    result = run_command(host, "zpool list -H -o name")
    if not result["success"]:
        return {"pools": [], "datasets": []}
    pools = []
    datasets = []
    for line in result["stdout"].strip().splitlines():
        pool = line.strip()
        if not pool:
            continue
        pools.append(pool)
        # Get sub-datasets one level deep
        ds_result = run_command(host, f"zfs list -H -o name -r -d 1 {pool}")
        if ds_result["success"]:
            for ds_line in ds_result["stdout"].strip().splitlines():
                ds = ds_line.strip()
                if ds and ds != pool:
                    datasets.append(ds)
    return {"pools": pools, "datasets": datasets}


def diff_snapshot(host, snapshot1, snapshot2=None):
    ds_name = snapshot1.rsplit("@", 1)[0] if "@" in snapshot1 else snapshot1
    snap_short = snapshot1.rsplit("@", 1)[1] if "@" in snapshot1 else ""
    type_check = run_command(host, f"zfs get -H -o value type {ds_name}")
    ds_type = type_check["stdout"].strip() if type_check["success"] else ""

    if ds_type == "volume":
        # zvol: use zfs send -nv and property comparisons
        output = f"=== Snapshot Info: {snapshot1} ===\n"
        # Get snapshot properties
        props = run_command(host, f"zfs get -H -o property,value used,referenced,written,creation {snapshot1}")
        if props["success"]:
            output += props["stdout"] + "\n"

        # Find previous snapshot for incremental comparison
        all_snaps = run_command(host, f"zfs list -t snapshot -H -o name,used,refer,creation -S creation -r {ds_name}")
        snap_list = []
        if all_snaps["success"]:
            for sline in all_snaps["stdout"].strip().splitlines():
                sparts = sline.split("\t", 3)
                if len(sparts) >= 4:
                    snap_list.append({"name": sparts[0], "used": sparts[1], "refer": sparts[2], "creation": sparts[3]})

        # Find current snapshot index and previous
        prev_snap = None
        curr_idx = -1
        for i, s in enumerate(snap_list):
            if s["name"] == snapshot1:
                curr_idx = i
                if i + 1 < len(snap_list):
                    prev_snap = snap_list[i + 1]["name"]  # list is newest-first
                break

        # Incremental send estimate (shows actual data change)
        if prev_snap:
            incr = run_command(host, f"zfs send -nvi {prev_snap} {snapshot1} 2>&1")
            if incr["success"] and incr["stdout"].strip():
                output += f"=== Data Changed (incremental from previous snapshot) ===\n{incr['stdout']}\n"
            else:
                output += f"=== Data Changed ===\n(Could not estimate incremental size)\n\n"
        else:
            # First snapshot - show full send size
            send_est = run_command(host, f"zfs send -nv {snapshot1} 2>&1")
            if send_est["success"] and send_est["stdout"].strip():
                output += f"=== Full Send Size (first snapshot) ===\n{send_est['stdout']}\n"

        # Show snapshot overview table
        if snap_list:
            output += f"=== All Snapshots ({len(snap_list)} total) ===\n"
            output += f"{'Name':<60} {'Used':>8} {'Refer':>8}  Created\n"
            output += "-" * 110 + "\n"
            for s in snap_list:
                marker = " <-- current" if s["name"] == snapshot1 else ""
                short_name = s["name"].rsplit("@", 1)[-1] if "@" in s["name"] else s["name"]
                output += f"{short_name:<60} {s['used']:>8} {s['refer']:>8}  {s['creation']}{marker}\n"

        return {"success": True, "stdout": output, "stderr": "", "is_zvol": True}

    # Filesystem: use zfs diff
    mount_check = run_command(host, f"zfs get -H -o value mounted {ds_name}")
    mounted = mount_check["stdout"].strip() if mount_check["success"] else ""
    if mounted == "no":
        return {
            "success": False,
            "stdout": "",
            "stderr": f"'{ds_name}' is not mounted. 'zfs diff' requires the dataset to be mounted.\n\nTry: zfs mount {ds_name}",
        }

    if snapshot2:
        cmd = f"zfs diff {snapshot1} {snapshot2}"
    else:
        cmd = f"zfs diff {snapshot1}"
    result = run_command(host, cmd)
    if result["success"] and not result["stdout"].strip():
        result["stdout"] = "(No changes since this snapshot)"
    result["is_zvol"] = False
    return result


# ---------------------------------------------------------------------------
# ZFS-auto-snapshot
# ---------------------------------------------------------------------------

def get_auto_snapshot_status(host):
    result = run_command(host, "which zfs-auto-snapshot 2>/dev/null && echo INSTALLED || echo NOT_INSTALLED")
    installed = "INSTALLED" in result.get("stdout", "")

    cron_result = run_command(host, "cat /etc/cron.d/zfs-auto-snapshot 2>/dev/null; crontab -l 2>/dev/null | grep zfs-auto-snapshot")
    timers_result = run_command(host, "systemctl list-timers --no-pager 2>/dev/null | grep zfs-auto-snapshot")

    return {
        "installed": installed,
        "cron_config": cron_result.get("stdout", ""),
        "timers": timers_result.get("stdout", ""),
    }


def get_auto_snapshot_property(host, dataset):
    result = run_command(host, f"zfs get com.sun:auto-snapshot {dataset} -H -o value,source")
    if result["success"]:
        parts = result["stdout"].strip().split("\t")
        value = parts[0] if parts else "-"
        source = parts[1] if len(parts) > 1 else "none"
        return {"value": value, "source": source}
    return {"value": "-", "source": "none"}


def set_auto_snapshot(host, dataset, enabled=True, label=None):
    prop = "com.sun:auto-snapshot"
    if label:
        prop += f":{label}"
    value = "true" if enabled else "false"
    return run_command(host, f"zfs set {prop}={value} {dataset}")


# ---------------------------------------------------------------------------
# Proxmox VM/CT helpers
# ---------------------------------------------------------------------------

def get_pve_vms(host):
    result = run_command(host, "qm list 2>/dev/null")
    vms = []
    if result["success"]:
        for line in result["stdout"].strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                vms.append({
                    "vmid": parts[0],
                    "name": parts[1],
                    "status": parts[2],
                    "type": "qemu",
                })
    return vms


def get_pve_cts(host):
    result = run_command(host, "pct list 2>/dev/null")
    cts = []
    if result["success"]:
        for line in result["stdout"].strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                cts.append({
                    "vmid": parts[0],
                    "status": parts[1],
                    "name": parts[2] if len(parts) > 2 else "",
                    "type": "lxc",
                })
    return cts


def get_vm_snapshots(host, pool, vmid, vm_type="qemu"):
    """Find ZFS snapshots belonging to a specific VM/CT."""
    prefix = f"subvol-{vmid}" if vm_type == "lxc" else f"vm-{vmid}"
    result = run_command(
        host,
        f"zfs list -t snapshot -H -o name,used,refer,creation -s creation -r {pool} 2>/dev/null | grep '{prefix}'"
    )
    if not result["success"] and not result["stdout"]:
        return []
    snapshots = []
    for line in result["stdout"].strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            full_name = parts[0]
            ds, snap = full_name.rsplit("@", 1) if "@" in full_name else (full_name, "")
            snapshots.append({
                "full_name": full_name,
                "dataset": ds,
                "snapshot": snap,
                "used": parts[1],
                "refer": parts[2],
                "creation": parts[3],
            })
    return snapshots


# ---------------------------------------------------------------------------
# File-Level Restore (LXC / filesystem snapshots)
# ---------------------------------------------------------------------------

RESTORE_MOUNT_BASE = "/tmp/zfs-tool-restore"


def snapshot_mount(host, snapshot):
    """Clone a snapshot and mount it readonly for file browsing."""
    ds_name = snapshot.rsplit("@", 1)[0] if "@" in snapshot else ""
    snap_short = snapshot.rsplit("@", 1)[1] if "@" in snapshot else ""

    # Only allow filesystem datasets
    type_check = run_command(host, f"zfs get -H -o value type {ds_name}")
    if type_check["success"] and type_check["stdout"].strip() != "filesystem":
        return {"success": False, "stderr": "File restore only works on filesystem datasets (LXC containers)."}

    clone_name = f"{ds_name}-restore-{snap_short}".replace("/", "-").replace("@", "-")
    clone_ds = f"{ds_name.split('/')[0]}/restore-{clone_name}"
    mount_path = f"{RESTORE_MOUNT_BASE}/{clone_name}"

    # Cleanup any previous clone with this name
    run_command(host, f"zfs destroy -r {clone_ds} 2>/dev/null")

    # Clone the snapshot (disable auto-snapshot to prevent children)
    clone_result = run_command(host, f"zfs clone -o mountpoint={mount_path} -o readonly=on -o com.sun:auto-snapshot=false {snapshot} {clone_ds}")
    if not clone_result["success"]:
        return {"success": False, "stderr": clone_result.get("stderr", "Clone failed")}

    return {
        "success": True,
        "clone_ds": clone_ds,
        "mount_path": mount_path,
        "snapshot": snapshot,
    }


def snapshot_unmount(host, clone_ds):
    """Destroy a restore clone and all its children (auto-snapshots) to clean up."""
    result = run_command(host, f"zfs destroy -r {clone_ds}")
    return result


def list_restore_clones(host):
    """List all leftover restore-* datasets."""
    result = run_command(host, "zfs list -H -o name,mountpoint,used,creation -t filesystem | grep '/restore-'")
    clones = []
    if result["success"] and result["stdout"].strip():
        for line in result["stdout"].strip().splitlines():
            parts = line.split("\t", 3)
            if len(parts) >= 4:
                clones.append({
                    "name": parts[0],
                    "mountpoint": parts[1],
                    "used": parts[2],
                    "creation": parts[3],
                })
    return clones


def cleanup_restore_clones(host):
    """Destroy all leftover restore-* datasets."""
    clones = list_restore_clones(host)
    destroyed = []
    errors = []
    for clone in clones:
        r = run_command(host, f"zfs destroy -r {clone['name']}")
        if r["success"]:
            destroyed.append(clone["name"])
        else:
            errors.append(f"{clone['name']}: {r.get('stderr', 'unknown error')}")
    return {"destroyed": destroyed, "errors": errors, "success": len(errors) == 0}


def snapshot_browse(host, mount_path, subpath=""):
    """List files/directories at a path inside a mounted snapshot."""
    full_path = f"{mount_path}/{subpath}".rstrip("/")
    # Sanitize path to prevent escaping
    if ".." in full_path:
        return {"success": False, "stderr": "Invalid path"}

    result = run_command(host, f"ls -la --time-style=long-iso '{full_path}' 2>/dev/null")
    if not result["success"]:
        return {"success": False, "stderr": result.get("stderr", "Cannot list directory")}

    entries = []
    for line in result["stdout"].strip().splitlines():
        if line.startswith("total"):
            continue
        parts = line.split(None, 7)
        if len(parts) >= 8:
            perms = parts[0]
            size = parts[4]
            date = f"{parts[5]} {parts[6]}"
            name = parts[7]
            if name in (".", ".."):
                continue
            entry_type = "dir" if perms.startswith("d") else ("link" if perms.startswith("l") else "file")
            entries.append({
                "name": name,
                "type": entry_type,
                "size": size,
                "date": date,
                "perms": perms,
            })
    return {"success": True, "entries": entries, "path": subpath or "/"}


def snapshot_read_file(host, mount_path, file_path):
    """Read a file from a mounted snapshot (for preview, max 100KB)."""
    full_path = f"{mount_path}/{file_path}".rstrip("/")
    if ".." in full_path:
        return {"success": False, "stderr": "Invalid path"}

    # Check file size first
    size_check = run_command(host, f"stat -c%s '{full_path}' 2>/dev/null")
    if size_check["success"]:
        size = int(size_check["stdout"].strip() or "0")
        if size > 102400:
            return {"success": False, "stderr": f"File too large for preview ({size} bytes). Use 'Restore' to download."}

    result = run_command(host, f"cat '{full_path}' 2>/dev/null")
    return result


def snapshot_restore_file(host, mount_path, file_path, dest_path):
    """Copy a file from the mounted snapshot back to the live filesystem."""
    src = f"{mount_path}/{file_path}".rstrip("/")
    if ".." in src or ".." in dest_path:
        return {"success": False, "stderr": "Invalid path"}

    # Create parent directory if needed
    run_command(host, f"mkdir -p \"$(dirname '{dest_path}')\"")

    result = run_command(host, f"cp -a '{src}' '{dest_path}'")
    return result


def snapshot_restore_dir(host, mount_path, dir_path, dest_path):
    """Recursively copy a directory from the mounted snapshot back to the live filesystem."""
    src = f"{mount_path}/{dir_path}".rstrip("/")
    if ".." in src or ".." in dest_path:
        return {"success": False, "stderr": "Invalid path"}

    run_command(host, f"mkdir -p '{dest_path}'")
    result = run_command(host, f"cp -a '{src}/.' '{dest_path}/'")
    return result


# ---------------------------------------------------------------------------
# Replication / Send-Receive
# ---------------------------------------------------------------------------

def estimate_send_size(host, snapshot):
    result = run_command(host, f"zfs send -nv {snapshot} 2>&1")
    return result


def estimate_incremental_size(host, snap_from, snap_to):
    result = run_command(host, f"zfs send -nvi {snap_from} {snap_to} 2>&1")
    return result


# ---------------------------------------------------------------------------
# Health & monitoring
# ---------------------------------------------------------------------------

def get_arc_stats(host):
    result = run_command(host, "cat /proc/spl/kstat/zfs/arcstats 2>/dev/null | grep -E '^(size|hits|misses|c_max)' ")
    return result


def get_zfs_events(host, limit=30):
    result = run_command(host, f"zpool events -v 2>/dev/null | tail -n {limit}")
    return result


def get_smart_status(host, pool_name):
    """Get SMART status of all disks in a pool."""
    vdevs = run_command(host, f"zpool status {pool_name} | grep -oP '/dev/\\S+'")
    if not vdevs["success"]:
        return {"success": False, "stderr": "Could not detect disks"}
    disks = vdevs["stdout"].strip().splitlines()
    results = {}
    for disk in disks:
        smart = run_command(host, f"smartctl -H {disk} 2>/dev/null | grep -i 'overall-health\\|result'")
        results[disk] = smart.get("stdout", "").strip()
    return {"success": True, "disks": results}
