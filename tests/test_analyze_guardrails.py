from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    ChannelHint,
    DangerFlag,
    Intent,
    Phase,
    ProfileUpdate,
    RiskChecklistItem,
    RiskLevel,
    Severity,
)
from libs.common.analyze_guardrails import normalize_analyze


def _empty_analyze(intent: Intent) -> AnalyzeV1:
    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=Phase.Act,
        confidence=0.84,
        summary_public='stub',
        risk_level=RiskLevel.high,
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=None,
            currency=None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint=ChannelHint.unknown,
            customer_claim='unknown',
            card_in_possession='unknown',
            delivery_pref=None,
            previous_actions=[],
        ),
        profile_update=ProfileUpdate(
            client_card_context='stub',
            recurring_issues=[],
            notes_for_case_file='stub',
        ),
        missing_fields=[],
        next_questions=[],
        tools_suggested=[],
        danger_flags=[],
        risk_checklist=[RiskChecklistItem(id='no_sms_codes', severity=Severity.high, text='Не запрашивать коды.')],
        analytics_tags=[],
    )


def test_mixed_fraud_case_is_normalized_to_suspicious_transaction():
    history = 'Клиент не совершал операцию по карте, карта у него на руках, сообщил код из SMS и просит проверить списание и при необходимости заблокировать карту.'
    raw = _empty_analyze(Intent.BlockCard)

    fixed = normalize_analyze(history, raw)

    assert fixed.intent == Intent.SuspiciousTransaction
    assert fixed.phase == Phase.Collect
    assert 'txn_amount_confirm' in fixed.missing_fields
    assert any(flag.type == 'scam_suspected' for flag in fixed.danger_flags)


def test_sms_code_signal_adds_danger_flag_even_if_model_missed_it():
    history = 'Клиент сообщил код из SMS незнакомому человеку.'
    raw = _empty_analyze(Intent.SuspiciousTransaction)

    fixed = normalize_analyze(history, raw)

    assert fixed.risk_level == RiskLevel.high
    assert any(flag.type == 'scam_suspected' for flag in fixed.danger_flags)