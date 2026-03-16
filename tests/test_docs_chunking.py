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
