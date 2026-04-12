import paramiko
import os
import json
import hashlib
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


def run_command(host, command):
    try:
        client = get_ssh_client(host)
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        return {"success": exit_code == 0, "stdout": out, "stderr": err}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


def test_connection(host):
    result = run_command(host, "echo ok")
    return result["success"]


def get_public_key():
    pub_key_path = SSH_KEY + ".pub"
    if os.path.exists(pub_key_path):
        with open(pub_key_path, "r") as f:
            return f.read().strip()
    return None
