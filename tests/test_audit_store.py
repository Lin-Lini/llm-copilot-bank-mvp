from libs.common.audit_store import _normalize


def test_normalize_supports_nested_structures():
    data = {
        'x': 1,
        'y': [{'a': 2}, {'b': 3}],
        'z': ('k', 'm'),
    }
    out = _normalize(data)

    assert out['x'] == 1
    assert out['y'][0]['a'] == 2
    assert out['z'] == ['k', 'm']