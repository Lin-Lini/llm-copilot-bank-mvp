from __future__ import annotations

import re


INJECTION = re.compile(
    r'(ignore\s+previous|ignore\s+all\s+previous|system\s+prompt|developer\s+message|reveal\s+prompt|jailbreak|do\s+anything\s+now|'
    r'игнорируй\s+(предыдущ|все\s+предыдущ|правил|инструкц)|раскрой\s+(промпт|инструкц)|обойди\s+ограничени)',
    re.IGNORECASE,
)
SECRETS_REQ = re.compile(
    r'(cvv|cvc|pin\b|пин\b|одноразов(ый|ые)\s+код|sms\s*код|push\s*код|код\s+подтверждения|полный\s+номер\s+карты|парол(ь|я))',
    re.IGNORECASE,
)
REMOTE_ACCESS = re.compile(
    r'(anydesk|teamviewer|rustdesk|quicksupport|удал[её]нн(ый|ого)\s+доступ|удаленн(ый|ого)\s+доступ|'
    r'установите\s+приложение|установите\s+программу|подключит(е|есь)\s+к\s+экрану)',
    re.IGNORECASE,
)
REFUND_PROMISE = re.compile(
    r'(гарантир(уем|ую|ован)|точно\s+верн(ём|ем|ется)|обязательн(о|а)\s+верн(ём|ем|ется)|'
    r'возврат\s+будет\s+точно|деньги\s+точно\s+вернутся)',
    re.IGNORECASE,
)
UNSAFE_FINALITY = re.compile(
    r'(карта\s+уже\s+заблокирована|мы\s+уже\s+заблокировали|возврат\s+уже\s+оформлен|'
    r'обращение\s+точно\s+одобрено|компенсация\s+гарантирована)',
    re.IGNORECASE,
)


def _scan(text: str, *, retrieval: bool = False) -> list[dict]:
    flags: list[dict] = []
    if INJECTION.search(text):
        flags.append({'type': 'retrieval_injection' if retrieval else 'prompt_injection', 'severity': 'high'})
    if SECRETS_REQ.search(text):
        flags.append({'type': 'secrets_instruction' if retrieval else 'secrets_request', 'severity': 'high'})
    if REMOTE_ACCESS.search(text):
        flags.append({'type': 'remote_access_instruction' if retrieval else 'remote_access', 'severity': 'high'})
    return flags


def moderate_input(text: str) -> dict:
    flags = _scan(text, retrieval=False)
    return {'ok': len(flags) == 0, 'flags': flags}


def moderate_retrieved(text: str) -> dict:
    flags = _scan(text, retrieval=True)
    return {'ok': len(flags) == 0, 'flags': flags}


def moderation_mode(result: dict) -> str:
    flags = result.get('flags') or []
    types = {flag.get('type') for flag in flags}
    if not flags:
        return 'ok'
    if 'prompt_injection' in types and (len(flags) > 1 or 'remote_access' in types):
        return 'block'
    if len(flags) >= 2:
        return 'block'
    return 'warn'


def moderate_output(text: str) -> dict:
    flags = []
    if SECRETS_REQ.search(text):
        flags.append({'type': 'secrets_in_output', 'severity': 'high'})
    if REMOTE_ACCESS.search(text):
        flags.append({'type': 'remote_access_in_output', 'severity': 'high'})
    if REFUND_PROMISE.search(text):
        flags.append({'type': 'refund_promise', 'severity': 'medium'})
    if UNSAFE_FINALITY.search(text):
        flags.append({'type': 'unsupported_finality', 'severity': 'medium'})
    return {'ok': len(flags) == 0, 'flags': flags}