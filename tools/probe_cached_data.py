#!/usr/bin/env python3
"""Probe the protobuf `cached_data` blob from Tesla Fleet /api/1/products.

The Tesla Fleet integration discards `cached_data` (a protobuf-encoded snapshot
of vehicle state). Low power mode / keep accessory power are not in the JSON
vehicle_data, so this tool decodes `cached_data` to see whether their state is
in there.

USAGE (run it yourself so your token never leaves your shell):

    TESLA_TOKEN=<fleet_api_access_token> \
    TESLA_VIN=<your_vin> \
    TESLA_REGION=na \
    python3 tools/probe_cached_data.py > before.txt

Then, in the Tesla app, toggle ONE setting (e.g. Controls > Charging > Low
Power Mode), wait ~30 s, and run it again into `after.txt`. Diff them:

    diff before.txt after.txt

The line(s) that change point at the protobuf field that holds the setting —
paste that diff and I'll wire the switch to read it. Repeat for Keep Accessory
Power.

No external dependencies; pure standard library. Region is `na` or `eu`.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

REGION_HOST = {
    "na": "fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "fleet-api.prd.eu.vn.cloud.tesla.com",
}


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _looks_like_message(buf: bytes) -> bool:
    """Heuristic: does `buf` parse cleanly as a protobuf message to its end?"""
    try:
        _decode(buf)
        return True
    except Exception:
        return False


def _decode(buf: bytes, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    i, n = 0, len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        field, wire = tag >> 3, tag & 7
        path = f"{prefix}{field}"
        if wire == 0:  # varint (bool / int / enum)
            val, i = _read_varint(buf, i)
            out[path] = val
        elif wire == 1:  # 64-bit
            out[path] = int.from_bytes(buf[i : i + 8], "little")
            i += 8
        elif wire == 2:  # length-delimited: nested message, string, or bytes
            ln, i = _read_varint(buf, i)
            sub = buf[i : i + ln]
            i += ln
            if sub and _looks_like_message(sub):
                for k, v in _decode(sub).items():
                    out[f"{path}.{k}"] = v
            else:
                try:
                    text = sub.decode("utf-8")
                    out[path] = text if text.isprintable() else sub.hex()
                except UnicodeDecodeError:
                    out[path] = sub.hex()
        elif wire == 5:  # 32-bit
            out[path] = int.from_bytes(buf[i : i + 4], "little")
            i += 4
        else:
            raise ValueError(f"bad wire type {wire}")
    return out


def main() -> int:
    token = os.environ.get("TESLA_TOKEN")
    vin = os.environ.get("TESLA_VIN")
    region = os.environ.get("TESLA_REGION", "").lower()
    if not token:
        print("Set TESLA_TOKEN (and TESLA_VIN; omit VIN to list vehicles).", file=sys.stderr)
        return 2

    # Region defaults to trying na then eu so you don't have to know it.
    candidates = [region] if region in REGION_HOST else ["na", "eu"]
    products = None
    for cand in candidates:
        req = urllib.request.Request(
            f"https://{REGION_HOST[cand]}/api/1/products",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                products = json.loads(resp.read().decode())["response"]
            print(f"(region: {cand})", file=sys.stderr)
            break
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                print(f"HTTP {err.code}: token invalid/expired.", file=sys.stderr)
                return 1
            continue  # wrong region → try the next one
    if products is None:
        print("Could not fetch products from either region.", file=sys.stderr)
        return 1

    if not vin:
        # No VIN given: list what's available so you can pick one.
        print("Products (set TESLA_VIN to one of these):", file=sys.stderr)
        for p in products:
            if "vin" in p:
                has = "cached_data" if p.get("cached_data") else "NO cached_data"
                print(f"  {p['vin']}  ({has})", file=sys.stderr)
        return 0

    product = next((p for p in products if p.get("vin") == vin), None)
    if product is None:
        print(f"VIN {vin} not found in products.", file=sys.stderr)
        return 1
    raw = product.get("cached_data")
    if not raw:
        print("No cached_data on this product.", file=sys.stderr)
        return 1

    blob = base64.b64decode(raw)
    fields = _decode(blob)
    # Sort by path for a stable, diff-friendly dump.
    for path in sorted(fields, key=lambda p: [int(x) for x in p.split(".")]):
        print(f"{path} = {fields[path]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
