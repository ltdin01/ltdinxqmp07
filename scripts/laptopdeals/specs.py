from __future__ import annotations

import re
from typing import Any


def clean_text(value: Any) -> str:
    if not value:
        return ""
    text = str(value).replace("â\u0084¢", "").replace("â\u00ae", "")
    text = re.sub(r"[™®]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_cpu_brand(cpu_raw: Any) -> str:
    low = str(cpu_raw or "").lower()
    if "snapdragon" in low or "qualcomm" in low:
        return "Snapdragon"
    if "amd" in low or "ryzen" in low:
        return "AMD"
    if "intel" in low or "core" in low or re.search(r"\bi[3579]-", low):
        return "Intel"
    return "Unknown"


def normalize_cpu_model(cpu_raw: Any, brand: str) -> str:
    text = clean_text(cpu_raw).replace("Processor", "")
    text = text.split("(")[0].strip()
    text = re.sub(r"^\d+(?:st|nd|rd|th)\s+(?:Generation|Gen)\s+", "", text, flags=re.I)
    if brand == "Intel":
        text = re.sub(r"^Intel\s+", "", text, flags=re.I)
    elif brand == "AMD":
        text = re.sub(r"^AMD\s+", "", text, flags=re.I)
    elif brand == "Snapdragon":
        text = re.sub(r"^(?:Qualcomm\s+)?Snapdragon\s+", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def normalize_gpu_model(gpu_raw: Any) -> str:
    text = clean_text(gpu_raw).replace("Graphics", "")
    text = re.sub(r"\bNVIDIA\b\s*", "", text, flags=re.I)
    text = re.sub(r"\bLaptop\s+GPU\b", "", text, flags=re.I)
    text = re.sub(r"\bGeforce\b", "GeForce", text, flags=re.I)
    text = re.sub(r"\bRTX\s+PRO\b", "RTX PRO", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def parse_spec_codes(raw_map: dict[str, Any]) -> dict[str, Any]:
    specs = {
        "display": {"size": None, "res": None, "refresh": None, "type": None, "brightness": None, "color": None},
        "memory": {"amount": None, "type": None, "speed": None},
        "storage": {"capacity": None, "type": None},
        "processor": {"brand": None, "model": None},
        "graphics": {"model": None, "vram": None, "dedicated": False},
        "network": {"wifi": None, "bluetooth": None},
        "power": {"adapter": None},
    }

    cpu_raw = raw_map.get("LOIS_SCA_CPU", "")
    if cpu_raw:
        brand = detect_cpu_brand(cpu_raw)
        specs["processor"]["brand"] = brand
        specs["processor"]["model"] = normalize_cpu_model(cpu_raw, brand)

    gpu_raw = raw_map.get("LOIS_SCA_VIDEO", "")
    if gpu_raw:
        model = normalize_gpu_model(gpu_raw)
        low = model.lower()
        specs["graphics"]["dedicated"] = any(x in low for x in ["rtx", "gtx", "discrete", "radeon rx"])
        vram = re.search(r"(\d+)\s*GB", model, flags=re.I)
        specs["graphics"]["vram"] = f"{vram.group(1)} GB" if vram else "Shared"
        specs["graphics"]["model"] = model

    mem_raw = str(raw_map.get("LOIS_SCA_MEM", ""))
    if mem_raw:
        amt = re.search(r"(\d+)\s*GB", mem_raw, flags=re.I)
        specs["memory"]["amount"] = f"{amt.group(1)} GB" if amt else "Unknown"
        specs["memory"]["type"] = "LPDDR5x" if "LPDDR5x" in mem_raw else ("DDR5" if "DDR5" in mem_raw else "DDR4")
        spd = re.search(r"(\d{4})", mem_raw)
        specs["memory"]["speed"] = f"{spd.group(1)} MHz" if spd else "Unknown"

    sto_raw = str(raw_map.get("LOIS_SCA_HDD", ""))
    if sto_raw:
        cap = re.search(r"(\d+)\s*(GB|TB)", sto_raw, flags=re.I)
        specs["storage"]["capacity"] = f"{cap.group(1)} {cap.group(2).upper()}" if cap else "Unknown"
        specs["storage"]["type"] = "SSD Gen4" if "Gen4" in sto_raw else "SSD"

    dpy_raw = str(raw_map.get("LOIS_SCA_DPY", ""))
    if dpy_raw:
        size = re.search(r"\((\d+(?:\.\d+)?)\)", dpy_raw)
        specs["display"]["size"] = f"{size.group(1)}\"" if size else "Unknown"
        res = re.search(r"(\d{4}\s*x\s*\d{4})", dpy_raw, flags=re.I)
        specs["display"]["res"] = res.group(1).replace(" ", "") if res else "Unknown"
        hz = re.search(r"(\d+)\s*Hz", dpy_raw, flags=re.I)
        specs["display"]["refresh"] = f"{hz.group(1)}Hz" if hz else "60Hz"
        nits = re.search(r"(\d+)\s*nits", dpy_raw, flags=re.I)
        specs["display"]["brightness"] = f"{nits.group(1)} nits" if nits else "Unknown"
        panel = re.search(r"(IPS|OLED|TN|VA)", dpy_raw, flags=re.I)
        specs["display"]["type"] = panel.group(1).upper() if panel else "Unknown"
        if "sRGB" in dpy_raw:
            specs["display"]["color"] = "100% sRGB"
        elif "DCI-P3" in dpy_raw:
            specs["display"]["color"] = "100% DCI-P3"

    wifi_raw = str(raw_map.get("LOIS_SCA_WIFI", ""))
    if wifi_raw:
        specs["network"]["wifi"] = "Wi-Fi 7" if "Wi-Fi 7" in wifi_raw else ("Wi-Fi 6" if "Wi-Fi 6" in wifi_raw else "Standard")
        bt = re.search(r"Bluetooth.*?(\d\.\d)", wifi_raw, flags=re.I)
        specs["network"]["bluetooth"] = bt.group(1) if bt else "Included"
    specs["power"]["adapter"] = raw_map.get("LOIS_SCA_POWERSUPP", "Unknown")
    return specs


def first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def first_float(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def detect_cpu_brand_psref(raw: str) -> str:
    low = (raw or "").lower()
    if "intel" in low or "core" in low:
        return "Intel"
    if "amd" in low or "ryzen" in low:
        return "AMD"
    if "snapdragon" in low or "qualcomm" in low:
        return "Qualcomm"
    return "Unknown"


def parse_cpu_psref(raw: str) -> dict[str, Any]:
    text = clean_text(raw)
    brand = detect_cpu_brand_psref(text)
    paren_idx = text.find("(")
    comma_idx = text.find(",")
    if paren_idx >= 0 and (comma_idx < 0 or paren_idx < comma_idx):
        base = text[:paren_idx].strip().rstrip(",").strip()
    elif comma_idx >= 0:
        base = text[:comma_idx].strip()
    else:
        base = text.strip()
    model = base
    for token in ("Intel ", "AMD ", "Qualcomm "):
        model = re.sub(rf"^{token}", "", model, flags=re.IGNORECASE).strip()
    full_model = f"{brand} {model}".strip() if brand != "Unknown" else model
    return {
        "raw": text,
        "brand": brand,
        "model": model,
        "full_model": full_model,
        "cores": first_int(text, r"(\d+)C\b"),
        "threads": first_int(text, r"/\s*(\d+)T\b"),
        "base_clock_ghz": first_float(text, r"\b(\d+(?:\.\d+)?)\s*/\s*\d+(?:\.\d+)?GHz"),
        "boost_clock_ghz": max([float(v) for v in re.findall(r"(\d+(?:\.\d+)?)GHz", text)] or [0]) or None,
    }


def detect_gpu_brand_psref(raw: str) -> str:
    low = (raw or "").lower()
    if "nvidia" in low or "geforce" in low or "rtx" in low:
        return "NVIDIA"
    if "amd" in low or "radeon" in low:
        return "AMD"
    if "intel" in low or "arc" in low or "iris" in low:
        return "Intel"
    if "qualcomm" in low or "adreno" in low:
        return "Qualcomm"
    return "Unknown"


def parse_gpu_psref(raw: str) -> dict[str, Any]:
    text = clean_text(raw)
    primary = text.split(",")[0].strip()
    brand = detect_gpu_brand_psref(text)
    model = re.sub(r"^(NVIDIA|AMD|Intel|Qualcomm)\s+", "", primary, flags=re.IGNORECASE).strip()
    dedicated = not bool(re.search(r"\bintegrated\b|shared|onboard", text, flags=re.IGNORECASE))
    return {
        "raw": text,
        "brand": brand,
        "model": model,
        "full_model": f"{brand} {model}".strip() if brand != "Unknown" else model,
        "dedicated": dedicated,
        "vram_gb": first_int(text, r"(\d+)GB\s+(?:GDDR|DDR)"),
        "boost_clock_mhz": first_int(text, r"Boost Clock\s+(\d+)MHz"),
        "tgp_w": first_int(text, r"\bTGP\s+(\d+)W"),
        "ai_tops": first_int(text, r"(\d+)\s+AI\s+TOPS"),
    }


def parse_memory_psref(raw: str) -> dict[str, Any]:
    text = clean_text(raw)
    hits = list(re.finditer(r"(?:(\d+)x\s*)?(\d+)\s*GB", text, flags=re.IGNORECASE))
    amount_gb = None
    if hits:
        first = hits[0]
        amount_gb = int(first.group(1) or "1") * int(first.group(2))
    mem_type = next((token for token in ["LPDDR5x", "LPDDR5", "DDR5", "DDR4"] if token.lower() in text.lower()), "")
    speed_mhz = first_int(text, r"(?:DDR\d|LPDDR\d\w*)-(\d{4,5})")
    return {
        "raw": text,
        "amount": f"{amount_gb} GB" if amount_gb else "",
        "amount_gb": amount_gb,
        "type": mem_type,
        "speed": f"{speed_mhz} MHz" if speed_mhz else "",
        "speed_mhz": speed_mhz,
        "slots_populated": int(hits[0].group(1) or "1") if hits else None,
        "soldered": "soldered" in text.lower(),
    }


def parse_storage_psref(raw: str) -> dict[str, Any]:
    text = clean_text(raw)
    parts: list[int] = []
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(TB|GB)", text, flags=re.IGNORECASE):
        value = float(amount) * (1024 if unit.upper() == "TB" else 1)
        parts.append(int(value))
    total_gb = sum(parts) if parts else None
    storage_type = "NVMe SSD" if re.search(r"NVMe|PCIe", text, re.I) else ("SSD" if "ssd" in text.lower() else "")
    return {
        "raw": text,
        "capacity": f"{total_gb // 1024} TB" if total_gb and total_gb % 1024 == 0 else (f"{total_gb} GB" if total_gb else ""),
        "capacity_gb": total_gb,
        "type": storage_type,
    }


DISPLAY_PANEL_PRIORITY_PSREF = {
    "OLED": 100,
    "POLED": 98,
    "Mini LED": 90,
    "IPS": 70,
    "IPS-level": 65,
    "WVA": 50,
    "VA": 45,
    "TN": 20,
}


def resolution_name(width: int | None, height: int | None) -> str:
    if not width or not height:
        return ""
    aliases = {
        (1920, 1080): "FHD",
        (1920, 1200): "WUXGA",
        (2240, 1400): "2.2K",
        (2560, 1440): "QHD",
        (2560, 1600): "WQXGA",
        (2880, 1800): "2.8K",
        (3200, 2000): "3.2K",
        (3840, 2160): "UHD",
        (3840, 2400): "WQUXGA",
    }
    return aliases.get((width, height), f"{width}x{height}")


def parse_display_psref(raw: str) -> dict[str, Any]:
    text = clean_text(raw)
    size = first_float(text, r'(\d+(?:\.\d+)?)"')
    if not size:
        size = first_float(text, r'\((\d+(?:\.\d+)?)\)')
    match = re.search(r"(\d{3,4})\s*x\s*(\d{3,4})", text, flags=re.IGNORECASE)
    width = int(match.group(1)) if match else None
    height = int(match.group(2)) if match else None
    panel = ""
    if "oled" in text.lower():
        panel = "OLED"
    for token in DISPLAY_PANEL_PRIORITY_PSREF:
        if panel:
            break
        if re.search(rf"\b{re.escape(token)}\b", text, flags=re.IGNORECASE):
            panel = token
            break
    brightness_nits = first_int(text, r"(\d+)\s*nits?")
    refresh_hz = first_int(text, r"(\d+)\s*Hz")
    return {
        "raw": text,
        "size": f'{size:g}"' if size else "",
        "size_inches": size,
        "resolution": f"{width}x{height}" if width and height else "",
        "resolution_name": resolution_name(width, height),
        "type": panel,
        "brightness": f"{brightness_nits} nits" if brightness_nits else "",
        "brightness_nits": brightness_nits,
        "refresh": f"{refresh_hz}Hz" if refresh_hz else "",
        "refresh_hz": refresh_hz,
        "color": clean_text(", ".join(re.findall(r"\d+%\s+(?:sRGB|DCI-P3|NTSC|Adobe RGB)", text, flags=re.IGNORECASE))),
        "touch": "Yes" if re.search(r"\btouch\b", text, re.I) and not re.search(r"non[- ]?touch", text, re.I) else "No",
        "surface": "Anti-glare" if "anti-glare" in text.lower() else ("Glossy" if "glossy" in text.lower() else ""),
    }


def parse_network_psref(raw: str) -> dict[str, Any]:
    text = clean_text(raw)
    wifi_match = re.search(r"Wi-?Fi\s*(\d(?:E|\.?\d)?)", text, re.I)
    bt_match = re.search(r"BT\s?(\d(?:\.\d)?)|Bluetooth\s?(\d(?:\.\d)?)", text, re.I)
    bluetooth = next((item for item in (bt_match.groups() if bt_match else []) if item), "")
    return {"raw": text, "wifi": f"Wi-Fi {wifi_match.group(1)}" if wifi_match else "", "bluetooth": bluetooth}


def processor_series(cpu_full: str) -> str:
    low = clean_text(cpu_full).lower()
    if low.startswith("amd"):
        if "ryzen ai" in low:
            match = re.search(r"\b(\d{3})\b", low)
            return f"Ryzen AI {match.group(1)[0]}00 Series" if match else "Ryzen AI"
        match = re.search(r"\b([2789]\d{3})[a-z]*\b", low)
        return f"Ryzen {match.group(1)[0]}000 Series" if match else "Ryzen"
    if low.startswith("intel"):
        if "core ultra" in low:
            match = re.search(r"\b([12]\d{2})[a-z]*\b", low)
            return f"Core Ultra {match.group(1)[0]}00 Series" if match else "Core Ultra"
        match = re.search(r"\bi[3579]-?(\d{2})\d{2,3}[a-z]*\b", low)
        return f"Core {int(match.group(1))}th Gen" if match else "Core"
    if low.startswith("snapdragon"):
        if "x elite" in low:
            return "Snapdragon X Elite"
        if "x plus" in low:
            return "Snapdragon X Plus"
        return "Snapdragon X"
    return "Other"
