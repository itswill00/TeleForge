import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import base64
from datetime import datetime, timezone
import json
import logging
import os
import re
import socket
import shutil
import struct
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin
import requests

from pyrogram import Client, raw
from pyrogram.errors import RPCError
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

logger = logging.getLogger("pygramx.network")

TELEGRAM_DCS = {
    1: {"name": "DC1 Pluto", "location": "Miami, USA", "ip": "149.154.175.50", "port": 443},
    2: {"name": "DC2 Venus", "location": "Amsterdam, NL", "ip": "149.154.167.51", "port": 443},
    3: {"name": "DC3 Aurora", "location": "Miami, USA", "ip": "149.154.175.100", "port": 443},
    4: {"name": "DC4 Vesta", "location": "Amsterdam, NL", "ip": "149.154.167.91", "port": 443},
    5: {"name": "DC5 Flora", "location": "Singapore, SG", "ip": "91.108.56.165", "port": 443},
}

TCP_STATES = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
}

async def _probe_dc_tcp(ip: str, port: int = 443, timeout: float = 2.5) -> Optional[float]:
    """Measure single TCP handshake round-trip latency in milliseconds."""
    t0 = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        writer.close()
        await writer.wait_closed()
        return latency
    except Exception:
        return None

def _hex_to_ipv4(hex_str: str) -> str:
    """Convert 8-char little-endian hex to dotted decimal IPv4."""
    try:
        ip_bytes = bytes.fromhex(hex_str)[::-1]
        return socket.inet_ntoa(ip_bytes)
    except Exception:
        return "0.0.0.0"

def _hex_to_ipv6(hex_str: str) -> str:
    """Convert 32-char host-endian hex to IPv6 or unwrapped IPv4."""
    try:
        words = [int(hex_str[i:i + 8], 16) for i in range(0, 32, 8)]
        packed = struct.pack("<4I", *words)
        addr = socket.inet_ntop(socket.AF_INET6, packed)
        if addr.startswith("::ffff:"):
            return addr.split("::ffff:")[-1]
        return addr
    except Exception:
        return "::"

