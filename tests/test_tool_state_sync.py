from libs.common.tool_state_sync import sync_after_create_case


def test_sync_after_create_case_replaces_stale_blockcard_state():
    prev_state = {
        'conversation_id': 'conv-1',
        'intent': 'BlockCard',
        'phase': 'Act',
        'plan': {'current_step_id': 'block_now', 'steps': []},
        'last_analyze': {
            'intent': 'BlockCard',
            'phase': 'Act',
            'missing_fields': [],
            'analytics_tags': ['block_card'],
            'tools_suggested': [
                {'tool': 'block_card', 'reason': 'old', 'params_hint': {}},
                {'tool': 'create_case', 'reason': 'old', 'params_hint': {'intent': 'BlockCard'}},
            ],
        },
        'last_draft': {'sidebar': {'intent': 'BlockCard'}},
    }

    tool_result = {
        'case_id': 'case-1',
        'case_type': 'SuspiciousTransaction',
        'status': 'open',
    }

    synced = sync_after_create_case(prev_state, tool_result)

    assert synced['intent'] == 'SuspiciousTransaction'
    assert synced['phase'] == 'Collect'
    assert synced['last_analyze']['intent'] == 'SuspiciousTransaction'
    assert synced['last_analyze']['analytics_tags'] == ['suspicious_transaction']
    assert synced['last_analyze']['tools_suggested'][1]['params_hint']['intent'] == 'SuspiciousTransaction'
    assert 'txn_amount_confirm' in synced['last_analyze']['missing_fields']
    assert synced['last_draft'] is None
    assert any(step['id'] == 'case_create' and step['done'] is True for step in synced['plan']['steps'])