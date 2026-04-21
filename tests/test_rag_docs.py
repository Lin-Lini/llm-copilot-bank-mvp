from libs.common.rag_docs import clean_blocks, infer_chunk_type, infer_risk_tags, parse_doc_meta, section_priority


def test_parse_doc_meta_and_filter_frontmatter():
    raw = '''Регламент коммуникации, защиты ПДн и противодействия социнжинирингу

ID: REG-SEC-003    Версия: 1.0    Дата: 2026-02-24

Классификация
Внутренний

3. Стандартное предупреждение по безопасности
Банк не запрашивает ПИН-код, CVV/CVC и коды из SMS/Push.
'''
    meta = parse_doc_meta('REG-SEC-003.docx', raw)
    assert meta.doc_code == 'REG-SEC-003'
    assert meta.source_type == 'security'
    blocks = [
        {'section': 'Классификация', 'text': 'Внутренний'},
        {'section': 'Регламент коммуникации, защиты ПДн и противодействия социнжинирингу', 'text': 'Регламент коммуникации, защиты ПДн и противодействия социнжинирингу'},
        {'section': '3. Стандартное предупреждение по безопасности', 'text': 'Банк не запрашивает ПИН-код, CVV/CVC и коды из SMS/Push.'},
    ]
    cleaned = clean_blocks(blocks, meta)
    assert len(cleaned) == 1
    assert 'ПИН-код' in cleaned[0]['text']
    assert cleaned[0]['chunk_type'] == 'warning'
    assert cleaned[0]['is_mandatory_step'] == '1'
    assert 'security' in cleaned[0]['risk_tags']


def test_section_priority_prefers_safety_and_deemphasizes_appendix():
    assert section_priority('Предупреждение по безопасности') > 1.0
    assert section_priority('Приложение A: шаблон заметки') < 1.0


def test_infer_chunk_type_and_risk_tags():
    text = 'Не запрашивать CVV/CVC, ПИН и коды из SMS/Push.'
    assert infer_chunk_type('Предупреждение по безопасности', text) == 'warning'
    tags = infer_risk_tags('Предупреждение по безопасности', text)
    assert 'security' in tags