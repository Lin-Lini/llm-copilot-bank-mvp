from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


POLICY_VERSION = '2026-04-21.1'


def _normalize(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, 'model_dump'):
        return _normalize(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(v) for v in value]
    return str(value)


def make_prompt_hash(*parts: Any) -> str:
    norm = [_normalize(part) for part in parts]
    raw = json.dumps(norm, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()