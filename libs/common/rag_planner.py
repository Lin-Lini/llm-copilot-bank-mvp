from __future__ import annotations

import re
from dataclasses import dataclass


_STOP_WORDS = {
    'и', 'или', 'в', 'во', 'на', 'по', 'для', 'с', 'со', 'к', 'ко', 'у', 'о', 'об', 'от',
    'до', 'из', 'за', 'над', 'под', 'при', 'что', 'как', 'это', 'а', 'но', 'же', 'ли',
    'бы', 'не', 'ни', 'мы', 'вы', 'он', 'она', 'они', 'его', 'ее', 'их', 'клиент',
    'оператор', 'банка', 'банк', 'нужно', 'надо', 'можно', 'если', 'когда', 'после',
    'перед', 'через', 'еще', 'ещё', 'только',
}

_SECURITY_TERMS = {
    'безопас', 'мошен', 'социнж', 'удален', 'удалён', 'cvv', 'cvc', 'pin',
    'пин', 'код', 'sms', 'push', 'пдн', 'секрет', 'компрометац',
}
_STATUS_TERMS = {
    'статус', 'срок', 'sla', 'эскалац', 'что дальше', 'когда', 'ожидание',
}
_SCRIPT_TERMS = {
    'сказать', 'ответ', 'формулиров', 'как ответить', 'скрипт', 'предупреждение',
    'сообщение', 'ghost', 'черновик',
}
_CARD_OPS_TERMS = {
    'блокиров', 'разблокиров', 'перевыпуск', 'лимит', 'онлайн', 'платеж',
    'карта', 'оспарив', 'спорн', 'операц', 'диспут',
}
_DISPUTE_TERMS = {
    'оспар', 'диспут', 'спорн', 'списан', 'платеж', 'платёж', 'чарджбэк',
    'chargeback', 'дубликат', 'отложен', 'подписк',
}
_LOST_STOLEN_TERMS = {
    'потер', 'утрат', 'украд', 'краж', 'пропал', 'компрометац', 'карта',
}
_FALLBACK_TERMS = {
    'недоступ', 'fallback', 'фолбэк', 'резерв', 'временно', 'инструмент',
    'сервис', 'не работает', 'ошибка', 'недоступны',
}

