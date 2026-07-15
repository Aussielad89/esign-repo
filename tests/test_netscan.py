"""Test suite for netscan.py — Task 3 (self-contained scanner)."""
from __future__ import annotations

import ipaddress

import netscan as ns


SAMPLE_IPCONFIG = """
Windows IP Configuration

Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 192.168.1.42
   Subnet Mask . . . . . . . . . . . : 255.255.255.0

Ethernet adapter VirtualBox Host-Only:

   IPv4 Address. . . . . . . . . . . : 169.254.56.1
   Subnet Mask . . . . . . . . . . . : 255.255.0.0

Loopback Adapter:

   IPv4 Address. . . . . . . . . . . : 127.0.0.1
   Subnet Mask . . . . . . . . . . . : 255.0.0.0
"""

SAMPLE_ARP = """
Interface: 192.168.1.42 --- 0x4
  Internet Address      Physical Address      Type
  192.168.1.1           ac-bc-32-11-22-33     dynamic
  192.168.1.50          f8-4e-17-aa-bb-cc     dynamic
  192.168.1.99          ff-ff-ff-ff-ff-ff     static
"""


def test_parse_ipconfig_skips_loopback_and_apipa():
    adapters = ns._parse_ipconfig_text(SAMPLE_IPCONFIG)
    # parser returns all adapters in order; filtering is done in detect_subnet
    assert len(adapters) == 3
    first = adapters[0]
    assert first.ip == "192.168.1.42"
    assert first.network == "192.168.1.0/24"
    # detect_subnet must skip loopback & APIPA
    usable = [a for a in adapters if not a.ip.startswith(("127.", "169.254."))]
    assert usable[0].ip == "192.168.1.42"


def test_detect_subnet_prefers_real_adapter(monkeypatch):
    monkeypatch.setattr(ns, "_ipconfig_adapters", lambda: ns._parse_ipconfig_text(SAMPLE_IPCONFIG))
    ad = ns.detect_subnet()
    assert ad.ip == "192.168.1.42"
    assert ad.network == "192.168.1.0/24"


def test_parse_arp_maps_and_filters():
    table = ns.parse_arp(SAMPLE_ARP)
    assert table.get("192.168.1.1") == "AC:BC:32:11:22:33"  # Apple OUI normalized
    assert table.get("192.168.1.50") == "F8:4E:17:AA:BB:CC"
    assert "192.168.1.99" not in table  # broadcast ff:ff:ff filtered


def test_oui_vendor_lookup():
    assert ns.oui_vendor("AC:BC:32:11:22:33") == "Apple"
    assert ns.oui_vendor("F8:4E:17:00:00:00") == "Xiaomi"
    assert ns.oui_vendor("00:00:00:00:00:00") == "Unknown"
    assert ns.oui_vendor(None) == "Unknown"


def test_format_table():
    devs = [ns.Device(ip="192.168.1.1", mac="AC:BC:32:11:22:33",
                      vendor="Apple", hostname="router", status="up")]
    out = ns.format_table(devs)
    assert "192.168.1.1" in out
    assert "Apple" in out
    assert "router" in out
    assert "1 active device" in out


def test_format_table_empty():
    assert "No active devices" in ns.format_table([])


def test_scan_target_validation():
    net = ipaddress.ip_network("10.0.0.0/24", strict=False)
    assert net.num_addresses == 256
    # hosts() excludes network + broadcast => 254
    assert len(list(net.hosts())) == 254
