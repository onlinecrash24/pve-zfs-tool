import paramiko
import os
import json
import hashlib
import shlex
import shutil
import subprocess
import threading
import logging

log = logging.getLogger(__name__)

DATA_DIR = "/app/data"
HOSTS_FILE = os.path.join(DATA_DIR, "hosts.json")
SSH_KEY = "/root/.ssh/id_ed25519"
KNOWN_HOSTS = os.path.join(DATA_DIR, "known_hosts")

_lock = threading.Lock()


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_hosts():
    _ensure_data_dir()
    if not os.path.exists(HOSTS_FILE):
        return []
    with open(HOSTS_FILE, "r") as f:
        return json.load(f)


def save_hosts(hosts):
    _ensure_data_dir()
    with _lock:
        with open(HOSTS_FILE, "w") as f:
            json.dump(hosts, f, indent=2)


def get_host_fingerprint(address, port=22):
    """Fetch SSH host key fingerprint from a remote host (TOFU step 1)."""
    try:
        transport = paramiko.Transport((address, int(port)))
        transport.connect()
        key = transport.get_remote_server_key()
        transport.close()
        fp = hashlib.sha256(key.asbytes()).hexdigest()
        fp_display = ":".join(fp[i:i+2] for i in range(0, 32, 2))  # first 16 bytes
        return {
            "success": True,
            "key_type": key.get_name(),
            "fingerprint": f"SHA256:{fp_display}",
            "raw_key": key,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def add_host(name, address, port=22, user="root"):
    hosts = load_hosts()
    for h in hosts:
        if h["address"] == address:
            return False, "Host already exists"

    # Fetch and store host key on first add (Trust On First Use)
    fp_result = get_host_fingerprint(address, port)
    if fp_result.get("success") and fp_result.get("raw_key"):
        _ensure_data_dir()
        host_keys = paramiko.HostKeys()
        if os.path.exists(KNOWN_HOSTS):
            try:
                host_keys.load(KNOWN_HOSTS)
            except Exception:
                pass
        raw_key = fp_result["raw_key"]
        host_keys.add(address, raw_key.get_name(), raw_key)
        host_keys.save(KNOWN_HOSTS)
        log.info("Stored host key for %s (%s)", address, fp_result.get("fingerprint", "?"))

    hosts.append({
        "name": name,
        "address": address,
        "port": int(port),
        "user": user,
    })
    save_hosts(hosts)
    return True, "Host added"


def remove_host(address):
    hosts = load_hosts()
    hosts = [h for h in hosts if h["address"] != address]
    save_hosts(hosts)
    return True, "Host removed"


def get_ssh_client(host):
    client = paramiko.SSHClient()
    # Load known hosts for host key verification
    if os.path.exists(KNOWN_HOSTS):
        try:
            client.load_host_keys(KNOWN_HOSTS)
        except Exception:
            pass
    # Warn if unknown key, but still allow (TOFU: key stored at add_host time)
    client.set_missing_host_key_policy(paramiko.WarningPolicy())
    try:
        client.connect(
            hostname=host["address"],
            port=host.get("port", 22),
            username=host.get("user", "root"),
            key_filename=SSH_KEY,
            timeout=10,
        )
    except paramiko.ssh_exception.SSHException as e:
        # If host key changed, this is a potential MITM — reject
        if "not found in known_hosts" in str(e).lower() or "does not match" in str(e).lower():
            raise ConnectionError(f"SSH host key verification failed for {host['address']}: {e}")
        raise
    # Save any new host keys
    try:
        client.get_host_keys().save(KNOWN_HOSTS)
    except Exception:
        pass
    return client


def run_command(host, command, timeout=30, cache_ttl=0):
    """Run a command on a remote host via SSH.

    Parameters
    ----------
    host : dict
        Host entry (from hosts.json).
    command : str
        Shell command to execute.
    timeout : int
        Command timeout in seconds (default 30).
    cache_ttl : int
        If > 0, cache successful results for this many seconds, keyed by
        (host address, command). Reads hit the cache before SSH-ing.
        Writes should not pass a TTL and should invalidate the host cache
        via ``app.cache.invalidate_host`` after a successful write.
    """
    # Cache lookup (read-only fast path)
    if cache_ttl > 0:
        from app.cache import get as _cache_get
        cached = _cache_get(host["address"], command)
        if cached is not None:
            return cached

    try:
        client = get_ssh_client(host)
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        result = {"success": exit_code == 0, "stdout": out, "stderr": err}
    except Exception as e:
        result = {"success": False, "stdout": "", "stderr": str(e)}

    # Only cache successful results — don't poison the cache with errors
    if cache_ttl > 0 and result.get("success"):
        from app.cache import set as _cache_set
        _cache_set(host["address"], command, result, cache_ttl)

    return result


def test_connection(host):
    result = run_command(host, "echo ok")
    return result["success"]


def get_public_key():
    pub_key_path = SSH_KEY + ".pub"
    if os.path.exists(pub_key_path):
        with open(pub_key_path, "r") as f:
            return f.read().strip()
    return None


# ---------------------------------------------------------------------------
# SSH Key Rotation
# ---------------------------------------------------------------------------

def _append_authorized_key(host, pubkey):
    """Append a public key to ~/.ssh/authorized_keys idempotently."""
    cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        f"grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys || "
        f"echo {shlex.quote(pubkey)} >> ~/.ssh/authorized_keys"
    )
    return run_command(host, cmd)


