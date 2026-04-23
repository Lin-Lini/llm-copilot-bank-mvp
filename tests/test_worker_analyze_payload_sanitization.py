from apps.worker.app.main import _hydrate_analyze
from libs.common.llm_stub import analyze as stub_analyze


def test_hydrate_analyze_sanitizes_stringified_channel_hint():
    history = 'У меня дважды списали одну и ту же сумму за одну покупку.'
    an = stub_analyze(history)
    payload = an.model_dump()
    payload['facts']['channel_hint'] = 'ChannelHint.unknown'

    fixed = _hydrate_analyze(history, payload, prev_analyze=None)

    assert fixed.facts.channel_hint.value == 'unknown'