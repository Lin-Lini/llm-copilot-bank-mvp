from __future__ import annotations

import re
from typing import Any


RE_EMAIL = re.compile(r'\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b')
RE_PHONE = re.compile(r'(?<!\d)(?:(?:\+7|7|8)[\s\-()]*)?(?:\d[\s\-()]*){10}(?![\s\-()]*\d)')
RE_PASSPORT = re.compile(r'\b\d{4}\s?\d{6}\b')
RE_DOB = re.compile(r'\b(?:0[1-9]|[12][0-9]|3[01])[./-](?:0[1-9]|1[0-2])[./-](?:19|20)\d{2}\b')
RE_FIO = re.compile(r'\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\b')
RE_ADDRESS = re.compile(r'(?im)^\s*(?:адрес|address)\s*:\s*[^\n\r]{6,}$')
RE_CONTRACT = re.compile(r'(?i)\b(?:договор|договора|contract|agreement)\s*(?:№|#)?\s*([A-Za-zА-Яа-я0-9-]{5,})')
RE_OTP_CONTEXT = re.compile(
    r'(?i)\b(?:sms|push|otp|код(?:\s+подтверждения)?|одноразов(?:ый|ые)\s+код(?:ы)?|пароль)\b([^\n\r]{0,20}?)(\d{4,8})'
)
RE_PAN_LIKE = re.compile(r'\b(?:\d[ -]?){12,19}\d\b')
RE_MASKED_PAN = re.compile(r'\b(?:\d{4}|[*xX]{4})(?:[ -]?(?:\d{4}|[*xX]{4})){2,3}\b')


def _sub(rx: re.Pattern, repl: str, key: str, s: str, summary: dict[str, int]) -> str:
    matches = list(rx.finditer(s))
    if matches:
        summary[key] += len(matches)
    return rx.sub(repl, s)


def _mask_pan_like(s: str, summary: dict[str, int]) -> str:
    def repl(m: re.Match) -> str:
        raw = m.group(0)
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if 12 <= len(digits) <= 19:
            summary['pan'] += 1
            return f'<masked_card_last4:{digits[-4:]}>'
        return raw

    out = RE_PAN_LIKE.sub(repl, s)

    def repl_masked(m: re.Match) -> str:
        raw = m.group(0)
        if '*' in raw or 'x' in raw.lower():
            summary['masked_pan'] += 1
            tail = ''.join(ch for ch in raw if ch.isdigit())[-4:]
            return f'<masked_card_last4:{tail}>' if tail else '<masked_card>'
        return raw

    return RE_MASKED_PAN.sub(repl_masked, out)


def _mask_phone(s: str, summary: dict[str, int]) -> str:
    def repl(m: re.Match) -> str:
        raw = m.group(0)
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) not in {10, 11}:
            return raw
        if len(digits) >= 12:
            return raw
        summary['phone'] += 1
        return '<masked_phone>'

    return RE_PHONE.sub(repl, s)


def redact(text: str) -> tuple[str, dict[str, Any]]:
    summary = {
        'email': 0,
        'phone': 0,
        'pan': 0,
        'masked_pan': 0,
        'passport': 0,
        'dob': 0,
        'otp_code': 0,
        'contract': 0,
        'address': 0,
        'fio': 0,
    }

    out = text or ''
    out = _sub(RE_EMAIL, '<masked_email>', 'email', out, summary)
    out = _mask_pan_like(out, summary)
    out = _sub(RE_PASSPORT, '<masked_doc>', 'passport', out, summary)
    out = _sub(RE_DOB, '<masked_dob>', 'dob', out, summary)
    out = _sub(RE_ADDRESS, 'Адрес: <masked_address>', 'address', out, summary)
    out = _sub(RE_FIO, '<masked_name>', 'fio', out, summary)

    def contract_repl(m: re.Match) -> str:
        summary['contract'] += 1
        return 'договор <masked_contract>'

    out = RE_CONTRACT.sub(contract_repl, out)

    def otp_repl(m: re.Match) -> str:
        summary['otp_code'] += 1
        return m.group(0).replace(m.group(2), '<masked_otp>')

    out = RE_OTP_CONTEXT.sub(otp_repl, out)
    out = _mask_phone(out, summary)

    return out, summary