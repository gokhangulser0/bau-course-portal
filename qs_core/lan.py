"""Local network (LAN) helpers: bind address, local IPv4 addresses and reachability checks."""
from __future__ import annotations

import ipaddress
import os
import socket

DEFAULT_PORT = 8501
LOCAL_ONLY = {"localhost", "127.0.0.1", "::1"}
ALL_INTERFACES = {"0.0.0.0", "::", ""}


def lan_command(port: int = DEFAULT_PORT) -> str:
    return f"python -m streamlit run app.py --server.address 0.0.0.0 --server.port {port}"


def binding(address: str | None) -> dict:
    """Interpret Streamlit's server.address option."""
    a = (address or "").strip()
    if a in LOCAL_ONLY:
        return {"mode": "local", "label": f"{a} (this computer only)", "lan": False,
                "detail": "The app only accepts connections from this computer. Other devices cannot open it."}
    if a in ("0.0.0.0", "::"):
        return {"mode": "all", "label": f"{a} (all network interfaces)", "lan": True,
                "detail": "The app accepts connections from other devices on the network."}
    if a == "":
        return {"mode": "default", "label": "not set (Streamlit default: all network interfaces)", "lan": True,
                "detail": "Streamlit listens on every interface by default, so the app is LAN-accessible. "
                          "Use --server.address 0.0.0.0 to make this explicit."}
    return {"mode": "specific", "label": f"{a} (one interface only)", "lan": True,
            "detail": f"The app only accepts connections that arrive on {a}."}


def _kind(ip: str) -> str:
    a = ipaddress.ip_address(ip)
    if a.is_loopback:
        return "Loopback"
    if a.is_link_local:
        return "Link-local (no DHCP address; usually not usable)"
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return "VPN / carrier-grade NAT (e.g. Tailscale)"
    if a.is_private:
        return "Private LAN"
    return "Public / other"


def local_ipv4s() -> list[dict]:
    """[{"ip", "kind", "primary"}] for this machine, the default-route address first."""
    found, primary = [], None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))          # UDP connect sends no packets; it only picks the outgoing interface
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    if primary:
        found.append(primary)
    host = socket.gethostname()
    try:
        for ip in socket.gethostbyname_ex(host)[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    out = []
    for ip in found:
        try:
            kind = _kind(ip)
        except ValueError:
            continue
        if kind == "Loopback":
            continue
        out.append({"ip": ip, "kind": kind, "primary": ip == primary})
    return out


def port_open(ip: str, port: int, timeout: float = 0.6) -> bool:
    """True when something accepts TCP connections on ip:port (checked from this computer;
    it does not prove that the firewall lets other devices in)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def on_streamlit_cloud() -> bool:
    return bool(os.environ.get("STREAMLIT_SHARING_MODE")) or os.getcwd().replace("\\", "/").startswith("/mount/src")
