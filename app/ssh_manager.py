import paramiko
import os
import json
import threading

DATA_DIR = "/app/data"
HOSTS_FILE = os.path.join(DATA_DIR, "hosts.json")
SSH_KEY = "/root/.ssh/id_ed25519"

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


def add_host(name, address, port=22, user="root"):
    hosts = load_hosts()
    for h in hosts:
        if h["address"] == address:
            return False, "Host already exists"
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
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host["address"],
        port=host.get("port", 22),
        username=host.get("user", "root"),
        key_filename=SSH_KEY,
        timeout=10,
    )
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
