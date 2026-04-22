from __future__ import annotations

import re
from typing import Any

from contracts.schemas import (
    AnalyzeV1,
    DangerFlag,
    DraftV1,
    ExplainV1,
    Intent,
    RiskChecklistItem,
)


_BAD_TAILS = (
    'что вы не совершали',
    'что карта сейчас',
    'о которой идет речь',
    'также подтвердите, пожалуйста',
    'чтобы сверить данные',
    'что вы хотите',
    'сумму и пример',
    'дату и пример',
    'время и пример',
)

_INCOMPLETE_ENDINGS_RE = re.compile(
    r'(сумму и пример|дату и пример|время и пример|что вы не совершали|что карта сейчас)\s*$',
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    text = (text or '').replace('\r', '\n')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _last_line(text: str) -> str:
    lines = [x.strip() for x in text.split('\n') if x.strip()]
    return lines[-1] if lines else ''


def _looks_truncated(text: str) -> bool:
    t = _clean_text(text)
    if not t:
        return True
    if len(t) < 24:
        return True
    if t.endswith(('...', '…', ':', ';', ',', '-', '—')):
        return True

    lower = t.lower().strip()
    if any(lower.endswith(x) for x in _BAD_TAILS):
        return True
    if _INCOMPLETE_ENDINGS_RE.search(lower):
        return True

    if re.search(r'\n\s*\d+\.\s*$', t):
        return True

    last = _last_line(t)
    last_lower = last.lower()

    if re.match(r'^\d+\.\s+', last):
        if last[-1] not in '.!?':
            return True
        if _INCOMPLETE_ENDINGS_RE.search(last_lower):
            return True

    if t[-1] not in '.!?':
        last_words = last_lower.split()
        if len(last_words) <= 3:
            return True
        if re.search(r'(подтвердите|уточните|сообщите|укажите|выберите)\s*$', last_lower):
            return True

    return False


def _ensure_terminal_punct(text: str) -> str:
    t = _clean_text(text)
    if not t:
        return t
    if t[-1] not in '.!?':
        t += '.'
    return t


def _fallback_draft_ghost(an: AnalyzeV1) -> str:
    if an.intent == Intent.SuspiciousTransaction:
        return (
            'Пожалуйста, уточните сумму и примерное время операции, а также подтвердите, '
            'что карта у вас на руках и что вы не совершали эту операцию. '
            'После этого я подскажу следующий безопасный шаг.'
        )
    if an.intent in {Intent.BlockCard, Intent.LostStolen}:
        return (
            'Подтвердите, пожалуйста, что карту нужно заблокировать прямо сейчас. '
            'После подтверждения я подскажу следующий безопасный шаг.'
        )
    if an.intent == Intent.StatusWhatNext:
        return (
            'Подскажите, пожалуйста, номер обращения или уточните, по какой операции нужен статус. '
            'После этого я смогу подсказать следующий шаг.'
        )
    return 'Пожалуйста, уточните недостающие детали обращения, чтобы я мог подсказать следующий шаг.'


def _fallback_explain_ghost(tool_name: str) -> str:
    if tool_name == 'create_case':
        return (
            'Обращение зарегистрировано. Сообщите клиенту номер обращения и поясните, '
            'что статус можно уточнить позже, а решение зависит от проверки.'
        )
    if tool_name == 'block_card':
        return (
            'Карта заблокирована. Сообщите клиенту, что действие выполнено, '
            'и поясните следующий безопасный шаг.'
        )
    if tool_name == 'get_transactions':
        return (
            'Список операций получен. Теперь можно сверить спорную транзакцию '
            'и при подтверждении оформить обращение.'
        )
    return 'Действие выполнено. Передайте клиенту подтвержденный результат и следующий безопасный шаг.'


def _coerce_danger_flags(raw: Any) -> list[DangerFlag]:
    out: list[DangerFlag] = []
    for item in raw or []:
        try:
            out.append(item if isinstance(item, DangerFlag) else DangerFlag.model_validate(item))
        except Exception:
            continue
    return out


def _coerce_risk_checklist(raw: Any) -> list[RiskChecklistItem]:
    out: list[RiskChecklistItem] = []
    for item in raw or []:
        try:
            out.append(item if isinstance(item, RiskChecklistItem) else RiskChecklistItem.model_validate(item))
        except Exception:
            continue
    return out


def repair_draft(draft: DraftV1, an: AnalyzeV1) -> DraftV1:
    ghost = _clean_text(draft.ghost_text)
    if _looks_truncated(ghost):
        ghost = _fallback_draft_ghost(an)
    else:
        ghost = _ensure_terminal_punct(ghost)

    sidebar = draft.sidebar
    if not sidebar.danger_flags and an.danger_flags:
        sidebar = sidebar.model_copy(update={'danger_flags': an.danger_flags})
    if not sidebar.risk_checklist and an.risk_checklist:
        sidebar = sidebar.model_copy(update={'risk_checklist': an.risk_checklist})

    return draft.model_copy(update={'ghost_text': ghost, 'sidebar': sidebar})


def repair_explain(exp: ExplainV1, *, state_before: dict[str, Any] | None, tool_name: str) -> ExplainV1:
    ghost = _clean_text(exp.ghost_text)
    if _looks_truncated(ghost):
        ghost = _fallback_explain_ghost(tool_name)
    else:
        ghost = _ensure_terminal_punct(ghost)

    prev_an = {}
    if isinstance(state_before, dict):
        prev_an = state_before.get('last_analyze') or {}

    danger_flags = exp.danger_flags or _coerce_danger_flags(prev_an.get('danger_flags'))
    risk_checklist = exp.risk_checklist or _coerce_risk_checklist(prev_an.get('risk_checklist'))

    return exp.model_copy(
        update={
            'ghost_text': ghost,
            'danger_flags': danger_flags,
            'risk_checklist': risk_checklist,
        }
    )