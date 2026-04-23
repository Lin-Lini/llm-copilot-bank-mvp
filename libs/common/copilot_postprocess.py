from __future__ import annotations

import re
from typing import Any

from contracts.schemas import (
    AnalyzeV1,
    CardState,
    DangerFlag,
    DisputeSubtype,
    DraftV1,
    ExplainV1,
    Intent,
    RequestedAction,
    RiskChecklistItem,
)
from libs.common.case_readiness import build_missing_field_meta, build_readiness
from libs.common.state_engine import resolve_tools


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
    'для вашей безопасности необходимо',
    'для вашей безопасности нужно',
    'необходимо не',
    'нужно не',
    'следует не',
)

_INCOMPLETE_ENDINGS_RE = re.compile(
    r'('
    r'сумму и пример|дату и пример|время и пример|что вы не совершали|что карта сейчас|'
    r'для вашей безопасности необходимо|для вашей безопасности нужно|'
    r'необходимо не|нужно не|следует не'
    r')\s*[.!?]?$',
    re.IGNORECASE,
)

_BROKEN_MODAL_RE = re.compile(
    r'(?:для\s+вашей\s+безопасности\s+)?(?:необходимо|нужно|следует|важно|рекомендуется)\s+не\.$',
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
    if _BROKEN_MODAL_RE.search(lower):
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
    actions = set(an.facts.requested_actions or [])
    signals = set(an.facts.compromise_signals or [])
    subtype = an.facts.dispute_subtype
    card_state = an.facts.card_state

    if an.intent in {Intent.LostStolen, Intent.BlockCard} or card_state in {CardState.lost, CardState.stolen}:
        if RequestedAction.reissue_card in actions:
            return (
                'Поскольку карта утеряна или скомпрометирована, для безопасности сначала нужно заблокировать карту. '
                'После подтверждения результата я подскажу, как оформить перевыпуск и какие дальнейшие шаги нужны по обращению.'
            )
        return (
            'Поскольку карта утеряна или есть риск ее компрометации, для безопасности сначала нужно заблокировать карту. '
            'После этого я подскажу следующие шаги и при необходимости помогу оформить обращение.'
        )

    if an.intent == Intent.SuspiciousTransaction:
        if subtype == DisputeSubtype.recurring_subscription:
            return (
                'Пожалуйста, уточните название сервиса или подписки, сумму и примерное время списания. '
                'После этого я подскажу следующий безопасный шаг по проверке операции.'
            )
        if subtype == DisputeSubtype.duplicate_charge:
            return (
                'Пожалуйста, уточните сумму и примерное время операции, а также подтвердите, что списание произошло дважды. '
                'После этого я подскажу следующий безопасный шаг по проверке операции.'
            )
        if subtype == DisputeSubtype.reversal_pending:
            return (
                'Пожалуйста, уточните сумму и примерное время операции и подтвердите, что вы видите холд или резерв. '
                'После этого я подскажу следующий безопасный шаг.'
            )
        if signals or card_state in {CardState.lost, CardState.stolen} or RequestedAction.reissue_card in actions:
            return (
                'Поскольку есть признаки компрометации и проблема с картой, для безопасности сначала нужно подтвердить блокировку карты. '
                'После этого я подскажу, как оформить перевыпуск и какие данные еще нужны для обращения.'
            )
        return (
            'Пожалуйста, уточните сумму и примерное время операции, а также подтвердите, '
            'что карта у вас на руках и что вы не совершали эту операцию. '
            'После этого я подскажу следующий безопасный шаг.'
        )

    if an.intent == Intent.UnblockReissue:
        if RequestedAction.unblock_card in actions:
            return (
                'Пожалуйста, уточните номер обращения или контекст блокировки, чтобы можно было безопасно проверить допустимость разблокировки. '
                'После этого я подскажу следующий шаг.'
            )
        return (
            'Пожалуйста, подтвердите, что вам нужен перевыпуск карты, и кратко уточните причину. '
            'После этого я подскажу следующий безопасный шаг.'
        )

    if an.intent == Intent.CardNotWorking:
        if an.facts.card_state == CardState.damaged:
            return (
                'Похоже, проблема может быть связана с повреждением карты. '
                'Подтвердите это, пожалуйста, и после этого я подскажу следующий шаг по перевыпуску.'
            )
        return (
            'Пожалуйста, уточните, где именно не работает карта: в магазине, онлайн или в банкомате. '
            'После этого я подскажу следующий безопасный шаг.'
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


def _operator_notes(an: AnalyzeV1) -> str:
    if an.intent == Intent.SuspiciousTransaction:
        if an.facts.card_state in {CardState.lost, CardState.stolen}:
            return 'Есть спорная операция на фоне утраты или кражи карты: сначала блокировка и фиксация риска, затем уточнение деталей операции и перевыпуск при необходимости.'
        if an.facts.dispute_subtype == DisputeSubtype.recurring_subscription:
            return 'Сначала уточни название сервиса или подписки, затем переходи к проверке операций и оформлению обращения.'
        if an.facts.dispute_subtype == DisputeSubtype.duplicate_charge:
            return 'Сначала собери подтверждение двойного списания, потом переходи к сверке операций и фиксации обращения.'
        if an.facts.dispute_subtype == DisputeSubtype.reversal_pending:
            return 'Сначала проверь, идет ли речь о холде или резерве, и не предлагай блокировку как первое действие без отдельного подтверждения.'
        return 'Сначала собери подтверждения по операции, затем переходи к сверке и оформлению обращения.'
    if an.intent in {Intent.BlockCard, Intent.LostStolen}:
        return 'При утрате, краже или высоком риске сначала блокировка, затем фиксация кейса и обсуждение перевыпуска.'
    if an.intent == Intent.UnblockReissue:
        return 'Сначала различи запрос на разблокировку и перевыпуск, не обещай разблокировку без подтвержденного контекста.'
    if an.intent == Intent.CardNotWorking:
        if an.facts.card_state == CardState.damaged:
            return 'Сначала подтверди повреждение карты, затем решай вопрос с перевыпуском.'
        return 'Сначала уточни, где именно не работает карта, и только потом проверяй лимиты или настройки.'
    if an.intent == Intent.StatusWhatNext:
        return 'Статус сообщай только по подтвержденному кейсу или номеру обращения, без догадок и обещаний по срокам.'
    return 'Соберите обязательные данные и выполните следующий шаг только после подтверждения клиента.'


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

    tools = resolve_tools(
        an.intent,
        sidebar.phase,
        missing_fields=an.missing_fields,
        analyze=an,
    )
    missing_fields_meta = build_missing_field_meta(an.intent, an.missing_fields, an)
    readiness = build_readiness(
        intent=an.intent,
        missing_fields=an.missing_fields,
        tools=tools,
        case_status='open',
        analyze=an,
    )

    sidebar = sidebar.model_copy(
        update={
            'tools': tools,
            'missing_fields_meta': missing_fields_meta,
            'readiness': readiness,
            'operator_notes': _operator_notes(an),
        }
    )

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