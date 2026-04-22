from __future__ import annotations

import json
from typing import Any


def parse_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []

    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='ignore')

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            raw = json.loads(s)
        except Exception:
            return [s]

    if not isinstance(raw, (list, tuple, set)):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None:
            continue
        s = str(item)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def normalize_string_list(raw: Any) -> list[str]:
    return parse_string_list(raw)