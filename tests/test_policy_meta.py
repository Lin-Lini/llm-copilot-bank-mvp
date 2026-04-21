from libs.common.policy_meta import make_prompt_hash


def test_prompt_hash_is_stable_for_same_payload():
    a = make_prompt_hash({'x': 1, 'y': [1, 2, 3]})
    b = make_prompt_hash({'y': [1, 2, 3], 'x': 1})
    assert a == b


def test_prompt_hash_changes_when_payload_changes():
    a = make_prompt_hash({'x': 1})
    b = make_prompt_hash({'x': 2})
    assert a != b