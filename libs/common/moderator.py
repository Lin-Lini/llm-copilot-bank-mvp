from __future__ import annotations

import re
from typing import Any


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


def _text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return str(value)


def _flag(flag_type: str, severity: str, *, source: str) -> dict[str, str]:
    return {'type': flag_type, 'severity': severity, 'source': source}


def _scan(text: str, *, retrieval: bool = False) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    if INJECTION.search(text):
        flags.append(
            _flag(
                'retrieval_injection' if retrieval else 'prompt_injection',
                'high',
                source='retrieved' if retrieval else 'user_input',
            )
        )
    if SECRETS_REQ.search(text):
        flags.append(
            _flag(
                'secrets_instruction' if retrieval else 'secrets_request',
                'high',
                source='retrieved' if retrieval else 'user_input',
            )
        )
    if REMOTE_ACCESS.search(text):
        flags.append(
            _flag(
                'remote_access_instruction' if retrieval else 'remote_access',
                'high',
                source='retrieved' if retrieval else 'user_input',
            )
        )
    return flags


def moderation_mode(result: dict) -> str:
    flags = result.get('flags') or []
    types = {flag.get('type') for flag in flags}
    if not flags:
        return 'ok'
    if 'prompt_injection' in types and (len(flags) > 1 or 'remote_access' in types):
        return 'block'
    if 'retrieval_injection' in types and len(flags) >= 1:
        return 'block'
    if len(flags) >= 2:
        return 'block'
    return 'warn'


def moderate_input(text: str) -> dict[str, Any]:
    text = _text(text)
    flags = _scan(text, retrieval=False)
    mode = moderation_mode({'flags': flags})
    out = {'ok': len(flags) == 0, 'flags': flags, 'mode': mode}
    if mode == 'block':
        out['safe_text'] = (
            'Для безопасности нельзя запрашивать CVV/CVC, ПИН, одноразовые коды '
            'или рекомендовать удаленный доступ. Продолжайте только по безопасному сценарию.'
        )
    elif mode == 'warn':
        out['safe_text'] = (
            'Во входе есть рискованные инструкции. Разрешены только безопасные '
            'уточнения без запроса секретов.'
        )
    return out


def moderate_retrieved(text: str) -> dict[str, Any]:
    text = _text(text)
    flags = _scan(text, retrieval=True)
    mode = 'block' if flags else 'ok'
    out = {'ok': len(flags) == 0, 'flags': flags, 'mode': mode}
    if flags:
        out['safe_text'] = (
            'Подозрительный retrieved-фрагмент исключен из контекста, потому что '
            'содержит признаки injection или инструкции запросить секреты.'
        )
    return out


def moderate_output(text: str) -> dict[str, Any]:
    text = _text(text)
    flags: list[dict[str, str]] = []
    if SECRETS_REQ.search(text):
        flags.append(_flag('secrets_in_output', 'high', source='model_output'))
    if REMOTE_ACCESS.search(text):
        flags.append(_flag('remote_access_in_output', 'high', source='model_output'))
    if REFUND_PROMISE.search(text):
        flags.append(_flag('refund_promise', 'medium', source='model_output'))
    if UNSAFE_FINALITY.search(text):
        flags.append(_flag('unsupported_finality', 'medium', source='model_output'))
    mode = 'block' if flags else 'ok'
    out = {'ok': len(flags) == 0, 'flags': flags, 'mode': mode}
    if flags:
        out['safe_text'] = (
            'Для безопасности используйте только подтвержденный результат инструмента. '
            'Не запрашивайте секреты и не обещайте гарантированный результат.'
        )
    return out


# New split API with backward-compatible wrappers.

def moderate_user_input(text: Any) -> dict[str, Any]:
    result = moderate_input(_text(text))
    return {
        'kind': 'user_input',
        'ok': result['ok'],
        'mode': result['mode'],
        'flags': result['flags'],
        'reasons': [flag['type'] for flag in result['flags']],
        'safe_text': result.get('safe_text'),
        'text_len': len(_text(text)),
    }


def _extract_chunk_text(chunk: Any) -> str:
    if isinstance(chunk, dict):
        for key in ('quote', 'text', 'content', 'snippet'):
            value = chunk.get(key)
            if value is not None:
                return _text(value)
    return _text(chunk)


def moderate_retrieved_chunks(chunks: list[Any] | None) -> dict[str, Any]:
    blocked_chunk_indices: list[int] = []
    suspicious_items: list[dict[str, Any]] = []
    allowed_chunks: list[Any] = []

    for idx, chunk in enumerate(chunks or []):
        mod = moderate_retrieved(_extract_chunk_text(chunk))
        if mod['ok']:
            allowed_chunks.append(chunk)
            continue
        blocked_chunk_indices.append(idx)
        suspicious_items.append({'index': idx, 'flags': mod['flags'], 'mode': mod['mode']})

    mode = 'block' if blocked_chunk_indices else 'ok'
    flags = [
        _flag('suspicious_retrieval_source', 'high', source='retrieved')
    ] if blocked_chunk_indices else []

    return {
        'kind': 'retrieved_chunks',
        'ok': not blocked_chunk_indices,
        'mode': mode,
        'flags': flags,
        'reasons': [flag['type'] for flag in flags],
        'blocked_chunk_indices': blocked_chunk_indices,
        'suspicious_items': suspicious_items,
        'allowed_chunks': allowed_chunks,
    }


def moderate_model_output(text: Any) -> dict[str, Any]:
    result = moderate_output(_text(text))
    return {
        'kind': 'model_output',
        'ok': result['ok'],
        'mode': result['mode'],
        'flags': result['flags'],
        'reasons': [flag['type'] for flag in result['flags']],
        'safe_text': result.get('safe_text'),
        'text_len': len(_text(text)),
    }


def summarize_security_moderation(
    *,
    user_input: dict[str, Any] | None = None,
    retrieved: dict[str, Any] | None = None,
    model_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parts = [p for p in [user_input, retrieved, model_output] if p]
    overall_mode = 'ok'
    flags: list[str] = []
    reasons: list[str] = []

    for part in parts:
        mode = part.get('mode', 'ok')
        if mode == 'block':
            overall_mode = 'block'
        elif mode == 'warn' and overall_mode == 'ok':
            overall_mode = 'warn'

        for flag in part.get('flags', []):
            flag_type = flag['type'] if isinstance(flag, dict) and 'type' in flag else str(flag)
            if flag_type not in flags:
                flags.append(flag_type)
        for reason in part.get('reasons', []):
            if reason not in reasons:
                reasons.append(reason)

    return {
        'mode': overall_mode,
        'flags': flags,
        'reasons': reasons,
        'user_input': user_input,
        'retrieved': retrieved,
        'model_output': model_output,
    }
