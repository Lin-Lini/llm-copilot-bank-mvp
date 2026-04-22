from libs.common.rag_search import _select_final_results


def test_select_final_results_keeps_security_coverage_for_security_query():
    ranked = [
        {
            'id': 1,
            'doc_id': 'REG-LST-005',
            'doc_code': 'REG-LST-005',
            'title': 'Регламент: утрата, кража карты и компрометация реквизитов',
            'section': 'Раздел 4',
            'section_path': 'Раздел 4',
            'quote': 'Нужно ли заблокировать карту немедленно.',
            'source_type': 'policy',
            'rerank': 1.00,
        },
        {
            'id': 2,
            'doc_id': 'REG-BLK-002',
            'doc_code': 'REG-BLK-002',
            'title': 'Регламент блокировки карты и экстренных действий',
            'section': 'Экстренная блокировка',
            'section_path': 'Экстренная блокировка',
            'quote': 'При утрате или краже карты предложить немедленную блокировку.',
            'source_type': 'security',
            'rerank': 0.78,
        },
        {
            'id': 3,
            'doc_id': 'SCRIPT-OPS-001',
            'doc_code': 'SCRIPT-OPS-001',
            'title': 'Скрипты ответов оператора',
            'section': 'Предупреждение',
            'section_path': 'Предупреждение',
            'quote': 'Пожалуйста, не сообщайте коды из SMS/Push.',
            'source_type': 'script',
            'rerank': 0.70,
        },
    ]

    out = _select_final_results(ranked, top_k=3, security_needed=True)

    assert out[0]['doc_id'] == 'REG-BLK-002'
    assert len(out) == 3