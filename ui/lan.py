"""Local Network (LAN) Test panel: open and test the app from a phone, tablet or another PC."""
from __future__ import annotations

import platform

import pandas as pd
import streamlit as st

import os

from qs_core import lan, pyenv


def _option(name, default=None):
    try:
        v = st.get_option(name)
        return default if v is None else v
    except Exception:
        return default


def render():
    """Shown in the Local Desktop UI. It checks the web portal (app.py), which runs separately on port 8501;
    the desktop UI itself stays bound to 127.0.0.1."""
    ui_head = st.columns([3, 1])
    ui_head[0].caption("Start the web portal with run_lan.bat, then open it from other devices on the same Wi-Fi or "
                       "wired network (phone, tablet, another PC) to check it before publishing.")
    if lan.on_streamlit_cloud():
        st.info("This copy is running on Streamlit Cloud, which already has a public URL. The LAN test is for "
                "a copy running on your own computer.")
    port = int(ui_head[1].number_input("Web portal port", 1024, 65535, lan.DEFAULT_PORT, 1, key="lan_port"))
    ips = lan.local_ipv4s()
    local_up = lan.port_open("127.0.0.1", port)
    lan_up = any(lan.port_open(i["ip"], port) for i in ips)
    c1, c2, c3 = st.columns(3)
    c1.metric("Web portal running", "Yes" if local_up else "No", help=f"Checked on http://localhost:{port}")
    c2.metric("Reachable on the network", "Yes" if lan_up else "No",
              help="Yes means the portal listens on a network address (started with --server.address 0.0.0.0).")
    c3.metric("Host name", platform.node() or "—")
    if not local_up:
        st.info(f"The web portal is not running on port {port}. Start it with run_lan.bat (or the command below) "
                f"and press R to refresh this page.")
    elif not lan_up:
        st.warning("The web portal is running but only for this computer (bound to localhost). Restart it with "
                   "run_lan.bat so other devices can open it.")
    else:
        st.success("The web portal is reachable from this computer's network addresses.")

    python_section(port)

    st.markdown("##### Addresses to open on the other device")
    if not ips:
        st.warning("No network address was found. Connect this computer to Wi-Fi or Ethernet and reload.")
    else:
        rows = []
        for i in ips:
            url = f"http://{i['ip']}:{port}"
            ok = lan.port_open(i["ip"], port)
            rows.append({"Address": i["ip"], "Type": i["kind"] + (" · default route" if i["primary"] else ""),
                         "URL": url, "Server answers on it": "Yes" if ok else "No"})
        best = next((r for r, i in zip(rows, ips) if i["primary"] and i["kind"] == "Private LAN"),
                    next((r for r, i in zip(rows, ips) if i["kind"] == "Private LAN"), rows[0]))
        st.markdown(f"**Most likely URL:** [{best['URL']}]({best['URL']})")
        st.code(best["URL"], language=None)
        if not lan_up:
            st.caption("These URLs work once the web portal is started with the LAN command below.")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={"URL": st.column_config.LinkColumn("URL")})
        st.caption("'Server answers on it' is checked from this computer. A 'Yes' means the app is listening on "
                   "that address; if another device still cannot connect, the firewall or the network is blocking it.")
    st.caption(f"On this computer you can always use http://localhost:{port}")

    st.markdown("##### Start the app for LAN testing")
    st.write("These commands work even when typing `python` alone gives \"Python was not found\". Run them in "
             "the `quiz_system_claude` folder. The launcher finds a Python interpreter with Streamlit installed "
             "(local .venv, Conda, `py -3`, PATH, standard install folders), offers to set one up if needed, "
             "prints the LAN URLs and starts the app on 0.0.0.0:8501. Stop the server with Ctrl+C.")
    for desc, cmd in pyenv.launch_commands(lan.DEFAULT_PORT):
        st.caption(desc)
        st.code(cmd, language="powershell" if cmd.startswith(("&", "powershell")) else "bat")
    st.write("To make LAN mode the default, create `.streamlit/config.toml` with:")
    st.code('[server]\naddress = "0.0.0.0"\nport = 8501', language="toml")

    st.markdown("##### Troubleshooting")
    st.markdown(
        "- **Same network:** the phone or tablet must be on the same Wi-Fi as this computer. Turn off mobile "
        "data and VPNs on both devices. Guest Wi-Fi networks and routers with *AP / client isolation* block "
        "devices from seeing each other.\n"
        "- **Windows firewall:** when Windows asks, allow **Python** on **Private networks**. Or run this in an "
        "Administrator terminal:\n"
        "  `netsh advfirewall firewall add rule name=\"Streamlit 8501\" dir=in action=allow protocol=TCP "
        "localport=8501 profile=private`\n"
        "- **Network profile:** the Wi-Fi must be set to **Private**, not Public (Settings → Network & Internet → "
        "Wi-Fi → your network), or in PowerShell as Administrator: "
        "`Set-NetConnectionProfile -InterfaceAlias \"Wi-Fi\" -NetworkCategory Private`\n"
        "- **macOS:** System Settings → Network → Firewall → Options → allow incoming connections for Python. "
        "**Linux (ufw):** `sudo ufw allow 8501/tcp`\n"
        "- **'Python was not found':** Windows is running the Microsoft Store shortcut instead of a real Python. "
        "Use `run_lan.bat` or the exact-interpreter command above, or turn off the 'python.exe' / 'python3.exe' "
        "entries under Settings → Apps → Advanced app settings → App execution aliases. If no Python is installed "
        "at all, install it with `winget install -e --id Python.Python.3.12` (or from python.org with "
        "'Add python.exe to PATH' ticked), then run `run_lan.bat -Setup` to create `.venv` with the requirements.\n"
        "- **Use http://**, not https://, and include the port (`:8501`).\n"
        "- **Port busy:** start with `--server.port 8502` and use that port in the URL.\n"
        "- **Address changed:** the router can give this computer a new IP after a restart; reload this panel.\n"
        "- **Mixed results:** try every address in the table; the 'Private LAN' one on the default route "
        "usually works.")
    st.warning("Anyone on the same network can open the app while LAN mode is on, including the password-protected "
               "Instructor Dashboard login page. Avoid LAN mode on public Wi-Fi, and set a strong "
               "INSTRUCTOR_PASSWORD.")


