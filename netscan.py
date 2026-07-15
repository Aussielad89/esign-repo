"""netscan.py — Task 3: fast self-contained Wi-Fi subnet scanner.

Pure standard library. Detects the active adapter + subnet on Windows, pings
every host concurrently (under ~10s), then resolves MAC / manufacturer /
hostname via the system ARP table and reverse DNS.

Run:
    python netscan.py                 # auto-detect subnet, scan it
    python netscan.py -t 10.0.0.0/24  # explicit target CIDR
    python netscan.py -w 200          # more worker threads
    python netscan.py --json          # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Built-in OUI -> vendor map (offline, no network needed). Covers the most
# common consumer/enterprise device vendors. Unknown OUIs show as "Unknown".
# ---------------------------------------------------------------------------

OUI_MAP = {
    "AC:BC:32": "Apple", "A4:83:E7": "Apple", "F0:18:98": "Apple",
    "3C:22:FB": "Apple", "D0:03:4B": "Apple", "C8:E0:EB": "Apple",
    "FC:F1:36": "Apple", "00:25:00": "Apple", "3C:5A:B4": "Apple",
    "8C:85:90": "Apple", "CC:08:FB": "Apple",
    "60:21:C0": "Samsung", "8C:B6:4E": "Samsung", "5C:0A:5B": "Samsung",
    "78:1B:4C": "Samsung", "F4:6B:8C": "Samsung", "D0:43:1E": "Samsung",
    "98:6B:3B": "Samsung", "40:4E:36": "Samsung",
    "F8:8F:CA": "Google", "AC:22:0B": "Google", "3C:28:6D": "Google",
    "9C:B6:D0": "Google", "DA:A1:19": "Google", "F4:CF:E2": "Google",
    "18:64:72": "Microsoft", "D0:EA:14": "Microsoft", "98:5F:D1": "Microsoft",
    "3C:99:4E": "Microsoft", "48:8F:5A": "Microsoft",
    "54:EE:75": "Huawei", "AC:E2:EC": "Huawei", "F0:79:60": "Huawei",
    "88:25:93": "Huawei", "50:FD:8C": "Huawei", "C4:0B:CB": "Huawei",
    "64:09:C8": "Xiaomi", "28:6C:07": "Xiaomi", "F8:4E:17": "Xiaomi",
    "8C:2D:AA": "Xiaomi", "34:CE:00": "Xiaomi", "DC:5C:4C": "Xiaomi",
    "A4:77:33": "Sony", "00:1D:0D": "Sony", "28:BD:89": "Sony",
    "E4:11:5B": "LG", "C0:65:00": "LG", "00:3E:0C": "LG",
    "C4:12:F5": "Cisco", "00:1B:0C": "Cisco", "F4:CA:E5": "Cisco",
    "B0:7E:11": "TP-Link", "50:C7:BF": "TP-Link", "14:CF:92": "TP-Link",
    "84:16:F9": "TP-Link", "A0:63:91": "TP-Link",
    "C0:FF:D4": "Netgear", "B0:7F:B9": "Netgear", "EC:71:DB": "Netgear",
    "F2:18:98": "Netgear", "00:26:F2": "Netgear",
    "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "24:0A:C4": "Raspberry Pi",
    "24:6F:28": "Espressif (ESP)", "AC:67:B2": "Espressif (ESP)", "84:CC:A8": "Espressif (ESP)",
    "3C:71:BF": "Espressif (ESP)", "68:C6:3A": "Espressif (ESP)",
    "00:0C:29": "VMware", "00:50:56": "VMware", "00:05:69": "VMware",
    "00:1A:79": "Ubiquiti", "FC:EC:DA": "Ubiquiti", "B4:FB:E4": "Ubiquiti",
    "F0:9F:C2": "Amazon", "68:37:E9": "Amazon", "A0:02:DC": "Amazon",
    "B0:FC:36": "ASUS", "04:D9:F5": "ASUS", "AC:1B:EA": "ASUS",
    "1C:1B:0D": "Dell", "F0:4D:A2": "Dell", "14:FE:B5": "Dell",
    "3C:52:82": "Intel", "00:1F:3C": "Intel", "98:FA:9B": "Intel",
    "00:23:AB": "Synology", "00:11:32": "Synology", "90:09:D0": "Synology",
    "00:1C:42": "Parallels", "00:1C:14": "QEMU", "52:54:00": "QEMU/KVM",
    "00:50:56": "VMware", "F2:3C:91": "Nintendo", "CC:FB:65": "Nintendo",
    "94:DB:56": "Sonos", "5C:AA:FD": "Sonos", "48:A6:B8": "Sonos",
    "B0:38:29": "Roomba/iRobot", "60:6F:45": "iRobot",
    "00:18:DD": "Roku", "B0:A3:86": "Roku", "DC:1C:46": "Roku",
    "78:DD:08": "Chromecast", "54:60:09": "Chromecast", "EC:AD:B8": "Chromecast",
}


# ---------------------------------------------------------------------------
# Subnet detection (Windows ipconfig parse, with fallbacks)
# ---------------------------------------------------------------------------

@dataclass
class AdapterInfo:
    name: str
    ip: str
    netmask: str
    network: str  # CIDR e.g. 192.168.1.0/24

    def cidr(self) -> str:
        return self.network


def _parse_ipconfig_text(text: str) -> list[AdapterInfo]:
    """Parse ipconfig output text into adapters with IPv4 + mask."""
    adapters: list[AdapterInfo] = []
    cur_name = "(unknown)"
    cur_ip = None
    cur_mask = None
    for line in text.splitlines():
        s = line.strip()
        if "adapter" in s.lower() and s.endswith(":"):
            # flush previous adapter
            if cur_ip and cur_mask:
                adapters.append(_make_adapter(cur_name, cur_ip, cur_mask))
            # name is the text before "adapter"
            head = s.split("adapter", 1)[0].strip()
            cur_name = head if head else "(unknown)"
            cur_ip = cur_mask = None
            continue
        if s.startswith("IPv4 Address"):
            val = s.split(":", 1)[1].strip()
            cur_ip = val.rstrip("()")  # strip any (Preferred)
            # Windows prints "IPv4 Address. . . . . : 1.2.3.4" — grab last token.
            if "." in cur_ip:
                cur_ip = cur_ip.split()[-1]
        elif s.startswith("Subnet Mask"):
            cur_mask = s.split(":", 1)[1].strip()
    if cur_ip and cur_mask:
        adapters.append(_make_adapter(cur_name, cur_ip, cur_mask))
    return adapters


def _ipconfig_adapters() -> list[AdapterInfo]:
    """Run `ipconfig` and parse its output into adapters."""
    out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=15)
    return _parse_ipconfig_text(out.stdout)


def _make_adapter(name: str, ip: str, mask: str) -> AdapterInfo:
    net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
    return AdapterInfo(name=name, ip=ip, netmask=mask, network=str(net))


def detect_subnet() -> AdapterInfo:
    """Pick the best active adapter (skip loopback & APIPA 169.254.*)."""
    adapters = _ipconfig_adapters()
    usable = [a for a in adapters if not a.ip.startswith(("127.", "169.254."))]
    if usable:
        return usable[0]
    if adapters:
        return adapters[0]
    # Last-resort fallback: derive a /24 from the socket IP.
    ip = _socket_ip()
    net = ipaddress.ip_network(f"{ip}/24", strict=False)
    return AdapterInfo(name="(fallback)", ip=ip, netmask="255.255.255.0", network=str(net))


def _socket_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Host discovery
# ---------------------------------------------------------------------------

def ping_host(ip: str, timeout_ms: int = 800) -> bool:
    """Return True if the host replies to a single ICMP echo (Windows)."""
    exe = shutil.which("ping") or "ping"
    try:
        res = subprocess.run(
            [exe, "-n", "1", "-w", str(timeout_ms), ip],
            capture_output=True, text=True, timeout=max(2, timeout_ms / 1000 + 2),
        )
    except Exception:
        return False
    out = (res.stdout + res.stderr).lower()
    return "reply from" in out and "timed out" not in out and "ttl=" in out


def parse_arp(text: str | None = None) -> dict[str, str]:
    """Return {ip: mac} from `arp -a` (or from provided `text` in tests)."""
    if text is None:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=15)
        text = out.stdout
    table: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and _looks_like_ip(parts[0]) and _looks_like_mac(parts[1]):
            mac = parts[1].replace("-", ":").upper()
            if mac != "FF:FF:FF:FF:FF:FF" and mac != "00:00:00:00:00:00":
                table[parts[0]] = mac
    return table


def _looks_like_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _looks_like_mac(s: str) -> bool:
    return s.count("-") == 5 or s.count(":") == 5


def oui_vendor(mac: str | None) -> str:
    if not mac:
        return "Unknown"
    # Normalise to colon form: AC:BC:32
    prefix = mac.upper().replace("-", ":")[:8]
    return OUI_MAP.get(prefix, "Unknown")


def resolve_hostname(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


@dataclass
class Device:
    ip: str
    mac: str | None = None
    vendor: str = "Unknown"
    hostname: str | None = None
    status: str = "up"


def scan(network: str, timeout_ms: int = 800, workers: int = 100,
         resolve_names: bool = True) -> list[Device]:
    net = ipaddress.ip_network(network, strict=False)
    # Skip network + broadcast addresses for host scanning.
    hosts = [str(h) for h in net.hosts()]
    devices: list[Device] = []

    def _probe(ip: str) -> Device | None:
        if ping_host(ip, timeout_ms):
            return Device(ip=ip, status="up")
        return None

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(hosts)))) as ex:
        futures = {ex.submit(_probe, ip): ip for ip in hosts}
        for fut in as_completed(futures):
            d = fut.result()
            if d:
                devices.append(d)

    # MAC resolution (one ARP table read after pings populate it).
    arp = parse_arp()
    name_lock = threading.Lock()

    def _enrich(d: Device) -> None:
        d.mac = arp.get(d.ip)
        d.vendor = oui_vendor(d.mac)
        if resolve_names:
            d.hostname = resolve_hostname(d.ip)

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(devices)))) as ex:
        list(ex.map(_enrich, devices))

    devices.sort(key=lambda d: ipaddress.ip_address(d.ip))
    return devices


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def format_table(devices: list[Device]) -> str:
    if not devices:
        return "No active devices found."
    cols = [("IP ADDRESS", 16), ("MAC ADDRESS", 20), ("VENDOR", 18),
            ("HOSTNAME", 28), ("STATUS", 7)]
    lines = []
    lines.append("  ".join(h.ljust(w) for h, w in cols))
    lines.append("  ".join("-" * w for _, w in cols))
    for d in devices:
        row = [
            d.ip.ljust(16),
            (d.mac or "?").ljust(20),
            d.vendor.ljust(18),
            (d.hostname or "-").ljust(28),
            d.status.ljust(7),
        ]
        lines.append("  ".join(row))
    total = len(devices)
    lines.append("")
    lines.append(f"  {total} active device(s).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast LAN subnet scanner (stdlib only)")
    parser.add_argument("-t", "--target", default=None,
                        help="Target CIDR, e.g. 192.168.1.0/24 (default: auto-detect)")
    parser.add_argument("-w", "--workers", type=int, default=120, help="Ping worker threads")
    parser.add_argument("--timeout", type=int, default=800, help="Per-host ping timeout (ms)")
    parser.add_argument("--no-names", action="store_true", help="Skip reverse-DNS hostname resolution")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args(argv)

    if args.target:
        net = args.target
        print(f"Target: {net}")
    else:
        ad = detect_subnet()
        net = ad.network
        print(f"Detected adapter: {ad.name} ({ad.ip} / {ad.netmask})")
        print(f"Scanning subnet : {net}\n")

    import time as _t
    start = _t.time()
    devices = scan(net, timeout_ms=args.timeout, workers=args.workers,
                   resolve_names=not args.no_names)
    elapsed = _t.time() - start

    if args.json:
        print(json.dumps([d.__dict__ for d in devices], indent=2))
    else:
        print(format_table(devices))
        print(f"  Scan completed in {elapsed:.2f}s across {len(list(ipaddress.ip_network(net, strict=False).hosts()))} hosts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
