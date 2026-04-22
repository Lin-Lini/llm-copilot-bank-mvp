from __future__ import annotations

import re

from contracts.schemas import AnalyzeV1, DangerFlag, Intent, Phase, RiskLevel, Severity, ToolSuggested


def _has_sms_code_signal(text: str) -> bool:
    patterns = [
        r'код\w*\s+из\s+(sms|смс)',
        r'сообщ\w*\s+код',
        r'одноразов\w*\s+код',
        r'sms[- ]?код',
        r'смс[- ]?код',
        r'push[- ]?код',
        r'код\s+подтверждени',
    ]
    return any(re.search(p, text) for p in patterns)


def _has_suspicious_tx_signal(text: str) -> bool:
    patterns = [
        r'не\s+совершал',
        r'не\s+совершала',
        r'не\s+я\b',
        r'не\s+мой\b',
        r'подозр\w*',
        r'мошен\w*',
        r'списан\w*',
        r'списани\w*',
        r'операц\w*',
        r'дубликат',
        r'двойн\w*\s+списан',
        r'подписк\w*',
    ]
    return any(re.search(p, text) for p in patterns)


def _has_existing_scam_flag(an: AnalyzeV1) -> bool:
    return any((item.type or '') == 'scam_suspected' for item in (an.danger_flags or []))


def _normalized_tools_for_suspicious() -> list[ToolSuggested]:
    return [
        ToolSuggested(
            tool='get_transactions',
            reason='Нужно сверить детали спорной операции по списку транзакций.',
            params_hint={'date_range': 'последние 7 дней'},
        ),
        ToolSuggested(
            tool='create_case',
            reason='Для корректной обработки спорной операции нужно зарегистрировать обращение.',
            params_hint={'intent': 'SuspiciousTransaction'},
        ),
    ]


def normalize_analyze(history: str, an: AnalyzeV1) -> AnalyzeV1:
    text = history.lower()
    suspicious = _has_suspicious_tx_signal(text)
    sms_code = _has_sms_code_signal(text)

    if suspicious and an.intent in {Intent.BlockCard, Intent.LostStolen, Intent.Unknown}:
        missing = ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block']

        an = an.model_copy(
            update={
                'intent': Intent.SuspiciousTransaction,
                'phase': Phase.Collect,
                'confidence': max(float(an.confidence), 0.9),
                'summary_public': 'Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.',
                'missing_fields': missing,
                'next_questions': [
                    'Подтвердите, пожалуйста, что карта сейчас находится у вас на руках (да/нет).',
                    'Подтвердите сумму и примерное время операции, чтобы сверить данные.',
                    'Подтвердите, пожалуйста, что вы не совершали эту операцию.',
                ],
                'analytics_tags': ['suspicious_transaction'],
                'tools_suggested': _normalized_tools_for_suspicious(),
                'profile_update': an.profile_update.model_copy(
                    update={
                        'client_card_context': 'Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.',
                        'recurring_issues': ['suspicious_transaction'],
                        'notes_for_case_file': 'Уточнить детали операции, факт владения картой, признаки компрометации и необходимость блокировки.',
                    }
                ),
                'facts': an.facts.model_copy(
                    update={
                        'customer_claim': 'not_mine',
                        'channel_hint': 'online' if sms_code else an.facts.channel_hint,
                    }
                ),
            }
        )

    if an.intent == Intent.SuspiciousTransaction:
        if not an.missing_fields and an.phase != Phase.Explain:
            an = an.model_copy(
                update={
                    'phase': Phase.Collect,
                    'missing_fields': ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block'],
                    'next_questions': [
                        'Подтвердите, пожалуйста, что карта сейчас находится у вас на руках (да/нет).',
                        'Подтвердите сумму и примерное время операции, чтобы сверить данные.',
                        'Подтвердите, пожалуйста, что вы не совершали эту операцию.',
                    ],
                }
            )

        an = an.model_copy(
            update={
                'analytics_tags': ['suspicious_transaction'],
                'tools_suggested': _normalized_tools_for_suspicious(),
                'profile_update': an.profile_update.model_copy(
                    update={
                        'recurring_issues': ['suspicious_transaction'],
                        'client_card_context': 'Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.',
                        'notes_for_case_file': 'Уточнить детали операции, факт владения картой, признаки компрометации и необходимость блокировки.',
                    }
                ),
            }
        )

    if sms_code and not _has_existing_scam_flag(an):
        an = an.model_copy(
            update={
                'danger_flags': [
                    *(an.danger_flags or []),
                    DangerFlag(
                        type='scam_suspected',
                        severity=Severity.high,
                        text='Есть признаки мошенничества или социальной инженерии; не запрашивайте коды из SMS/Push и предупредите клиента о риске.',
                    ),
                ]
            }
        )

    if sms_code and an.risk_level != RiskLevel.high:
        an = an.model_copy(update={'risk_level': RiskLevel.high})

    return an