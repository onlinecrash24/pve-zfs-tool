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
        parts = line.split("\t")
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
                "creation": parts[3],
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
    return run_command(host, cmd)


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
    return run_command(host, f"zfs clone {full_name} {clone_name}")


def diff_snapshot(host, snapshot1, snapshot2=None):
    # zfs diff only works on filesystem datasets, not on zvols (volumes).
    ds_name = snapshot1.rsplit("@", 1)[0] if "@" in snapshot1 else snapshot1
    type_check = run_command(host, f"zfs get -H -o value type {ds_name}")
    ds_type = type_check["stdout"].strip() if type_check["success"] else ""
    if ds_type == "volume":
        return {
            "success": False,
            "stdout": "",
            "stderr": f"'{ds_name}' is a zvol (volume). 'zfs diff' only works on filesystem datasets.\n\nUse 'Rollback' instead to restore the volume to this snapshot state.",
        }

    # zfs diff also requires the dataset to be mounted
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
    # Provide helpful message on empty diff
    if result["success"] and not result["stdout"].strip():
        result["stdout"] = "(No changes since this snapshot)"
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
