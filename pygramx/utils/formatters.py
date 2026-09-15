def format_uptime(seconds: float) -> str:
    """Format seconds into readable days, hours, minutes, seconds."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d > 0:
        parts.append(f"{d}d")
    if h > 0:
        parts.append(f"{h}h")
    if m > 0:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"

def format_bytes(bytes_count: int) -> str:
    """Format byte integers into human-readable memory strings."""
    if bytes_count <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(bytes_count)
    while val >= 1024.0 and i < len(units) - 1:
        val /= 1024.0
        i += 1
    return f"{val:.2f} {units[i]}"

def format_latency(ms: float) -> str:
    """Format millisecond latency with precision."""
    return f"{ms:.2f} ms"

def get_device_model() -> str:
    """Dynamically resolve device hardware model or host platform."""
    import os
    import subprocess
    import platform

    try:
        res = subprocess.run(
            ["getprop", "ro.product.model"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        model = res.stdout.strip()
        if model:
            res_brand = subprocess.run(
                ["getprop", "ro.product.brand"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            brand = res_brand.stdout.strip().capitalize()
            return f"{brand} {model}".strip() if brand and not model.lower().startswith(brand.lower()) else model
    except Exception:
        pass

    for p in ["/sys/class/dmi/id/product_name", "/sys/devices/virtual/dmi/id/product_name"]:
        if os.path.isfile(p):
            try:
                with open(p) as f:
                    name = f.read().strip()
                    if name and name not in ["None", "System Product Name", "Default string"]:
                        return name
            except Exception:
                pass

    return f"{platform.system()} ({platform.machine()})"

def get_portable_home() -> str:
    """
    Resolve the real user home directory portably across environments
    (Android Termux, Linux VPS, macOS, Docker, and agent subshells).
    """
    import os
    from pathlib import Path

    home = os.environ.get("HOME", "")
    if ".gemini" in home or ".agy-" in home:
        base = home.split("/.gemini")[0].split("/.agy-")[0]
        if base and os.path.isdir(base):
            return base
        if os.path.isdir("/data/data/com.termux/files/home"):
            return "/data/data/com.termux/files/home"

    if home and os.path.isdir(home):
        return home

    return str(Path.home())

def find_power_supply_dir() -> str | None:
    """
    Dynamically discover the active battery/power supply directory
    across diverse Android OEMs (Qualcomm bms, MediaTek battery, Samsung sec-battery)
    and Linux systems (BAT0, BAT1).
    """
    import os

    base = "/sys/class/power_supply"
    if not os.path.isdir(base):
        return None

    candidates = ["battery", "bms", "sec-battery", "BAT0", "BAT1", "qcom-battery", "main"]
    for c in candidates:
        p = f"{base}/{c}"
        if os.path.isdir(p):
            return p

    try:
        for entry in os.listdir(base):
            if entry.lower() not in ("usb", "ac", "charger", "wireless", "otg"):
                return f"{base}/{entry}"
    except Exception:
        pass

    return None