def _read_proc_net(filename: str) -> Optional[str]:
    """Read /proc/net file directly or with root fallback if restricted by Android SELinux."""
    full_path = f"/proc/net/{filename}"
    if os.path.exists(full_path):
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except PermissionError:
            pass

    try:
        res = subprocess.run(
            ["su", "-c", f"cat {full_path}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout
    except Exception:
        pass
    return None

def _get_socket_process_map() -> Dict[str, Tuple[str, str]]:
    """
    Map socket inode numbers to (PID, ProcessName).
    Executes fast Python inspection via su if root is available, otherwise scans local /proc.
    """
    fast_script = (
        "import os, json\n"
        "res = {}\n"
        "for pid in os.listdir('/proc'):\n"
        "    if not pid.isdigit(): continue\n"
        "    fd_dir = f'/proc/{pid}/fd'\n"
        "    try:\n"
        "        fds = os.listdir(fd_dir)\n"
        "    except Exception:\n"
        "        continue\n"
        "    pname = 'unknown'\n"
        "    try:\n"
        "        with open(f'/proc/{pid}/cmdline', 'r', errors='ignore') as f:\n"
        "            raw_cmd = f.read().split(chr(0))[0].strip()\n"
        "            if raw_cmd: pname = raw_cmd.split('/')[-1]\n"
        "    except Exception: pass\n"
        "    if pname == 'unknown':\n"
        "        try:\n"
        "            with open(f'/proc/{pid}/comm', 'r') as f: pname = f.read().strip()\n"
        "        except Exception: pass\n"
        "    for fd in fds:\n"
        "        try:\n"
        "            link = os.readlink(f'{fd_dir}/{fd}')\n"
        "            if link.startswith('socket:['):\n"
        "                res[link[8:-1]] = (pid, pname)\n"
        "        except Exception:\n"
        "            continue\n"
        "print(json.dumps(res))\n"
    )

    # 1. Try root helper for system-wide inode mapping
    py_path = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    if py_path and os.path.isfile(py_path):
        try:
            proc = subprocess.run(
                ["su", "-c", f"{py_path} -c \"{fast_script}\""],
                capture_output=True,
                text=True,
                timeout=2.5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout.strip())
        except Exception:
            pass

    # 2. Unprivileged fallback
    fallback_map: Dict[str, Tuple[str, str]] = {}
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            fd_dir = f"/proc/{pid}/fd"
            try:
                fds = os.listdir(fd_dir)
            except Exception:
                continue
            pname = "unknown"
            try:
                with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="ignore") as f:
                    pname = f.read().strip()
            except Exception:
                pass
            for fd in fds:
                try:
                    link = os.readlink(f"{fd_dir}/{fd}")
                    if link.startswith("socket:["):
                        fallback_map[link[8:-1]] = (pid, pname)
                except Exception:
                    continue
    except Exception:
        pass

    return fallback_map

@on_cmd(["dcinfo", "dcs", "mtproto"], desc="Show Telegram connection details, active datacenter, and ping times", usage="")
async def dcinfo_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Checking connection to Telegram servers...`")

    # Concurrent MTProto queries and TCP probes
    async def probe_all_dcs():
        tasks = []
        for dc_id, meta in sorted(TELEGRAM_DCS.items()):
            tasks.append(_probe_dc_tcp(meta["ip"], meta["port"]))
        return await asyncio.gather(*tasks)

    nearest_task = asyncio.create_task(client.invoke(raw.functions.help.GetNearestDc()))
    config_task = asyncio.create_task(client.invoke(raw.functions.help.GetConfig()))
    probe_task = asyncio.create_task(probe_all_dcs())

    nearest_res = None
    config_res = None
    probe_res = []

    try:
        nearest_res = await nearest_task
    except Exception as e:
        logger.debug("GetNearestDc error: %s", e)

    try:
        config_res = await config_task
    except Exception as e:
        logger.debug("GetConfig error: %s", e)

    try:
        probe_res = await probe_task
    except Exception as e:
        logger.debug("DC probe error: %s", e)

    current_dc_id = getattr(config_res, "this_dc", None)
    if not current_dc_id:
        current_dc_id = getattr(client, "session", None) and getattr(client.session, "dc_id", 0)

    # Server time difference
    drift_str = "Unknown"
    server_time_str = "Unknown"
    if config_res and getattr(config_res, "date", None):
        srv_time = config_res.date
        diff = srv_time - time.time()
        drift_str = f"{diff:+.2f}s"
        server_time_str = datetime.fromtimestamp(srv_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Resolve primary gateway IP
    gateway_ip = None
    if config_res and getattr(config_res, "dc_options", None):
        for opt in config_res.dc_options:
            if opt.id == current_dc_id and not getattr(opt, "ipv6", False) and not getattr(opt, "media_only", False):
                gateway_ip = f"{opt.ip_address}:{opt.port}"
                break

    dc_meta = TELEGRAM_DCS.get(current_dc_id, {})
    dc_name = dc_meta.get("name", f"DC{current_dc_id}")
    dc_loc = dc_meta.get("location", "Unknown")

    lines = [
        "**Telegram Connection Info**",
        f"• **Active Datacenter:** {dc_name} ({dc_loc}) [ID: `{current_dc_id}`]",
    ]

    if nearest_res:
        n_id = getattr(nearest_res, "nearest_dc", 0)
        n_meta = TELEGRAM_DCS.get(n_id, {})
        n_name = n_meta.get("name", f"DC{n_id}")
        country = getattr(nearest_res, "country", "Unknown")
        lines.append(f"• **Nearest Datacenter:** {n_name} [Country: `{country}`]")

    if gateway_ip:
        lines.append(f"• **Primary Gateway:** `{gateway_ip}` (TCP)")

    lines.append(f"• **Server Clock:** `{server_time_str}` (drift: `{drift_str}`)")

    if config_res:
        msg_max = getattr(config_res, "message_length_max", 4096)
        cap_max = getattr(config_res, "caption_length_max", 1024)
        grp_max = getattr(config_res, "megagroup_size_max", 200000)
        lines.append(f"• **Limits:** Message `{msg_max}` | Caption `{cap_max}` | Megagroup `{grp_max}`")

    lines.append("\n**Datacenter Ping Times:**")
    dc_ids = sorted(TELEGRAM_DCS.keys())
    for idx, d_id in enumerate(dc_ids):
        d_meta = TELEGRAM_DCS[d_id]
        lat = probe_res[idx] if idx < len(probe_res) else None
        lat_str = f"`{lat:.1f} ms`" if lat is not None else "`Timeout`"
        active_tag = " **[Active]**" if d_id == current_dc_id else ""
        lines.append(f"• {d_meta['name']} ({d_meta['location']}): {lat_str}{active_tag}")

    await edit_or_reply(status_msg, "\n".join(lines))

@on_cmd(["pingdc", "dcprobe"], desc="Check connection speed to all Telegram datacenters", usage="")
async def pingdc_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Pinging Telegram servers...`")

    samples: Dict[int, List[float]] = {d: [] for d in TELEGRAM_DCS}

    # Run 3 consecutive probe rounds
    for _ in range(3):
        tasks = [_probe_dc_tcp(TELEGRAM_DCS[d]["ip"], TELEGRAM_DCS[d]["port"]) for d in sorted(TELEGRAM_DCS)]
        results = await asyncio.gather(*tasks)
        for idx, d in enumerate(sorted(TELEGRAM_DCS)):
            val = results[idx]
            if val is not None:
                samples[d].append(val)
        await asyncio.sleep(0.3)

    lines = [
        "**Telegram Datacenter Ping Test**",
        "Tested 3 times per server:\n",
    ]

    best_dc = None
    best_avg = 999999.0

    for d in sorted(TELEGRAM_DCS):
        vals = samples[d]
        if vals:
            avg_val = sum(vals) / len(vals)
            if avg_val < best_avg:
                best_avg = avg_val
                best_dc = d

    for d in sorted(TELEGRAM_DCS):
        meta = TELEGRAM_DCS[d]
        vals = samples[d]
        if not vals:
            lines.append(f"• **{meta['name']}** ({meta['location']}): Failed / Timeout")
            continue

        min_v = min(vals)
        avg_v = sum(vals) / len(vals)
        max_v = max(vals)
        tag = " **[Fastest]**" if d == best_dc else ""
        lines.append(
            f"• **{meta['name']}** ({meta['location']}): "
            f"min `{min_v:.1f}ms` | avg `{avg_v:.1f}ms` | max `{max_v:.1f}ms`{tag}"
        )

    await edit_or_reply(status_msg, "\n".join(lines))

@on_cmd(["netstat", "ports", "sockets"], desc="List active network connections and open ports", usage="[listen|tg|est|query]")
async def netstat_cmd(client: Client, message: Message):
    prefix = get_client_prefix(client)
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    query = parts[1].lower().strip() if len(parts) > 1 else ""

    status_msg = await edit_or_reply(message, "`Scanning active network connections...`")

    tcp_raw = _read_proc_net("tcp")
    tcp6_raw = _read_proc_net("tcp6")
    udp_raw = _read_proc_net("udp")

    if not tcp_raw and not tcp6_raw:
        return await edit_or_reply(
            status_msg,
            "Failed to read kernel socket table (`/proc/net/tcp`). "
            "On Android 10+, root access (`su`) is required by SELinux to inspect network sockets."
        )

    socket_map = _get_socket_process_map()

    entries = []

    def parse_table(raw_content: str, is_v6: bool, proto: str):
        if not raw_content:
            return
        lines = raw_content.strip().splitlines()
        if len(lines) <= 1:
            return
        for line in lines[1:]:
            tokens = line.split()
            if len(tokens) < 10:
                continue
            loc_raw = tokens[1]
            rem_raw = tokens[2]
            state_hex = tokens[3]
            inode = tokens[9]

            try:
                loc_ip_hex, loc_port_hex = loc_raw.split(":")
                rem_ip_hex, rem_port_hex = rem_raw.split(":")
                loc_port = int(loc_port_hex, 16)
                rem_port = int(rem_port_hex, 16)

                if is_v6:
                    loc_ip = _hex_to_ipv6(loc_ip_hex)
                    rem_ip = _hex_to_ipv6(rem_ip_hex)
                else:
                    loc_ip = _hex_to_ipv4(loc_ip_hex)
                    rem_ip = _hex_to_ipv4(rem_ip_hex)

                state = TCP_STATES.get(state_hex, state_hex) if proto.startswith("TCP") else "UDP"
                pid, pname = socket_map.get(inode, ("-", "-"))

                entries.append({
                    "proto": proto,
                    "loc": f"{loc_ip}:{loc_port}",
                    "rem": f"{rem_ip}:{rem_port}",
                    "state": state,
                    "inode": inode,
                    "pid": pid,
                    "pname": pname,
                    "rem_ip": rem_ip,
                    "loc_port": loc_port,
                    "rem_port": rem_port,
                })
            except Exception:
                continue

    if tcp_raw:
        parse_table(tcp_raw, is_v6=False, proto="TCP")
    if tcp6_raw:
        parse_table(tcp6_raw, is_v6=True, proto="TCP6")
    if udp_raw:
        parse_table(udp_raw, is_v6=False, proto="UDP")

    # Filter logic
    filtered = []
    if query in ("listen", "listening", "ports", "port"):
        filtered = [e for e in entries if e["state"] == "LISTEN"]
        filter_label = "LISTEN ports"
    elif query in ("tg", "telegram", "dc"):
        # Match Telegram DC ranges: 91.108.*, 149.154.*, 95.161.*, or python3/telegram
        filtered = [
            e for e in entries
            if e["rem_ip"].startswith(("91.108.", "149.154.", "95.161."))
            or "telegram" in e["pname"].lower()
            or "python" in e["pname"].lower()
        ]
        filter_label = "Telegram MTProto sockets"
    elif query in ("est", "established"):
        filtered = [e for e in entries if e["state"] == "ESTABLISHED"]
        filter_label = "ESTABLISHED sockets"
    elif query and query != "all":
        filtered = [
            e for e in entries
            if query in e["pname"].lower()
            or query in str(e["pid"])
            or query in e["loc"]
            or query in e["rem"]
            or query in e["state"].lower()
        ]
        filter_label = f"Query: `{query}`"
    else:
        # Default: active ESTABLISHED and LISTEN sockets
        filtered = [e for e in entries if e["state"] in ("ESTABLISHED", "LISTEN")]
        filter_label = "Active (ESTABLISHED + LISTEN)"

    if not filtered:
        return await edit_or_reply(
            status_msg,
            f"No network sockets matched filter: {filter_label}.\n"
            f"Use `{prefix}netstat all` to inspect all `{len(entries)}` raw sockets."
        )

    # Cap display to top 15 entries for chat readability
    DISPLAY_LIMIT = 15
    display_entries = filtered[:DISPLAY_LIMIT]

    lines = [
        "**Active Network Connections**",
        f"• **Total Filtered:** `{len(filtered)}` of `{len(entries)}` | **Filter:** {filter_label}\n",
    ]

    for item in display_entries:
        proto = item["proto"]
        state = item["state"]
        loc = item["loc"]
        rem = item["rem"]
        pid = item["pid"]
        pname = item["pname"]
        inode = item["inode"]

        proc_info = f"`{pname}` (PID: `{pid}`)" if pid != "-" else "`Kernel/Unmapped`"

        if state == "LISTEN":
            lines.append(f"• `[{proto}]` `{loc}` `[LISTEN]`\n  Process: {proc_info}")
        else:
            lines.append(f"• `[{proto}]` `{loc}` -> `{rem}` `[{state}]`\n  Process: {proc_info} | Inode: `{inode}`")

    if len(filtered) > DISPLAY_LIMIT:
        lines.append(f"\n• Truncated `{len(filtered) - DISPLAY_LIMIT}` additional entries. Refine with `{prefix}netstat <query>`.")

    await edit_or_reply(status_msg, "\n".join(lines))

@on_cmd(["unshort", "expand"], desc="Unshorten a redirected or shortened link", usage="<url or reply>")
async def unshort_cmd(client: Client, message: Message):
    url = ""
    reply = message.reply_to_message

    parts = message.text.split(None, 1) if message.text else []
    if len(parts) > 1:
        url = parts[1].strip()
    elif reply:
        text = reply.text or reply.caption or ""
        entities = reply.entities or reply.caption_entities or []
        for e in entities:
            if e.type.name == "URL":
                url = text[e.offset:e.offset + e.length]
                break
            if e.type.name == "TEXT_LINK" and e.url:
                url = e.url
                break
        if not url:
            url = text.strip()

    if not url:
        prefix = get_client_prefix(client)
        return await edit_or_reply(
            message,
            f"Provide a URL or reply to a message containing a link:\n`{prefix}unshort https://bit.ly/example`",
        )

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    status_msg = await edit_or_reply(message, f"`Checking link destination for {url}...`")
    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()

    def progress(text: str):
        try:
            asyncio.run_coroutine_threadsafe(
                edit_or_reply(status_msg, f"`{text}`"), loop
            )
        except Exception:
            pass

    def _resolve_unshort():
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })

        resp = session.get(url, allow_redirects=True, timeout=10.0)
        curr_url = resp.url
        history_hops = [(h.url, h.status_code) for h in resp.history]

        # Unwrap safelink landing forms (WPSafeLink, AdLinkFly landing, etc.)
        for step in range(1, 9):
            m_go = re.search(r'name=[\"\']go[\"\']\s+value=[\"\']([^\"\']+)[\"\']', resp.text) or \
                   re.search(r'value=[\"\']([^\"\']+)[\"\']\s+name=[\"\']go[\"\']', resp.text)
            if not m_go:
                break
            progress(f"Bypassing safelink layer {step}...")
            action_m = re.search(r'<form[^>]+action=[\"\']([^\"\']+)[\"\']', resp.text)
            action = urljoin(curr_url, action_m.group(1)) if action_m else curr_url
            session.headers["Referer"] = curr_url
            r_post = session.post(action, data={"go": m_go.group(1)}, allow_redirects=True, timeout=10.0)
            curr_url = r_post.url
            history_hops.append((curr_url, r_post.status_code))

            m_safe = re.search(r'name=[\"\']newwpsafelink[\"\']\s+value=[\"\']([^\"\']+)[\"\']', r_post.text)
            if m_safe:
                try:
                    d = json.loads(base64.b64decode(m_safe.group(1)).decode())
                    linkr = d.get("linkr")
                    if linkr:
                        session.headers["Referer"] = curr_url
                        resp = session.get(linkr, allow_redirects=True, timeout=10.0)
                        curr_url = resp.url
                        history_hops.append((curr_url, resp.status_code))
                        continue
                except Exception:
                    pass
            resp = r_post

        # Poll countdown if waiting on AdLinkFly/ShortXLinks
        for attempt in range(1, 11):
            if "Too Early" in resp.text or "isnt ready yet" in resp.text:
                progress(f"Bypassing shortener cooldown ({attempt * 3}s)...")
                time.sleep(3)
                ref = history_hops[-2][0] if len(history_hops) > 1 else curr_url
                session.headers["Referer"] = ref
                resp = session.get(curr_url, allow_redirects=True, timeout=10.0)
            else:
                break

        # Submit AdLinkFly go-link form if present
        form_match = re.search(r'<form[^>]+id=[\"\']go-link[\"\'][^>]*>(.*?)</form>', resp.text, re.DOTALL)
        if form_match:
            progress("Resolving final destination link...")
            action_m = re.search(r'<form[^>]+id=[\"\']go-link[\"\'][^>]*action=[\"\']([^\"\']+)[\"\']', resp.text)
            go_action = urljoin(curr_url, action_m.group(1)) if action_m else urljoin(curr_url, "/links/go")
            data = {}
            for m in re.finditer(r'<input[^>]+>', form_match.group(1)):
                tag = m.group(0)
                name_m = re.search(r'name=[\"\']([^\"\']+)[\"\']', tag)
                val_m = re.search(r'value=[\"\']([^\"\']*)[\"\']', tag)
                if name_m and val_m:
                    data[name_m.group(1)] = val_m.group(1)
            r_go = session.post(
                go_action,
                data=data,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": curr_url,
                },
                timeout=10.0,
            )
            try:
                res_json = r_go.json()
                if res_json.get("url"):
                    final_target = res_json["url"]
                    history_hops.append((curr_url, 200))
                    return final_target, history_hops, 200
            except Exception:
                pass

        return curr_url, history_hops, resp.status_code

    try:
        final_url, history_hops, status_code = await asyncio.to_thread(_resolve_unshort)
        elapsed = time.perf_counter() - t0

        hops = len(history_hops)
        lines = [
            "**Link Details**",
            f"• **Initial URL:** `{url}`",
            f"• **Final Target:** `{final_url}`",
            f"• **HTTP Status:** `{status_code}`",
            f"• **Total Hops:** `{hops}`",
            f"• **Elapsed Time:** `{elapsed:.1f}s`",
        ]

        if hops > 0:
            lines.append("\n**Redirect Chain:**")
            for idx, (h_url, h_code) in enumerate(history_hops[:10], 1):
                lines.append(f"`{idx}.` `{h_url}` `({h_code})`")
            if hops > 10:
                lines.append(f"• Truncated `{hops - 10}` additional hops.")

        await edit_or_reply(status_msg, "\n".join(lines))
    except Exception as err:
        await edit_or_reply(status_msg, f"Failed to resolve URL: `{err}`")