_DOC_CODE_RE = re.compile(r'\b([A-Z]+-[A-Z]+-\d+)\b', flags=re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class PlannedQuery:
    label: str
    query: str
    weight: float = 1.0
    source_types: tuple[str, ...] = ()
    prefer_chunk_types: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    doc_code: str | None = None


def normalize_query(text: str) -> str:
    return ' '.join((text or '').replace('\r', ' ').replace('\n', ' ').split()).strip()


def significant_terms(text: str, *, limit: int = 8) -> list[str]:
    raw = re.findall(r'[a-zA-Zа-яА-Я0-9_-]+', (text or '').lower())
    out: list[str] = []
    seen: set[str] = set()

    for token in raw:
        if len(token) < 3:
            continue
        if token in _STOP_WORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _has_any(text: str, terms: set[str]) -> bool:
    low = (text or '').lower()
    return any(term in low for term in terms)


def build_search_queries(user_query: str) -> list[PlannedQuery]:
    q = normalize_query(user_query)
    if not q:
        return []

    planned: list[PlannedQuery] = []
    seen: set[tuple[str, str, str | None]] = set()
    terms = significant_terms(q, limit=10)

    def add(item: PlannedQuery) -> None:
        key = (item.label, item.query, item.doc_code)
        if not item.query or key in seen:
            return
        seen.add(key)
        planned.append(item)

    add(
        PlannedQuery(
            label='policy',
            query=q,
            weight=1.0,
            source_types=('policy', 'procedure', 'security', 'fallback'),
            prefer_chunk_types=('step', 'checklist', 'condition', 'warning'),
        )
    )

    recall_q = ' '.join(terms[:6])
    if recall_q and recall_q != q.lower():
        add(
            PlannedQuery(
                label='recall',
                query=recall_q,
                weight=0.82,
                source_types=('policy', 'procedure', 'script', 'security', 'fallback'),
                prefer_chunk_types=('step', 'checklist', 'warning', 'paragraph'),
            )
        )

    doc_code_match = _DOC_CODE_RE.search(q)
    if doc_code_match:
        doc_code = doc_code_match.group(1).upper()
        add(
            PlannedQuery(
                label='doc_code',
                query=doc_code,
                weight=1.08,
                source_types=('policy', 'procedure', 'script', 'security', 'fallback'),
                prefer_chunk_types=('step', 'warning', 'checklist', 'paragraph'),
                doc_code=doc_code,
            )
        )

    if _has_any(q, _SECURITY_TERMS):
        safety_terms = [
            t for t in terms
            if any(x in t for x in ('мошен', 'безопас', 'код', 'cvv', 'cvc', 'pin', 'пин', 'пдн', 'sms', 'push', 'социнж', 'удален', 'компрометац'))
        ]
        add(
            PlannedQuery(
                label='safety',
                query=' '.join(safety_terms) or q,
                weight=1.16,
                source_types=('security', 'policy'),
                prefer_chunk_types=('warning', 'checklist', 'condition'),
                risk_tags=('security', 'fraud'),
            )
        )

    if _has_any(q, _DISPUTE_TERMS):
        dispute_terms = [
            t for t in terms
            if any(x in t for x in ('оспар', 'диспут', 'спорн', 'списан', 'платеж', 'платёж', 'дубликат', 'подписк', 'chargeback'))
        ]
        add(
            PlannedQuery(
                label='dispute',
                query=' '.join(dispute_terms) or q,
                weight=1.04,
                source_types=('policy', 'procedure', 'security'),
                prefer_chunk_types=('step', 'condition', 'checklist', 'paragraph'),
                risk_tags=('dispute', 'fraud'),
            )
        )

    if _has_any(q, _LOST_STOLEN_TERMS):
        lost_terms = [
            t for t in terms
            if any(x in t for x in ('потер', 'утрат', 'украд', 'краж', 'пропал', 'компрометац', 'карт'))
        ]
        add(
            PlannedQuery(
                label='lost_stolen',
                query=' '.join(lost_terms + ['блокировка']) or q,
                weight=1.12,
                source_types=('security', 'policy', 'procedure'),
                prefer_chunk_types=('step', 'condition', 'checklist', 'warning'),
                risk_tags=('security', 'lost_stolen'),
            )
        )

    if _has_any(q, _FALLBACK_TERMS):
        fallback_terms = [
            t for t in terms
            if any(x in t for x in ('недоступ', 'fallback', 'фолбэк', 'резерв', 'временн', 'инструмент', 'сервис', 'ошибк'))
        ]
        add(
            PlannedQuery(
                label='fallback',
                query=' '.join(fallback_terms + ['fallback']) or q,
                weight=1.18,
                source_types=('fallback', 'procedure', 'policy', 'script'),
                prefer_chunk_types=('step', 'condition', 'paragraph', 'checklist'),
                risk_tags=('fallback',),
            )
        )

    if _has_any(q, _STATUS_TERMS):
        status_terms = [t for t in terms if any(x in t for x in ('статус', 'срок', 'sla', 'эскалац', 'дальше', 'ожид'))]
        add(
            PlannedQuery(
                label='status',
                query=' '.join(status_terms) or q,
                weight=0.96,
                source_types=('policy', 'procedure'),
                prefer_chunk_types=('condition', 'step', 'paragraph'),
                risk_tags=('status',),
            )
        )

    if _has_any(q, _SCRIPT_TERMS):
        script_terms = [t for t in terms if any(x in t for x in ('сказать', 'ответ', 'скрипт', 'сообщ', 'предупрежд', 'формулиров'))]
        add(
            PlannedQuery(
                label='script',
                query=' '.join(script_terms) or q,
                weight=0.92,
                source_types=('script', 'security', 'policy'),
                prefer_chunk_types=('warning', 'step', 'paragraph'),
            )
        )

    if _has_any(q, _CARD_OPS_TERMS):
        ops_terms = [
            t for t in terms
            if any(x in t for x in ('блок', 'разблок', 'перевыпуск', 'лимит', 'онлайн', 'карта', 'операц', 'оспар', 'диспут'))
        ]
        add(
            PlannedQuery(
                label='card_ops',
                query=' '.join(ops_terms) or q,
                weight=0.94,
                source_types=('security', 'policy', 'procedure'),
                prefer_chunk_types=('step', 'condition', 'checklist'),
                risk_tags=('card_ops', 'dispute'),
            )
        )

    return planned