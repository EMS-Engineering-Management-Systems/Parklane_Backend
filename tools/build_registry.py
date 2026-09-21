#!/usr/bin/env python3
"""Build src/data/devices.json from the Parklane tag definition workbook.

Reads `Parklane_Tags_Bool_Min_Max.xlsx` (Tag Name | Bool Check | Min | Max),
groups every tag under its equipment family, and emits the device registry the
Node backend uses for validation, dummy-data generation and collection naming.

    python tools/build_registry.py
    python tools/build_registry.py --input ../Parklane_Tags_Bool_Min_Max.xlsx
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT.parent / "Parklane_Tags_Bool_Min_Max.xlsx"
DEFAULT_OUTPUT = ROOT / "app" / "data" / "devices.json"

# Equipment families, longest prefix first so that e.g. Electrical_Fire_Pump is
# matched before Electrical_Digital_Meters cannot steal it.
DEVICE_TYPES = [
    "Electrical_Digital_Meters",
    "Emergency_Electrical_Panel",
    "Security_Access_Control",
    "Lifting_Booster_Pumps",
    "AI_Video_Analytics",
    "Electrical_Fire_Pump",
    "Fire_Fighting_Tank",
    "Diesel_Fire_Pump",
    "Fresh_Air_Fans",
    "Top_Roof_Fans",
    "Staircase_Fans",
    "Exhaust_Fans",
    "Water_Meters",
    "Water_Tanks",
    "Jockey_Pump",
    "Data_System",
    "Transformer",
    "Fire_Alarm",
    "Elevators",
    "Generator",
    "Intercom",
    "Jet_Fans",
    "SolarPV",
    "CCTV",
    "IPTV",
    "ATS",
    "RMU",
    "MDB",
]

# Human-readable grouping, mirrors the technical design document sections.
CATEGORIES = {
    "Transformer": "Electrical",
    "RMU": "Electrical",
    "MDB": "Electrical",
    "Emergency_Electrical_Panel": "Electrical",
    "Generator": "Electrical",
    "ATS": "Electrical",
    "SolarPV": "Electrical",
    "Electrical_Digital_Meters": "Electrical",
    "Elevators": "Vertical Transportation",
    "CCTV": "ELV / Security",
    "AI_Video_Analytics": "ELV / Security",
    "Fire_Alarm": "Life Safety",
    "IPTV": "ELV",
    "Security_Access_Control": "ELV / Security",
    "Data_System": "ELV / IT",
    "Intercom": "ELV",
    "Water_Tanks": "Plumbing",
    "Lifting_Booster_Pumps": "Plumbing",
    "Water_Meters": "Plumbing",
    "Fire_Fighting_Tank": "Fire Fighting",
    "Electrical_Fire_Pump": "Fire Fighting",
    "Diesel_Fire_Pump": "Fire Fighting",
    "Jockey_Pump": "Fire Fighting",
    "Exhaust_Fans": "Ventilation",
    "Fresh_Air_Fans": "Ventilation",
    "Jet_Fans": "Ventilation",
    "Top_Roof_Fans": "Ventilation",
    "Staircase_Fans": "Ventilation",
}


def normalize(raw: str) -> str:
    """Tag names mix spaces, slashes and underscores - settle on underscores."""
    return "_".join(str(raw).strip().replace("/", "_").split()).replace("__", "_")


def split_tag(tag: str) -> tuple[str, str]:
    for device in DEVICE_TYPES:
        if tag == device:
            return device, device
        if tag.startswith(device + "_"):
            return device, tag[len(device) + 1 :]
    raise ValueError(f"No equipment family matches tag: {tag}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wb = load_workbook(args.input, data_only=True, read_only=True)
    ws = wb.active

    devices: dict[str, dict] = {}
    total = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        raw_name = row[0]
        if raw_name is None or str(raw_name).strip() == "":
            continue

        original = str(raw_name).strip()
        tag_name = normalize(original)
        device_type, point = split_tag(tag_name)
        is_bool = bool(row[1])

        entry = devices.setdefault(
            device_type,
            {
                "type": device_type,
                "label": device_type.replace("_", " "),
                "category": CATEGORIES.get(device_type, "Uncategorized"),
                "instances": 1,
                "tags": [],
            },
        )

        tag = {
            "name": point,
            "fullName": tag_name,
            "sourceName": original,
            "kind": "boolean" if is_bool else "numeric",
        }
        if not is_bool:
            minimum = float(row[2])
            maximum = float(row[3])
            integer = float(minimum).is_integer() and float(maximum).is_integer()
            tag["min"] = int(minimum) if integer else minimum
            tag["max"] = int(maximum) if integer else maximum
            tag["integer"] = integer

        entry["tags"].append(tag)
        total += 1

    registry = {
        "database": "Atal",
        "source": args.input.name,
        "deviceCount": len(devices),
        "tagCount": total,
        "devices": [devices[name] for name in devices],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    print(f"Devices: {len(devices)}  Tags: {total}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
