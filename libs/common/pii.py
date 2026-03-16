from __future__ import annotations

import re
from typing import Any


RE_EMAIL = re.compile(r'\b[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}\b')
RE_PHONE = re.compile(r'(?:(?:\+7|7|8)\s*)?(?:\(?\d{3}\)?\s*)?\d{3}[\s-]*\d{2}[\s-]*\d{2}')
RE_PAN = re.compile(r'\b\d{12,19}\b')
RE_PASSPORT = re.compile(r'\b\d{4}\s?\d{6}\b')


def redact(text: str) -> tuple[str, dict[str, Any]]:
    summary = {'email': 0, 'phone': 0, 'pan': 0, 'passport': 0}

    def _sub(rx: re.Pattern, repl: str, key: str, s: str) -> str:
        matches = list(rx.finditer(s))
        if matches:
            summary[key] += len(matches)
        return rx.sub(repl, s)

    out = text
    out = _sub(RE_EMAIL, '<masked_email>', 'email', out)
    out = _sub(RE_PHONE, '<masked_phone>', 'phone', out)
    out = _sub(RE_PASSPORT, '<masked_doc>', 'passport', out)

    def pan_repl(m: re.Match) -> str:
        val = m.group(0)
        if len(val) >= 12:
            summary['pan'] += 1
            return f'<masked_card_last4:{val[-4:]}>'
        return '<masked_card>'

    out = RE_PAN.sub(pan_repl, out)

    return out, summary
