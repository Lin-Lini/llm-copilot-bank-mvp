from libs.common.rag_planner import build_search_queries


def test_build_search_queries_adds_safety_and_recall_queries():
    items = build_search_queries('как ответить клиенту про подозрительную операцию и не запрашивать коды sms')
    labels = {item.label for item in items}

    assert 'policy' in labels
    assert 'recall' in labels
    assert 'safety' in labels
    assert 'script' in labels
    assert 'card_ops' in labels


def test_build_search_queries_extracts_doc_code():
    items = build_search_queries('покажи REG-SEC-003 про безопасность')
    doc_code_items = [item for item in items if item.label == 'doc_code']

    assert len(doc_code_items) == 1
    assert doc_code_items[0].doc_code == 'REG-SEC-003'


def test_build_search_queries_adds_status_path():
    items = build_search_queries('какой статус обращения и что дальше по sla')
    labels = {item.label for item in items}

    assert 'status' in labels


def test_safety_query_prefers_security_and_policy_sources():
    items = build_search_queries('клиент сообщил код из SMS и есть риск мошенничества')
    safety = [item for item in items if item.label == 'safety'][0]

    assert safety.source_types == ('security', 'policy')
    assert 'security' in safety.risk_tags
    assert 'fraud' in safety.risk_tags


def test_build_search_queries_adds_lost_stolen_and_fallback_paths():
    lost = build_search_queries('карта потеряна или украдена, что делать дальше')
    fallback = build_search_queries('что делать если внутренние инструменты временно недоступны')

    lost_labels = {item.label for item in lost}
    fallback_labels = {item.label for item in fallback}

    assert 'lost_stolen' in lost_labels
    assert 'fallback' in fallback_labels