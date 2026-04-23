from libs.common.analyze_guardrails import normalize_analyze
from libs.common.llm_stub import analyze as stub_analyze


def test_duplicate_charge_keeps_customer_claim_unknown_without_explicit_not_mine():
    history = (
        'У меня дважды списали одну и ту же сумму за одну покупку. '
        'Карта у меня, ничего подозрительного не было.'
    )
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent.value == 'SuspiciousTransaction'
    assert an.facts.dispute_subtype.value == 'duplicate_charge'
    assert an.facts.customer_claim == 'unknown'


def test_reversal_pending_keeps_customer_claim_unknown_without_explicit_not_mine():
    history = (
        'По карте вижу холд и резерв по операции, но не понимаю, '
        'это уже списание или нет. Карта у меня, ничего не терял.'
    )
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.intent.value == 'SuspiciousTransaction'
    assert an.facts.dispute_subtype.value == 'reversal_pending'
    assert an.facts.customer_claim == 'unknown'


def test_suspicious_transaction_sets_not_mine_when_explicit():
    history = 'Я не совершал эту операцию, карта у меня.'
    raw = stub_analyze(history)
    an = normalize_analyze(history, raw)

    assert an.facts.dispute_subtype.value == 'suspicious'
    assert an.facts.customer_claim == 'not_mine'