@st.cache_data(ttl=600, show_spinner=False)
def _discover():
    return pyenv.discover()


def python_section(port: int):
    st.markdown("##### Python environment")
    cur = pyenv.current()
    launcher = os.environ.get("QS_LAUNCHER")
    p1, p2, p3 = st.columns(3)
    p1.metric("Python", cur["version"])
    p2.metric("Streamlit", cur["streamlit"] or "not found")
    p3.metric("Started by", launcher or "manual command")
    st.write("Interpreter running this app:")
    st.code(cur["executable"], language=None)
    st.caption(f"Environment: {cur['environment']}. Use this exact path in the commands below if `python` is not "
               f"on PATH.")
    if st.button("Scan for other Python installations", key="lan_scan"):
        st.session_state["lan_scan"] = True
    if st.session_state.get("lan_scan"):
        with st.spinner("Checking Python interpreters..."):
            found = _discover()
        if found:
            st.dataframe(pd.DataFrame([{"Interpreter": f["executable"], "Python": f["version"],
                                        "Streamlit": f["streamlit"] or "not installed", "Found via": f["source"],
                                        "Start with": f["command"]} for f in found]),
                         use_container_width=True, hide_index=True)
            if not any(f["streamlit"] for f in found):
                st.warning("None of these interpreters has Streamlit. Run `run_lan.bat -Setup` to create a local "
                           ".venv with every requirement.")
        else:
            st.warning("No other working Python interpreter was found.")
