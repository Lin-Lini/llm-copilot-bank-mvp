from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FRONTMATTER_HEADINGS = {
    'классификация',
    'владелец документа',
    'утверждающий',
    'область действия',
    'связанные документы',
    'срок пересмотра',
}

LOW_VALUE_SECTIONS = {
    'как пользоваться скриптами',
}


@dataclass(slots=True)
class RagDocMeta:
    title: str
    doc_code: str
    version: str
    effective_date: str
    source_type: str
    priority: float


@dataclass(slots=True)
class RagSeedDoc:
    path: Path
    title: str


def _line_value(text: str, label: str) -> str:
    m = re.search(rf'{re.escape(label)}\s*:\s*([^\n\r]+)', text, flags=re.IGNORECASE)
    return (m.group(1).strip() if m else '')


def infer_source_type(title: str, doc_code: str) -> tuple[str, float]:
    t = f'{doc_code} {title}'.lower()
    if doc_code.startswith('SCRIPT-'):
        return 'script', 0.92
    if 'пдн' in t or 'социнжинир' in t or 'безопас' in t or 'мошен' in t:
        return 'security', 1.18
    if 'fallback' in t or 'недоступности инструментов' in t:
        return 'fallback', 1.05
    if 'qa' in t or 'контроль качества' in t:
        return 'qa', 0.96
    if 'эскалац' in t or 'статус' in t or 'регламент' in t:
        return 'policy', 1.12
    return 'procedure', 1.0


SECTION_ALIASES = {
    'цель': 'Цель',
    'цель и принципы': 'Цель и принципы',
    'назначение': 'Назначение',
    'термины и роли': 'Термины и роли',
    'входные условия и минимальный набор данных': 'Входные условия и данные',
    'ограничения по безопасности и пдн': 'Ограничения по безопасности и ПДн',
    'сценарий обработки (end-to-end)': 'Сценарий обработки',
    'sla, статусы и коммуникация': 'SLA, статусы и коммуникация',
    'контроль качества и аудит': 'Контроль качества и аудит',
    'стандартное предупреждение по безопасности': 'Предупреждение по безопасности',
    'действия оператора при красных флагах': 'Красные флаги и действия',
    'контроль передачи данных в llm': 'Контроль передачи данных в LLM',
    'приложение a: чеклист оператора (1 минута)': 'Чеклист оператора',
}


IGNORE_BLOCK_PATTERNS = [
    re.compile(r'^id\s*:', re.IGNORECASE),
    re.compile(r'^версия\s*:', re.IGNORECASE),
    re.compile(r'^дата\s*:', re.IGNORECASE),
]


_RISK_RULES: list[tuple[str, tuple[str, ...]]] = [
    ('security', ('cvv', 'cvc', 'пин', 'pin', 'sms', 'push', 'код', 'пдн', 'паспорт', 'персональн', 'секрет')),
    ('fraud', ('мошен', 'социнж', 'удален', 'удалён', 'remote', 'скам')),
    ('status', ('sla', 'статус', 'эскалац', 'срок', 'ожидание')),
    ('dispute', ('оспар', 'диспут', 'чарджбэк', 'спорн', 'списани')),
    ('card_ops', ('блокиров', 'разблокиров', 'перевыпуск', 'лимит', 'онлайн-платеж', 'онлайн платеж')),
]


def parse_doc_meta(title: str, raw_text: str) -> RagDocMeta:
    lines = [line.strip() for line in (raw_text or '').replace('\r', '').split('\n') if line.strip()]
    title_line = lines[0] if lines else title
    m_code = re.search(r'ID\s*:\s*([A-Z]+-[A-Z]+-\d+)', raw_text, flags=re.IGNORECASE)
    doc_code = m_code.group(1).upper() if m_code else _line_value(raw_text, 'ID')
    if not doc_code:
        stem = Path(title).stem
        m = re.match(r'([A-Z]+-[A-Z]+-\d+)', stem)
        doc_code = m.group(1) if m else stem[:32]
    m_ver = re.search(r'Версия\s*:\s*([0-9.]+)', raw_text, flags=re.IGNORECASE)
    version = (m_ver.group(1) if m_ver else _line_value(raw_text, 'Версия')) or '1.0'
    m_date = re.search(r'Дата\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})', raw_text, flags=re.IGNORECASE)
    effective_date = (m_date.group(1) if m_date else _line_value(raw_text, 'Дата')) or ''
    source_type, priority = infer_source_type(title_line, doc_code)
    return RagDocMeta(
        title=title_line,
        doc_code=doc_code,
        version=version,
        effective_date=effective_date,
        source_type=source_type,
        priority=priority,
    )


