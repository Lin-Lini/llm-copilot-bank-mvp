from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    ChannelHint,
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


def test_mixed_lost_card_sms_and_reissue_is_normalized_to_loststolen_context():
    history = 'Я не совершал эту операцию, карту потерял, код из SMS сообщил, хочу перевыпуск карты.'
    raw = _empty_analyze(Intent.SuspiciousTransaction)

    fixed = normalize_analyze(history, raw)

    assert fixed.intent == Intent.LostStolen
    assert fixed.facts.card_state.value == 'lost'
    assert any(x.value == 'reissue_card' for x in fixed.facts.requested_actions)
    assert any(x.value == 'investigate_transaction' for x in fixed.facts.requested_actions)
    assert any(x.value == 'sms_code_shared' for x in fixed.facts.compromise_signals)
    assert fixed.phase == Phase.Act