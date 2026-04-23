from libs.common.analyze_guardrails import normalize_analyze
from libs.common.llm_stub import analyze as stub_analyze
from contracts.schemas import RequestedAction

def test_merchant_dispute_detected_from_delivery_and_refund_phrase():
    history = (
        'Я оплатил товар картой, но магазин не доставил заказ и деньги не вернул. '
        'Карта у меня, операция моя. Хочу понять, как оспорить ситуацию.'
    )
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent.value == 'SuspiciousTransaction'
    assert an.facts.dispute_subtype.value == 'merchant_dispute'
    assert an.facts.card_state.value == 'with_client'
    assert an.facts.card_in_possession == 'yes'
    assert an.facts.customer_claim == 'unknown'
    assert RequestedAction.investigate_transaction in an.facts.requested_actions