def section_priority(section: str) -> float:
    s = (section or '').strip().lower()
    if not s:
        return 1.0
    if s in FRONTMATTER_HEADINGS:
        return 0.0
    if s in LOW_VALUE_SECTIONS:
        return 0.88
    if s.startswith('приложение'):
        return 0.94
    if 'чеклист' in s:
        return 1.03
    if 'красн' in s or 'безопас' in s:
        return 1.08
    if 'сценарий' in s or 'процесс' in s:
        return 1.06
    if 'статус' in s or 'эскалац' in s:
        return 1.05
    return 1.0


def normalize_section_name(section: str) -> str:
    s = (section or '').strip()
    key = s.lower()
    return SECTION_ALIASES.get(key, s or 'Общий раздел')


def is_frontmatter_block(section: str, text: str) -> bool:
    sec = (section or '').strip().lower()
    if sec in FRONTMATTER_HEADINGS:
        return True
    txt = (text or '').strip()
    if not txt:
        return True
    low = txt.lower()
    if low in FRONTMATTER_HEADINGS:
        return True
    if txt in {'Внутренний', 'Внутренний (обучающий материал)'}:
        return True
    if low.startswith('id:') or low.startswith('версия:') or low.startswith('дата:'):
        return True
    return False


def infer_chunk_type(section: str, text: str) -> str:
    sec = (section or '').lower()
    low = (text or '').strip().lower()

    if '|' in text and '\n' in text:
        return 'table'
    if 'чеклист' in sec or 'чеклист' in low:
        return 'checklist'
    if 'предупреждение' in sec or 'внимание' in low or 'запрещено' in low or 'не запрашива' in low:
        return 'warning'
    if re.match(r'^\s*(\d+[\.\)]|[-•])\s+', text):
        return 'step'
    if low.count('\n- ') >= 1 or low.count('\n•') >= 1:
        return 'checklist'
    if 'если' in low and ('то ' in low or 'иначе' in low):
        return 'condition'
    return 'paragraph'


def infer_risk_tags(section: str, text: str) -> str:
    hay = f'{section}\n{text}'.lower()
    tags: list[str] = []
    for tag, parts in _RISK_RULES:
        if any(part in hay for part in parts):
            tags.append(tag)
    return ','.join(sorted(set(tags)))


def is_mandatory_step(section: str, text: str, chunk_type: str | None = None) -> bool:
    low = f'{section}\n{text}'.lower()
    if chunk_type in {'warning', 'checklist'}:
        return True
    markers = ('обязательно', 'должен', 'должна', 'необходимо', 'требуется', 'запрещено', 'нельзя')
    return any(marker in low for marker in markers)


def clean_blocks(blocks: Iterable[dict[str, str]], meta: RagDocMeta) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for block in blocks:
        text = (block.get('text') or '').strip()
        section = normalize_section_name(block.get('section') or meta.title)
        if is_frontmatter_block(section, text):
            continue
        if text == meta.title:
            continue
        if any(p.match(text) for p in IGNORE_BLOCK_PATTERNS):
            continue

        chunk_type = infer_chunk_type(section, text)
        risk_tags = infer_risk_tags(section, text)

        out.append(
            {
                'section': section,
                'section_path': section,
                'text': text,
                'chunk_type': chunk_type,
                'risk_tags': risk_tags,
                'is_mandatory_step': '1' if is_mandatory_step(section, text, chunk_type) else '0',
            }
        )
    return out


RAG_CORPUS_EXTS = {'.docx', '.txt', '.pdf'}


def iter_seed_docs(seed_dir: str | Path) -> list[RagSeedDoc]:
    root = Path(seed_dir)
    if not root.exists():
        return []
    docs: list[RagSeedDoc] = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in RAG_CORPUS_EXTS:
            continue
        docs.append(RagSeedDoc(path=path, title=path.name))
    return docs