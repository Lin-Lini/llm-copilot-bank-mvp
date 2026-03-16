from libs.common.rag_docs import clean_blocks, parse_doc_meta, section_priority


def test_parse_doc_meta_and_filter_frontmatter():
    raw = '''Регламент коммуникации, защиты ПДн и противодействия социнжинирингу\n\nID: REG-SEC-003    Версия: 1.0    Дата: 2026-02-24\n\nКлассификация\nВнутренний\n\n3. Стандартное предупреждение по безопасности\nБанк не запрашивает ПИН-код, CVV/CVC и коды из SMS/Push.\n'''
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


def test_section_priority_prefers_safety_and_deemphasizes_appendix():
    assert section_priority('Предупреждение по безопасности') > 1.0
    assert section_priority('Приложение A: шаблон заметки') < 1.0
