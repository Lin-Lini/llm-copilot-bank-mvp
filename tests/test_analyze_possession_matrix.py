from contracts.schemas import CardState, DisputeSubtype, Intent
from libs.common.analyze_guardrails import normalize_analyze
from libs.common.llm_stub import analyze as stub_analyze


def test_recurring_subscription_with_explicit_possession_marks_card_as_with_client():
    history = 'У меня списание по подписке, карту я не терял, она у меня, хочу разобраться что это за сервис.'
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent == Intent.SuspiciousTransaction
    assert an.facts.dispute_subtype == DisputeSubtype.recurring_subscription
    assert an.facts.card_state == CardState.with_client
    assert an.facts.card_in_possession == 'yes'
    assert 'card_in_possession' not in an.missing_fields
    assert 'merchant_name_confirm' in an.missing_fields


def test_explicit_negative_loss_keeps_card_with_client():
    history = 'Операция не моя, карту не терял, карта у меня на руках.'
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent == Intent.SuspiciousTransaction
    assert an.facts.dispute_subtype == DisputeSubtype.suspicious
    assert an.facts.card_state == CardState.with_client
    assert an.facts.card_in_possession == 'yes'
    assert 'card_in_possession' not in an.missing_fields


def test_explicit_loss_still_sets_card_not_in_possession():
    history = 'Карту потерял, была спорная операция, нужен перевыпуск.'
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent == Intent.LostStolen
    assert an.facts.card_state == CardState.lost
    assert an.facts.card_in_possession == 'no'