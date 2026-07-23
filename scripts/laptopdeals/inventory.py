from __future__ import annotations

from typing import Any

from .datafile import iter_products
from .specs import clean_text, detect_cpu_brand, normalize_cpu_model, normalize_gpu_model, processor_series


def build_spec_inventory(data: Any) -> dict[str, Any]:
    inventory: dict[str, Any] = {
        "processors": {},
        "gpus": {},
        "display": {"sizes": set(), "types": set(), "resolutions": set(), "refresh_rates": set(), "brightness_levels": set(), "color_gamuts": set()},
        "memory": {"amounts": set(), "types": set()},
        "storage": {"capacities": set(), "types": set()},
        "network": {"wifi_standards": set()},
    }
    for _, product in iter_products(data):
        specs = product.get("tech_specs") or {}
        cpu = specs.get("processor") or {}
        brand = cpu.get("brand") or detect_cpu_brand(cpu.get("model"))
        model = normalize_cpu_model(cpu.get("model") or "", brand)
        if brand and model:
            inventory["processors"].setdefault(brand, {}).setdefault(processor_series(f"{brand} {model}"), set()).add(f"{brand} {model}".strip())
        gpu = specs.get("graphics") or {}
        gpu_model = normalize_gpu_model(gpu.get("model") or "")
        if gpu_model:
            low = gpu_model.lower()
            kind = "Integrated" if "integrated" in low else ("Professional" if "rtx pro" in low else ("Discrete (NVIDIA RTX)" if "rtx" in low else "Other"))
            inventory["gpus"].setdefault(kind, set()).add(gpu_model)
        display = specs.get("display") or {}
        for key, target in [("size", "sizes"), ("type", "types"), ("res", "resolutions"), ("refresh", "refresh_rates"), ("brightness", "brightness_levels"), ("color", "color_gamuts")]:
            value = clean_text(display.get(key))
            if value:
                inventory["display"][target].add(value)
        memory = specs.get("memory") or {}
        if memory.get("amount"):
            inventory["memory"]["amounts"].add(clean_text(memory["amount"]))
        if memory.get("type"):
            inventory["memory"]["types"].add(clean_text(memory["type"]))
        storage = specs.get("storage") or {}
        if storage.get("capacity"):
            inventory["storage"]["capacities"].add(clean_text(storage["capacity"]))
        if storage.get("type"):
            inventory["storage"]["types"].add(clean_text(storage["type"]))
        network = specs.get("network") or {}
        if network.get("wifi"):
            inventory["network"]["wifi_standards"].add(clean_text(network["wifi"]))

    def serializable(value: Any) -> Any:
        if isinstance(value, set):
            return sorted(item for item in value if item)
        if isinstance(value, dict):
            return {key: serializable(child) for key, child in value.items()}
        return value

    out = serializable(inventory)
    out["summary"] = {
        "total_unique_processors": sum(len(models) for series in out["processors"].values() for models in series.values()),
        "total_unique_gpus": sum(len(models) for models in out["gpus"].values()),
        "display_types": len(out["display"]["types"]),
        "display_sizes": len(out["display"]["sizes"]),
        "resolutions": len(out["display"]["resolutions"]),
        "refresh_rates": len(out["display"]["refresh_rates"]),
    }
    return out
