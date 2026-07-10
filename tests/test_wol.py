"""Wake-on-LAN: magic packet construction, MAC capture parsing, and the
wake orchestration (local + relays). MAC validation guards the SSH relay
command against injection."""

import pytest
from app import wol


# --- parse_mac -------------------------------------------------------------

@pytest.mark.parametrize("raw,norm", [
    ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
    ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),
    (" 3c:ec:ef:12:34:56 ", "3c:ec:ef:12:34:56"),
])
def test_parse_mac_valid(raw, norm):
    assert wol.parse_mac(raw) == norm


@pytest.mark.parametrize("bad", [
    "", None, "aa:bb:cc:dd:ee", "aabbccddeeff", "gg:bb:cc:dd:ee:ff",
    "aa:bb:cc:dd:ee:ff; rm -rf /",
])
def test_parse_mac_invalid(bad):
    assert wol.parse_mac(bad) is None


# --- magic packet ----------------------------------------------------------

def test_magic_packet_structure():
    pkt = wol.build_magic_packet("aa:bb:cc:dd:ee:ff")
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes.fromhex("aabbccddeeff")
    assert pkt[6:] == bytes.fromhex("aabbccddeeff") * 16


def test_magic_packet_invalid_mac_is_none():
    assert wol.build_magic_packet("nope") is None


# --- iface parsing (MAC auto-capture) --------------------------------------

IP_O4 = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever
2: enp3s0    inet 192.168.66.70/24 brd 192.168.66.255 scope global enp3s0\\       valid_lft forever
3: vmbr0    inet 192.168.66.71/24 brd 192.168.66.255 scope global vmbr0\\       valid_lft forever
"""


def test_parse_iface_for_ip():
    assert wol.parse_iface_for_ip(IP_O4, "192.168.66.70") == "enp3s0"
    assert wol.parse_iface_for_ip(IP_O4, "192.168.66.71") == "vmbr0"


def test_parse_iface_no_prefix_confusion():
    # .7 must not match .70/.71
    assert wol.parse_iface_for_ip(IP_O4, "192.168.66.7") is None


# --- relay command ----------------------------------------------------------

def test_relay_sends_python_oneliner(monkeypatch):
    captured = {}

    def fake_run(host, cmd, timeout=30, **kw):
        captured["cmd"] = cmd
        return {"success": True, "stdout": "__WOL_OK__\n", "stderr": ""}

    monkeypatch.setattr(wol, "run_command", fake_run)
    ok = wol.send_wol_via_host({"address": "10.0.0.5"}, "AA:BB:CC:DD:EE:FF")
    assert ok is True
    assert "python3" in captured["cmd"]
    assert "aabbccddeeff" in captured["cmd"]
    assert "255.255.255.255" in captured["cmd"]


def test_relay_refuses_invalid_mac(monkeypatch):
    monkeypatch.setattr(wol, "run_command",
                        lambda *a, **k: pytest.fail("must not SSH with bad MAC"))
    assert wol.send_wol_via_host({"address": "10.0.0.5"}, "bad;mac") is False


# --- wake orchestration ------------------------------------------------------

def _hosts():
    return [
        {"address": "10.0.0.1", "name": "sleeper", "wol_mac": "aa:bb:cc:dd:ee:ff"},
        {"address": "10.0.0.2", "name": "relay1"},
        {"address": "10.0.0.3", "name": "relay2"},
    ]


def test_wake_uses_local_and_all_other_hosts(monkeypatch):
    monkeypatch.setattr(wol, "load_hosts", _hosts)
    monkeypatch.setattr(wol, "send_wol_local", lambda mac: True)
    relayed = []
    monkeypatch.setattr(wol, "send_wol_via_host",
                        lambda hst, mac: relayed.append(hst["address"]) or True)
    r = wol.wake("10.0.0.1")
    assert r["success"] is True
    assert r["sent_local"] is True
    assert relayed == ["10.0.0.2", "10.0.0.3"]   # target itself not used as relay


def test_wake_without_known_mac_fails_cleanly(monkeypatch):
    monkeypatch.setattr(wol, "load_hosts",
                        lambda: [{"address": "10.0.0.1", "name": "x"}])
    r = wol.wake("10.0.0.1")
    assert r["success"] is False
    assert "MAC" in r["error"]


def test_wake_unknown_host(monkeypatch):
    monkeypatch.setattr(wol, "load_hosts", lambda: [])
    r = wol.wake("1.2.3.4")
    assert r["success"] is False


def test_wake_succeeds_via_relay_when_local_fails(monkeypatch):
    monkeypatch.setattr(wol, "load_hosts", _hosts)
    monkeypatch.setattr(wol, "send_wol_local", lambda mac: False)
    monkeypatch.setattr(wol, "send_wol_via_host", lambda hst, mac: hst["address"] == "10.0.0.3")
    r = wol.wake("10.0.0.1")
    assert r["success"] is True
    assert r["sent_local"] is False
