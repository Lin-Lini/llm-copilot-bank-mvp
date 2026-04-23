from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    CardState,
    ChannelHint,
    DisputeSubtype,
    Intent,
    Phase,
    ProfileUpdate,
    RequestedAction,
    RiskChecklistItem,
    RiskLevel,
    Severity,
)
from libs.common.case_readiness import required_pending_fields


def _analyze(
    *,
    intent: Intent = Intent.SuspiciousTransaction,
    subtype: DisputeSubtype = DisputeSubtype.unknown,
    card_state: CardState = CardState.unknown,
    card_in_possession: str = 'unknown',
    requested_actions: list[RequestedAction] | None = None,
) -> AnalyzeV1:
    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=Phase.Collect,
        confidence=0.9,
        summary_public='stub',
        risk_level=RiskLevel.medium,
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=None,
            currency=None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint=ChannelHint.unknown,
            customer_claim='unknown',
            card_in_possession=card_in_possession,
            delivery_pref=None,
            previous_actions=[],
            dispute_subtype=subtype,
            card_state=card_state,
            requested_actions=requested_actions or [],
            status_context='unknown',
            compromise_signals=[],
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
        risk_checklist=[RiskChecklistItem(id='no_cvv', severity=Severity.high, text='Не запрашивать CVV/CVC.')],
        analytics_tags=[],
    )


def test_recurring_subscription_with_known_possession_does_not_require_card_in_possession():
    an = _analyze(
        subtype=DisputeSubtype.recurring_subscription,
        card_state=CardState.with_client,
        card_in_possession='yes',
        requested_actions=[RequestedAction.investigate_transaction],
    )

    missing = required_pending_fields(Intent.SuspiciousTransaction, an)

    assert 'card_in_possession' not in missing
    assert 'txn_amount_confirm' in missing
    assert 'txn_datetime_confirm' in missing
    assert 'merchant_name_confirm' in missing


def test_suspicious_transaction_with_unknown_possession_still_requires_card_in_possession():
    an = _analyze(
        subtype=DisputeSubtype.suspicious,
        card_state=CardState.unknown,
        card_in_possession='unknown',
        requested_actions=[RequestedAction.investigate_transaction],
    )

    missing = required_pending_fields(Intent.SuspiciousTransaction, an)

    assert 'card_in_possession' in missing


def test_lost_card_implies_possession_known_for_suspicious_transaction():
    an = _analyze(
        subtype=DisputeSubtype.suspicious,
        card_state=CardState.lost,
        card_in_possession='no',
        requested_actions=[RequestedAction.investigate_transaction],
    )

    missing = required_pending_fields(Intent.SuspiciousTransaction, an)

    assert 'card_in_possession' not in missing