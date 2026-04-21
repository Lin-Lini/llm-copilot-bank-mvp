from __future__ import annotations

import io
from collections.abc import Iterable


def normalize_text(text: str) -> str:
    return '\n'.join(line.rstrip() for line in (text or '').replace('\r', '').split('\n')).strip()


def _merge_chunk_type(types: list[str]) -> str:
    if not types:
        return 'paragraph'
    uniq = [t for t in types if t]
    if not uniq:
        return 'paragraph'
    if len(set(uniq)) == 1:
        return uniq[0]
    priority = ['warning', 'checklist', 'step', 'condition', 'table', 'paragraph']
    for item in priority:
        if item in uniq:
            return item
    return uniq[0]


def chunk_blocks(
    blocks: Iterable[dict[str, str]],
    *,
    max_chars: int = 900,
    overlap: int = 140,
) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    cur_section = ''
    cur_parts: list[str] = []
    cur_meta: list[dict[str, str]] = []

    def flush():
        nonlocal cur_parts, cur_section, cur_meta
        text = '\n\n'.join(part for part in cur_parts if part).strip()
        if text:
            risk_tags: set[str] = set()
            chunk_types: list[str] = []
            section_path = cur_section or 'Общий раздел'
            mandatory = False

            for meta in cur_meta:
                tags = (meta.get('risk_tags') or '').split(',')
                for tag in tags:
                    tag = tag.strip()
                    if tag:
                        risk_tags.add(tag)
                chunk_type = (meta.get('chunk_type') or '').strip()
                if chunk_type:
                    chunk_types.append(chunk_type)
                if meta.get('section_path'):
                    section_path = meta['section_path']
                if str(meta.get('is_mandatory_step') or '') in {'1', 'true', 'True'}:
                    mandatory = True

            chunks.append(
                {
                    'section': cur_section or 'Общий раздел',
                    'section_path': section_path,
                    'text': text,
                    'chunk_type': _merge_chunk_type(chunk_types),
                    'risk_tags': ','.join(sorted(risk_tags)),
                    'is_mandatory_step': '1' if mandatory else '0',
                }
            )
        cur_parts = []
        cur_meta = []

    for block in blocks:
        text = normalize_text(block.get('text', ''))
        if not text:
            continue
        section = block.get('section') or cur_section or 'Общий раздел'
        meta = {
            'section_path': block.get('section_path') or section,
            'chunk_type': block.get('chunk_type') or 'paragraph',
            'risk_tags': block.get('risk_tags') or '',
            'is_mandatory_step': block.get('is_mandatory_step') or '0',
        }

        if not cur_parts:
            cur_section = section
            cur_parts = [text]
            cur_meta = [meta]
            continue

        candidate = '\n\n'.join(cur_parts + [text])
        if section == cur_section and len(candidate) <= max_chars:
            cur_parts.append(text)
            cur_meta.append(meta)
            continue

        flush()
        cur_section = section
        if overlap > 0 and chunks:
            tail = chunks[-1]['text'][-overlap:].strip()
            cur_parts = [tail, text] if tail else [text]
        else:
            cur_parts = [text]
        cur_meta = [meta]

    flush()
    return chunks


def extract_docx_blocks(filename: str, data: bytes) -> list[dict[str, str]]:
    import zipfile
    from xml.etree import ElementTree as ET

    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    z = zipfile.ZipFile(io.BytesIO(data))

    styles_map: dict[str, str] = {}
    if 'word/styles.xml' in z.namelist():
        styles_xml = ET.fromstring(z.read('word/styles.xml'))
        for st in styles_xml.findall('.//w:style', ns):
            style_id = st.attrib.get(f'{{{ns["w"]}}}styleId')
            name_el = st.find('w:name', ns)
            if style_id and name_el is not None:
                styles_map[style_id] = name_el.attrib.get(f'{{{ns["w"]}}}val', style_id)

    root = ET.fromstring(z.read('word/document.xml'))
    body = root.find('w:body', ns)
    if body is None:
        return [{'section': filename, 'text': ''}]

    blocks: list[dict[str, str]] = []
    current_section = filename

    def paragraph_text(p) -> str:
        texts: list[str] = []
        for node in p.findall('.//w:t', ns):
            if node.text:
                texts.append(node.text)
        return ''.join(texts).strip()

    def paragraph_style_name(p) -> str:
        p_style = p.find('./w:pPr/w:pStyle', ns)
        if p_style is None:
            return ''
        style_id = p_style.attrib.get(f'{{{ns["w"]}}}val', '')
        return styles_map.get(style_id, style_id)

    for child in list(body):
        tag = child.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            text = paragraph_text(child)
            if not text:
                continue
            style_name = paragraph_style_name(child).lower()
            is_heading = (
                style_name.startswith('heading')
                or 'заголовок' in style_name
                or style_name in {'title', 'subtitle'}
            )
            if is_heading:
                current_section = text
            blocks.append({'section': current_section, 'text': text})
        elif tag == 'tbl':
            rows: list[str] = []
            for tr in child.findall('.//w:tr', ns):
                cells: list[str] = []
                for tc in tr.findall('./w:tc', ns):
                    cell_parts = [paragraph_text(p) for p in tc.findall('.//w:p', ns)]
                    cell_text = ' '.join(part for part in cell_parts if part).strip()
                    cells.append(cell_text)
                row_text = ' | '.join(cell for cell in cells if cell)
                if row_text:
                    rows.append(row_text)
            if rows:
                blocks.append({'section': current_section, 'text': '\n'.join(rows)})

    return blocks