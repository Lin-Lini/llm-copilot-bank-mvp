import pytest

from contracts.schemas import (
    CardState,
    DisputeSubtype,
    Intent,
    RequestedAction,
)
from libs.common.analyze_guardrails import normalize_analyze
from libs.common.llm_stub import analyze as stub_analyze


@pytest.mark.parametrize(
    ('history', 'intent', 'subtype', 'card_state', 'requested_action', 'phase', 'missing_field'),
    [
        (
            'Я не совершал операцию по карте, карта у меня, код из SMS уже сообщал.',
            Intent.SuspiciousTransaction,
            DisputeSubtype.suspicious,
            CardState.with_client,
            RequestedAction.investigate_transaction,
            'Collect',
            'txn_datetime_confirm',
        ),
        (
            'Карту потерял, кажется были списания, нужно заблокировать и потом перевыпустить.',
            Intent.LostStolen,
            DisputeSubtype.suspicious,
            CardState.lost,
            RequestedAction.block_card,
            'Act',
            None,
        ),
        (
            'Карту украли, хочу заблокировать и перевыпустить новую.',
            Intent.LostStolen,
            DisputeSubtype.unknown,
            CardState.stolen,
            RequestedAction.reissue_card,
            'Act',
            None,
        ),
        (
            'Онлайн-платеж не проходит, карта у меня.',
            Intent.CardNotWorking,
            DisputeSubtype.unknown,
            CardState.with_client,
            None,
            'Act',
            None,
        ),
        (
            'Списание по подписке, не понимаю откуда оно взялось.',
            Intent.SuspiciousTransaction,
            DisputeSubtype.recurring_subscription,
            CardState.unknown,
            RequestedAction.investigate_transaction,
            'Collect',
            'merchant_name_confirm',
        ),
        (
            'Списали дважды одну и ту же сумму.',
            Intent.SuspiciousTransaction,
            DisputeSubtype.duplicate_charge,
            CardState.unknown,
            RequestedAction.investigate_transaction,
            'Collect',
            'txn_datetime_confirm',
        ),
        (
            'Вижу холд по операции, это уже списание или нет?',
            Intent.SuspiciousTransaction,
            DisputeSubtype.reversal_pending,
            CardState.unknown,
            RequestedAction.investigate_transaction,
            'Collect',
            'txn_datetime_confirm',
        ),
        (
            'У меня есть номер обращения, какой сейчас статус?',
            Intent.StatusWhatNext,
            DisputeSubtype.unknown,
            CardState.unknown,
            RequestedAction.get_case_status,
            'Explain',
            None,
        ),
        (
            'Карта была заблокирована, можно разблокировать?',
            Intent.UnblockReissue,
            DisputeSubtype.unknown,
            CardState.blocked,
            RequestedAction.unblock_card,
            'Collect',
            'case_id',
        ),
        (
            'Операция не моя, карта потеряна, нужен перевыпуск.',
            Intent.LostStolen,
            DisputeSubtype.suspicious,
            CardState.lost,
            RequestedAction.reissue_card,
            'Act',
            None,
        ),
    ],
)
def test_domain_matrix(history, intent, subtype, card_state, requested_action, phase, missing_field):
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent == intent
    assert an.facts.dispute_subtype == subtype
    assert an.facts.card_state == card_state
    assert an.phase.value == phase

    if requested_action is not None:
        assert requested_action in an.facts.requested_actions

    if missing_field is None:
        assert an.missing_fields == [] or missing_field not in an.missing_fields
    else:
        assert missing_field in an.missing_fields

def test_card_not_working_online_minimal_phrase_is_actionable():
    history = 'Онлайн-платеж не проходит, карта у меня.'
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent == Intent.CardNotWorking
    assert an.phase.value == 'Act'
    assert an.facts.channel_hint == 'online'
    assert an.facts.card_state == CardState.with_client
    assert an.facts.card_in_possession == 'yes'
    assert an.facts.customer_claim == 'unknown'
    assert an.facts.dispute_subtype == DisputeSubtype.unknown
    assert 'problem_channel_confirm' not in an.missing_fields