def _remove_authorized_key(host, pubkey):
    """Remove an exact-match public key line from ~/.ssh/authorized_keys."""
    cmd = (
        f"if grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys 2>/dev/null; then "
        f"  grep -vxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys > ~/.ssh/.ak_new && "
        f"  mv ~/.ssh/.ak_new ~/.ssh/authorized_keys && "
        f"  chmod 600 ~/.ssh/authorized_keys; "
        f"fi"
    )
    return run_command(host, cmd)


def rotate_ssh_keys():
    """Rotate the tool's SSH key across all configured hosts.

    Flow (safe — never locks us out):
      1. Generate new Ed25519 keypair into a temp path.
      2. For each host: append the NEW public key to authorized_keys
         while the OLD key is still active. Abort on first failure
         and roll back any already-deployed new keys.
      3. Swap files: old → id_ed25519.old(.pub), new → id_ed25519(.pub).
      4. Verify the new key works on each host. On success, remove the
         old public key from that host's authorized_keys.
      5. Invalidate the SSH command cache so fresh connections are used.
    """
    from app.cache import invalidate_all

    old_pub = get_public_key() or ""
    tmp_key = "/tmp/pvezfs_rotate_key"

    # Clean temp files from a previous abort
    for p in (tmp_key, tmp_key + ".pub"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    # 1. Generate new keypair
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", tmp_key, "-N", "",
             "-C", "pvezfs-tool-rotated"],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return {"success": False, "error": "ssh-keygen not available in container"}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"ssh-keygen failed: {e.stderr or e.stdout}"}

    try:
        with open(tmp_key + ".pub") as f:
            new_pub = f.read().strip()
    except Exception as e:
        return {"success": False, "error": f"cannot read new public key: {e}"}

    hosts = load_hosts()
    if not hosts:
        # No hosts configured — just swap locally
        try:
            for suf in ("", ".pub"):
                if os.path.exists(SSH_KEY + suf):
                    shutil.move(SSH_KEY + suf, SSH_KEY + suf + ".old")
                shutil.move(tmp_key + suf, SSH_KEY + suf)
            os.chmod(SSH_KEY, 0o600)
        except Exception as e:
            return {"success": False, "error": f"local swap failed: {e}"}
        return {"success": True, "new_pubkey": new_pub, "results": [],
                "note": "No hosts configured; key rotated locally only."}

    # 2. Deploy new pubkey to all hosts (old key still active)
    results = []
    deploy_ok = True
    for h in hosts:
        r = _append_authorized_key(h, new_pub)
        ok = r.get("success", False)
        results.append({
            "host": h["address"], "name": h.get("name", h["address"]),
            "deploy": ok,
            "deploy_error": ("" if ok else (r.get("stderr") or r.get("stdout") or "unknown error")),
            "verify": None, "cleanup": None,
        })
        if not ok:
            deploy_ok = False

    if not deploy_ok:
        # Roll back: remove new_pub from hosts where we succeeded
        for h, res in zip(hosts, results):
            if res["deploy"]:
                _remove_authorized_key(h, new_pub)
        for p in (tmp_key, tmp_key + ".pub"):
            try:
                os.remove(p)
            except OSError:
                pass
        return {
            "success": False,
            "error": "Deployment failed on one or more hosts; rolled back",
            "results": results,
        }

    # 3. Swap local files
    try:
        for suf in ("", ".pub"):
            # Remove any stale .old from a previous rotation
            if os.path.exists(SSH_KEY + suf + ".old"):
                os.remove(SSH_KEY + suf + ".old")
            if os.path.exists(SSH_KEY + suf):
                shutil.move(SSH_KEY + suf, SSH_KEY + suf + ".old")
            shutil.move(tmp_key + suf, SSH_KEY + suf)
        os.chmod(SSH_KEY, 0o600)
    except Exception as e:
        return {
            "success": False,
            "error": f"local key swap failed: {e}",
            "results": results,
            "warning": "New key is deployed to hosts but local swap failed — old key still active.",
        }

    # 4. Verify new key works on every host, then remove old pubkey
    invalidate_all()  # drop any cached SSH results tied to old auth

    all_verified = True
    for h, res in zip(hosts, results):
        ok = test_connection(h)
        res["verify"] = ok
        if ok and old_pub:
            cr = _remove_authorized_key(h, old_pub)
            res["cleanup"] = cr.get("success", False)
        else:
            res["cleanup"] = False
            if not ok:
                all_verified = False

    return {
        "success": all_verified,
        "new_pubkey": new_pub,
        "old_pubkey_removed_on_all": all(r["cleanup"] for r in results),
        "results": results,
        "warning": None if all_verified else (
            "New key does not authenticate on some hosts — old key was kept "
            "on those hosts so you are not locked out. Investigate and re-run "
            "rotation or fix authorized_keys manually."
        ),
    }
