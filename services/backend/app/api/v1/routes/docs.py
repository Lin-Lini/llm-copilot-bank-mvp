from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.embeddings import embed_texts
from shared.models import Document, RagChunk
from shared.security import require_actor
from services.backend.app.deps import get_db


router = APIRouter(prefix='/docs', tags=['docs'])


def _chunk(text: str, max_chars: int = 900, overlap: int = 140) -> list[str]:
    """Chunk by paragraphs with small overlap.

    Регламенты/скрипты обычно структурированы абзацами. Обычный line-based
    чанк ломает смысл и ухудшает RAG.
    """
    text = (text or '').replace('\r', '')
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    parts: list[str] = []
    cur = ''
    for p in paras:
        if not cur:
            cur = p
            continue
        if len(cur) + len(p) + 2 <= max_chars:
            cur = cur + '\n\n' + p
            continue
        parts.append(cur)
        # overlap tail
        tail = cur[-overlap:] if overlap > 0 else ''
        cur = (tail + '\n\n' + p).strip()
    if cur:
        parts.append(cur)
    return parts


def _extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith('.txt'):
        return data.decode('utf-8', errors='ignore')

    if name.endswith('.pdf'):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        out = []
        for p in reader.pages:
            out.append(p.extract_text() or '')
        return '\n'.join(out)

    if name.endswith('.docx'):
        # легкий парсер docx через zip xml (без тяжёлых либ)
        import zipfile
        from xml.etree import ElementTree as ET
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read('word/document.xml')
        root = ET.fromstring(xml)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        texts = [t.text for t in root.findall('.//w:t', ns) if t.text]
        # join with spaces so words don't glue together
        return ' '.join(texts)

    return ''


def _minio():
    from minio import Minio
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


@router.post('/upload')
async def upload_doc(
    file: UploadFile = File(...),
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    doc_id = f'doc-{uuid.uuid4().hex[:10]}'
    storage_key = f'{doc_id}/{file.filename}'

    c = _minio()
    bucket = settings.minio_bucket
    if not c.bucket_exists(bucket):
        c.make_bucket(bucket)

    c.put_object(bucket, storage_key, io.BytesIO(data), length=len(data), content_type=file.content_type or 'application/octet-stream')

    title = file.filename
    d = Document(id=doc_id, title=title, storage_key=storage_key, version=1)
    db.add(d)
    await db.commit()

    return {'doc_id': doc_id, 'title': title, 'version': 1}


@router.post('/reindex')
async def reindex(actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(Document))).scalars().all()
    c = _minio()
    bucket = settings.minio_bucket

    indexed = 0
    for d in docs:
        obj = c.get_object(bucket, d.storage_key)
        data = obj.read()
        text = _extract_text(d.title, data)
        chunks = _chunk(text)

        # удалить старые чанки
        await db.execute(delete(RagChunk).where(RagChunk.doc_id == d.id))
        await db.commit()

        # batch embeddings (real provider can do this fast)
        batch = chunks[:500]
        embs = await embed_texts(batch)
        for i, (ch, emb) in enumerate(zip(batch, embs)):
            rc = RagChunk(doc_id=d.id, title=d.title, section=f'chunk-{i+1}', text=ch, embedding=emb)
            db.add(rc)
        await db.commit()
        indexed += 1

    return {'indexed_docs': indexed}


@router.get('')
async def list_docs(actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(Document).order_by(Document.created_at.desc()))).scalars().all()
    return {'items': [{'doc_id': d.id, 'title': d.title, 'version': d.version, 'created_at': d.created_at.isoformat()} for d in docs]}


@router.get('/{doc_id}')
async def doc_meta(doc_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    d = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not d:
        return {'error': 'not_found'}
    return {'doc_id': d.id, 'title': d.title, 'version': d.version, 'storage_key': d.storage_key}
