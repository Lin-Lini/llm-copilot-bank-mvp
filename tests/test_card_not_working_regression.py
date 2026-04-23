from contracts.schemas import CardState, Intent
from libs.common.analyze_guardrails import normalize_analyze
from libs.common.case_readiness import required_pending_fields
from libs.common.llm_stub import analyze as stub_analyze


def test_card_not_working_online_sets_channel_and_not_suspicious():
    history = (
        'У меня карта на руках, но онлайн-платеж не проходит. '
        'В магазине карта работает, ничего не терял, ничего подозрительного не было. '
        'Хочу понять, не отключены ли онлайн-платежи или лимиты.'
    )
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent == Intent.CardNotWorking
    assert an.facts.channel_hint == 'online'
    assert an.facts.card_state == CardState.with_client
    assert an.facts.card_in_possession == 'yes'
    assert an.facts.dispute_subtype.value == 'unknown'
    assert an.facts.customer_claim == 'unknown'
    assert 'problem_channel_confirm' not in an.missing_fields


def test_case_readiness_does_not_require_problem_channel_when_channel_is_known():
    history = (
        'У меня карта на руках, но онлайн-платеж не проходит. '
        'В магазине карта работает, ничего не терял.'
    )
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    missing = required_pending_fields(Intent.CardNotWorking, an)

    assert an.facts.channel_hint == 'online'
    assert missing == []