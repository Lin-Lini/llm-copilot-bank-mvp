from libs.common.rag_chunking import chunk_blocks


def test_chunk_blocks_preserve_section():
    blocks = [
        {'section': 'Раздел 1', 'text': 'Первый абзац'},
        {'section': 'Раздел 1', 'text': 'Второй абзац'},
        {'section': 'Раздел 2', 'text': 'Третий абзац'},
    ]
    chunks = chunk_blocks(blocks, max_chars=40, overlap=0)

    assert chunks[0]['section'] == 'Раздел 1'
    assert any(chunk['section'] == 'Раздел 2' for chunk in chunks)


def test_chunk_blocks_preserve_extended_metadata():
    blocks = [
        {
            'section': 'Предупреждение по безопасности',
            'section_path': 'Предупреждение по безопасности',
            'text': 'Не запрашивать CVV/CVC и коды из SMS/Push.',
            'chunk_type': 'warning',
            'risk_tags': 'security,fraud',
            'is_mandatory_step': '1',
        },
        {
            'section': 'Предупреждение по безопасности',
            'section_path': 'Предупреждение по безопасности',
            'text': 'Не предлагать удаленный доступ.',
            'chunk_type': 'warning',
            'risk_tags': 'security,fraud',
            'is_mandatory_step': '1',
        },
    ]

    chunks = chunk_blocks(blocks, max_chars=500, overlap=0)

    assert len(chunks) == 1
    assert chunks[0]['chunk_type'] == 'warning'
    assert chunks[0]['risk_tags'] == 'fraud,security'
    assert chunks[0]['is_mandatory_step'] == '1'
    assert chunks[0]['section_path'] == 'Предупреждение по безопасности'