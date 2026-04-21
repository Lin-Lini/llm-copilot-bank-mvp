from libs.common.rag_planner import build_search_queries


def test_build_search_queries_adds_safety_and_recall_queries():
    items = build_search_queries('как ответить клиенту про подозрительную операцию и не запрашивать коды sms')
    labels = {item.label for item in items}

    assert 'policy' in labels
    assert 'recall' in labels
    assert 'safety' in labels
    assert 'script' in labels


def test_build_search_queries_extracts_doc_code():
    items = build_search_queries('покажи REG-SEC-003 про безопасность')
    doc_code_items = [item for item in items if item.label == 'doc_code']

    assert len(doc_code_items) == 1
    assert doc_code_items[0].doc_code == 'REG-SEC-003'


def test_build_search_queries_adds_status_path():
    items = build_search_queries('какой статус обращения и что дальше по sla')
    labels = {item.label for item in items}

    assert 'status' in labels