"""Wake-on-LAN for registered hosts.

The MAC of each host's management NIC is captured automatically while the
host is online (metrics sampler) and stored in hosts.json (``wol_mac``).
When the host is offline, the magic packet is sent two ways, because the
tool usually runs in a bridged Docker container whose broadcasts don't
reach the LAN:

  1. locally from the container (works with host networking / same L2), and
  2. relayed via every OTHER reachable registered host (python3 one-liner
     over SSH) -- a PVE box on the same LAN as the sleeping host.
"""

from __future__ import annotations

import re
import socket
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command, load_hosts, save_hosts

WOL_PORT = 9

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")


def parse_mac(mac: str) -> Optional[str]:
    """Normalize a MAC to lowercase colon form, or None if invalid."""
    m = (mac or "").strip()
    if not _MAC_RE.match(m):
        return None
    return m.replace("-", ":").lower()


def build_magic_packet(mac: str) -> Optional[bytes]:
    """6x 0xFF + 16x MAC -- the WOL magic packet (102 bytes)."""
    norm = parse_mac(mac)
    if norm is None:
        return None
    mac_bytes = bytes.fromhex(norm.replace(":", ""))
    return b"\xff" * 6 + mac_bytes * 16


def parse_iface_for_ip(ip_o4_output: str, ip: str) -> Optional[str]:
    """From ``ip -o -4 addr show`` output, the interface that carries ``ip``.

    Lines look like: ``2: enp3s0    inet 192.168.66.70/24 brd ... scope ...``
    """
    needle = ip + "/"
    for line in (ip_o4_output or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet" and parts[3].startswith(needle):
            return parts[1]
    return None


def fetch_host_mac(host: Dict[str, Any]) -> Optional[str]:
    """MAC of the NIC carrying the host's management IP (host must be up)."""
    addr = host.get("address", "")
    r = run_command(host, "ip -o -4 addr show 2>/dev/null", timeout=10)
    iface = parse_iface_for_ip(r.get("stdout") or "", addr)
    if not iface or not re.match(r"^[A-Za-z0-9@._-]+$", iface):
        return None
    iface = iface.split("@", 1)[0]   # VLAN etc.: enp3s0.20@enp3s0
    r = run_command(host, f"cat /sys/class/net/{iface}/address 2>/dev/null", timeout=10)
    return parse_mac((r.get("stdout") or "").strip())


def ensure_host_mac(host: Dict[str, Any]) -> None:
    """Capture + persist the host's MAC once, while it is reachable.
    Called from the metrics sampler; never raises."""
    try:
        if host.get("wol_mac"):
            return
        mac = fetch_host_mac(host)
        if not mac:
            return
        hosts = load_hosts()
        for entry in hosts:
            if entry.get("address") == host.get("address"):
                entry["wol_mac"] = mac
                save_hosts(hosts)
                host["wol_mac"] = mac
                return
    except Exception:
        pass


def send_wol_local(mac: str) -> bool:
    """Best-effort broadcast from the container itself."""
    pkt = build_magic_packet(mac)
    if pkt is None:
        return False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                s.sendto(pkt, ("255.255.255.255", WOL_PORT))
            return True
        finally:
            s.close()
    except OSError:
        return False


def send_wol_via_host(relay_host: Dict[str, Any], mac: str) -> bool:
    """Send the broadcast from another (online) host's LAN via python3."""
    norm = parse_mac(mac)
    if norm is None:
        return False
    hexmac = norm.replace(":", "")
    script = (
        "import socket\n"
        f"p = b'\\xff'*6 + bytes.fromhex('{hexmac}')*16\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)\n"
        f"[s.sendto(p, ('255.255.255.255', {WOL_PORT})) for _ in range(3)]\n"
        "print('__WOL_OK__')\n"
    )
    r = run_command(relay_host, f"python3 - <<'__EOF__'\n{script}__EOF__", timeout=15)
    return "__WOL_OK__" in (r.get("stdout") or "")


def wake(address: str) -> Dict[str, Any]:
    """Wake a registered host: local broadcast + relay via other hosts."""
    hosts = load_hosts()
    target = next((x for x in hosts if x.get("address") == address), None)
    if target is None:
        return {"success": False, "error": "host not found"}
    mac = parse_mac(target.get("wol_mac") or "")
    if mac is None:
        return {"success": False, "error": "no MAC known for this host yet "
                "(it is captured automatically while the host is online)"}

    sent_local = send_wol_local(mac)
    relays: List[Dict[str, Any]] = []
    for other in hosts:
        if other.get("address") == address:
            continue
        ok = send_wol_via_host(other, mac)
        relays.append({"host": other.get("address"), "ok": ok})

    any_sent = sent_local or any(rl["ok"] for rl in relays)
    return {"success": any_sent, "mac": mac, "sent_local": sent_local,
            "relays": relays,
            "error": "" if any_sent else "no send path succeeded"}