@on_cmd(["speedtest", "speed"], desc="Test network latency, download, and upload bandwidth", usage="")
async def speedtest_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Testing network latency...`")

    def _measure_ping():
        t0 = time.perf_counter()
        resp = requests.get("https://speed.cloudflare.com/__down?bytes=0", timeout=5.0)
        ms = (time.perf_counter() - t0) * 1000.0
        colo = ""
        ray = resp.headers.get("cf-ray", "")
        if "-" in ray:
            colo = ray.split("-")[-1].strip()
        return ms, colo

    def _measure_download(bytes_to_fetch: int):
        t0 = time.perf_counter()
        resp = requests.get(f"https://speed.cloudflare.com/__down?bytes={bytes_to_fetch}", timeout=15.0)
        elapsed = time.perf_counter() - t0
        bytes_read = len(resp.content)
        mbps = (bytes_read * 8.0 / 1_000_000.0) / elapsed if elapsed > 0 else 0.0
        return mbps, bytes_read, elapsed

    def _measure_upload(bytes_to_send: int):
        payload = b"0" * bytes_to_send
        t0 = time.perf_counter()
        resp = requests.post("https://speed.cloudflare.com/__up", data=payload, timeout=15.0)
        elapsed = time.perf_counter() - t0
        mbps = (bytes_to_send * 8.0 / 1_000_000.0) / elapsed if elapsed > 0 else 0.0
        return mbps, bytes_to_send, elapsed

    try:
        ping_ms, colo = await asyncio.to_thread(_measure_ping)
        server_str = f"Cloudflare Edge ({colo})" if colo else "Cloudflare Edge"

        await edit_or_reply(status_msg, f"`Ping: {ping_ms:.1f}ms ({server_str}) · Testing download (5 MB)...`")
        dl_mbps, dl_bytes, dl_elapsed = await asyncio.to_thread(_measure_download, 5_000_000)

        await edit_or_reply(status_msg, f"`Download: {dl_mbps:.2f} Mbps · Testing upload (2 MB)...`")
        up_str = "Unavailable"
        try:
            up_mbps, up_bytes, up_elapsed = await asyncio.to_thread(_measure_upload, 2_000_000)
            up_str = f"`{up_mbps:.2f} Mbps` (`{up_bytes / (1024 * 1024):.1f} MB` in `{up_elapsed:.2f}s`)"
        except Exception:
            pass

        result_lines = [
            "**Network Speed Test**",
            f"• **Latency:** `{ping_ms:.1f} ms`",
            f"• **Download:** `{dl_mbps:.2f} Mbps` (`{dl_bytes / (1024 * 1024):.1f} MB` in `{dl_elapsed:.2f}s`)",
            f"• **Upload:** {up_str}",
            f"• **Server:** `{server_str}`",
        ]
        await edit_or_reply(status_msg, "\n".join(result_lines))
    except Exception as err:
        await edit_or_reply(status_msg, f"Speed test failed: `{err}`")

DEFAULT_SCAN_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 143, 443, 465, 587, 993, 995,
    3306, 3389, 5432, 6379, 8000, 8022, 8080, 8443, 9000
]

KNOWN_SERVICES = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    110: "POP3",
    143: "IMAP",
    443: "HTTPS",
    465: "SMTPS",
    587: "Submission",
    993: "IMAPS",
    995: "POP3S",
    1080: "SOCKS",
    1433: "MSSQL",
    1521: "Oracle",
    3306: "MySQL",
    3389: "RDP",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    8000: "HTTP-Alt",
    8022: "Termux SSH",
    8080: "HTTP-Proxy",
    8443: "HTTPS-Alt",
    9000: "SonarQube/PHP",
    27017: "MongoDB",
}

def _parse_port_args(arg_str: str) -> List[int]:
    ports = set()
    for item in arg_str.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            parts = item.split("-", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                s, e = int(parts[0]), int(parts[1])
                if s > e:
                    s, e = e, s
                for p in range(max(1, s), min(65535, e) + 1):
                    ports.add(p)
                    if len(ports) >= 100:
                        break
        elif item.isdigit():
            p = int(item)
            if 1 <= p <= 65535:
                ports.add(p)
        if len(ports) >= 100:
            break
    return sorted(ports)

async def _probe_single_port(ip: str, port: int, timeout: float = 1.2) -> dict:
    t0 = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
    except Exception:
        return {
            "port": port,
            "open": False,
            "latency": None,
            "service": KNOWN_SERVICES.get(port, "Unknown"),
            "banner": "",
        }

    latency = (time.perf_counter() - t0) * 1000.0
    banner = ""

    try:
        if port in (80, 8000, 8080):
            writer.write(b"HEAD / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n")
            await writer.drain()

        data = await asyncio.wait_for(reader.read(512), timeout=0.6)
        if data:
            raw_text = data.decode("utf-8", errors="ignore")
            for line in raw_text.splitlines():
                clean = line.strip()
                if clean.lower().startswith("server:"):
                    banner = clean.split(":", 1)[1].strip()
                    break
                elif clean.startswith("SSH-"):
                    banner = clean
                    break
            if not banner and raw_text.strip():
                banner = raw_text.splitlines()[0].strip()[:40]
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    service = KNOWN_SERVICES.get(port) or "Unknown"
    return {
        "port": port,
        "open": True,
        "latency": latency,
        "service": service,
        "banner": banner,
    }

@on_cmd(["scan", "portscan", "probe"], desc="Scan open network ports and check service banners", usage="<host> [ports]")
async def scan_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=2)
    prefix = get_client_prefix(client)

    if len(parts) < 2:
        return await edit_or_reply(
            message,
            f"**Usage:** `{prefix}scan <host> [ports]`\n"
            f"Examples:\n"
            f"• `{prefix}scan 1.1.1.1` (scans 22 common ports)\n"
            f"• `{prefix}scan 192.168.1.1 22,80,443,8022,8080`\n"
            f"• `{prefix}scan example.com 8000-8010`",
        )

    raw_host = parts[1].strip().lstrip("http://").lstrip("https://").split("/")[0].split(":")[0]
    ports_arg = parts[2].strip() if len(parts) > 2 else ""

    ports_to_scan = _parse_port_args(ports_arg) if ports_arg else DEFAULT_SCAN_PORTS

    if not ports_to_scan:
        return await edit_or_reply(message, "No valid ports specified to scan (range: 1-65535).")

    status_msg = await edit_or_reply(
        message,
        f"`Resolving {raw_host} and probing {len(ports_to_scan)} port{'s' if len(ports_to_scan) != 1 else ''}...`",
    )

    try:
        resolved_ip = await asyncio.to_thread(socket.gethostbyname, raw_host)
    except Exception as e:
        return await edit_or_reply(status_msg, f"Could not resolve host `{raw_host}`: `{e}`")

    host_display = f"`{raw_host}`" if raw_host == resolved_ip else f"`{raw_host}` (`{resolved_ip}`)"

    t_start = time.perf_counter()
    tasks = [_probe_single_port(resolved_ip, p) for p in ports_to_scan]
    results = await asyncio.gather(*tasks)
    total_duration = time.perf_counter() - t_start

    open_results = [r for r in results if r["open"]]
    closed_count = len(results) - len(open_results)

    lines = [
        f"**Port Scan: {host_display}**",
        f"• **Open:** `{len(open_results)}` of `{len(results)}` scanned ports (Time: `{total_duration:.2f}s`)\n",
    ]

    if open_results:
        for r in open_results:
            p_num = r["port"]
            srv = r["service"]
            lat = f"`{r['latency']:.1f}ms`" if r["latency"] is not None else ""
            banner_str = f" | `{r['banner']}`" if r["banner"] else ""
            lines.append(f"• `{p_num}/tcp` : **OPEN** ({lat}) | {srv}{banner_str}")
    else:
        lines.append("• No open ports discovered.")

    if closed_count > 0 and open_results:
        lines.append(f"\n• **Closed / Filtered:** `{closed_count}` ports")

    await edit_or_reply(status_msg, "\n".join(lines))


