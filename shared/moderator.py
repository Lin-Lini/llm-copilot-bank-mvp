from __future__ import annotations

import re


INJECTION = re.compile(r'(ignore\s+previous|system\s+prompt|developer\s+message|jailbreak|do\s+anything\s+now)', re.IGNORECASE)
SECRETS_REQ = re.compile(r'(cvv|cvc|pin\b|одноразов(ый|ые)\s+код|sms\s*код|парол(ь|я)|полный\s+номер\s+карты)', re.IGNORECASE)
REMOTE_ACCESS = re.compile(r'(anydesk|teamviewer|удал[её]нн(ый|ого)\s+доступ|установите\s+приложение)', re.IGNORECASE)


def moderate_input(text: str) -> dict:
    flags = []
    if INJECTION.search(text):
        flags.append({'type': 'prompt_injection', 'severity': 'high'})
    if SECRETS_REQ.search(text):
        flags.append({'type': 'secrets_request', 'severity': 'high'})
    if REMOTE_ACCESS.search(text):
        flags.append({'type': 'remote_access', 'severity': 'high'})
    return {'ok': len(flags) == 0, 'flags': flags}


def moderate_output(text: str) -> dict:
    flags = []
    if SECRETS_REQ.search(text):
        flags.append({'type': 'secrets_in_output', 'severity': 'high'})
    if REMOTE_ACCESS.search(text):
        flags.append({'type': 'remote_access_in_output', 'severity': 'high'})
    # "обещания" возврата
    if re.search(r'(гарантир(уем|ую)|точно\s+верн(ём|ем)|обязательн(о|а)\s+верн(ём|ем))', text, re.IGNORECASE):
        flags.append({'type': 'refund_promise', 'severity': 'medium'})
    return {'ok': len(flags) == 0, 'flags': flags}
