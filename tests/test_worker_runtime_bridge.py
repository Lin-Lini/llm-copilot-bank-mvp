from apps.worker.app.main import _prepare_runtime_context
from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    CardState,
    ChannelHint,
    CompromiseSignal,
    Intent,
    Phase,
    ProfileUpdate,
    RequestedAction,
    RiskChecklistItem,
    RiskLevel,
    Severity,
)


def _base_analyze(intent: Intent = Intent.SuspiciousTransaction) -> AnalyzeV1:
    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=Phase.Collect,
        confidence=0.81,
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


def test_prepare_runtime_context_enriches_mixed_lost_stolen_scenario():
    history = 'Я не совершал эту операцию, карту потерял, код из SMS сообщил, хочу перевыпуск карты.'
    an = _base_analyze()

    an_model, intent, plan, resolved_phase, missing_fields, tools_ui = _prepare_runtime_context(
        history,
        an.model_dump(),
        safe_mode='ok',
        prev_analyze=None,
    )

    assert intent == Intent.LostStolen
    assert an_model.facts.card_state == CardState.lost
    assert RequestedAction.reissue_card in an_model.facts.requested_actions
    assert CompromiseSignal.sms_code_shared in an_model.facts.compromise_signals
    assert resolved_phase == Phase.Act
    assert any(t.tool.value == 'block_card' and t.enabled for t in tools_ui)


def test_prepare_runtime_context_preserves_previous_context_for_status_query():
    prev = _base_analyze(Intent.LostStolen).model_copy(
        update={
            'summary_public': 'Клиент сообщил об утрате карты и признаках компрометации.',
            'risk_level': RiskLevel.high,
            'analytics_tags': ['loststolen'],
            'facts': _base_analyze(Intent.LostStolen).facts.model_copy(
                update={
                    'card_state': CardState.lost,
                    'requested_actions': [RequestedAction.block_card, RequestedAction.reissue_card],
                    'compromise_signals': [CompromiseSignal.sms_code_shared],
                }
            ),
        }
    )
    current = _base_analyze(Intent.StatusWhatNext)
    history = 'Какой сейчас статус обращения и что дальше?'

    an_model, intent, plan, resolved_phase, missing_fields, tools_ui = _prepare_runtime_context(
        history,
        current.model_dump(),
        safe_mode='ok',
        prev_analyze=prev.model_dump(),
    )

    assert intent == Intent.StatusWhatNext
    assert an_model.facts.card_state == CardState.lost
    assert CompromiseSignal.sms_code_shared in an_model.facts.compromise_signals
    assert an_model.risk_level == RiskLevel.high
    assert any(t.tool.value == 'get_case_status' for t in tools_ui)