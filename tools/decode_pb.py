#!/usr/bin/env python3
"""Decode a base64-encoded protobuf blob into a flat field-number tree.

For decoding the base64 protobuf the Tesla app uses (captured via MITM proxy).
Feed it the ON capture and the OFF capture, then diff the two outputs to find
the field that holds the setting.

USAGE:
    python3 tools/decode_pb.py <file-with-base64>      # or pipe base64 on stdin
    python3 tools/decode_pb.py app_on.b64  > on.txt
    python3 tools/decode_pb.py app_off.b64 > off.txt
    diff on.txt off.txt

Input may contain whitespace/newlines and may be standard or URL-safe base64.
"""

from __future__ import annotations

import base64
import binascii
import sys


def _b64decode(text: str) -> bytes:
    raw = "".join(text.split())  # strip all whitespace/newlines
    raw += "=" * (-len(raw) % 4)  # fix padding
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(raw)
        except (binascii.Error, ValueError):
            continue
    raise SystemExit("input is not valid base64")


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
        if wire == 0:
            out[path], i = _read_varint(buf, i)
        elif wire == 1:
            out[path] = int.from_bytes(buf[i : i + 8], "little")
            i += 8
        elif wire == 2:
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
        elif wire == 5:
            out[path] = int.from_bytes(buf[i : i + 4], "little")
            i += 4
        else:
            raise ValueError(f"bad wire type {wire}")
    return out


def main() -> int:
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    fields = _decode(_b64decode(text))
    for path in sorted(fields, key=lambda p: [int(x) for x in p.split(".")]):
        print(f"{path} = {fields[path]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
