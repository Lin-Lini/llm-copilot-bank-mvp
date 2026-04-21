from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.app.core.deps import get_db
from libs.common.config import settings
from libs.common.embeddings import embed_texts
from libs.common.models import Document, RagChunk
from libs.common.rag_chunking import chunk_blocks, extract_docx_blocks
from libs.common.rag_docs import clean_blocks, iter_seed_docs, parse_doc_meta
from libs.common.security import require_operator


router = APIRouter(prefix='/docs', tags=['docs'])


def _extract_blocks(filename: str, data: bytes) -> list[dict[str, str]]:
    name = filename.lower()
    if name.endswith('.txt'):
        return [{'section': filename, 'text': data.decode('utf-8', errors='ignore')}]

    if name.endswith('.pdf'):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        blocks = []
        for i, page in enumerate(reader.pages, start=1):
            blocks.append({'section': f'Страница {i}', 'text': page.extract_text() or ''})
        return blocks

    if name.endswith('.docx'):
        return extract_docx_blocks(filename, data)

    return [{'section': filename, 'text': ''}]


def _minio():
    from minio import Minio

    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _meta_and_chunks(filename: str, data: bytes) -> tuple[dict, list[dict[str, str]]]:
    raw_blocks = _extract_blocks(filename, data)
    raw_text = '\n'.join((b.get('text') or '').strip() for b in raw_blocks if (b.get('text') or '').strip())
    meta = parse_doc_meta(filename, raw_text)
    cleaned = clean_blocks(raw_blocks, meta)
    chunks = chunk_blocks(cleaned)
    return {
        'title': meta.title,
        'doc_code': meta.doc_code,
        'version_label': meta.version,
        'effective_date': meta.effective_date,
        'source_type': meta.source_type,
        'source_priority': meta.priority,
    }, chunks


async def _upsert_document(db: AsyncSession, *, doc_id: str, storage_key: str, meta: dict) -> Document:
    d = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if d is None:
        d = Document(
            id=doc_id,
            title=meta['title'],
            storage_key=storage_key,
            version=1,
            doc_code=meta['doc_code'],
            version_label=meta['version_label'],
            effective_date=meta['effective_date'],
            source_type=meta['source_type'],
            source_priority=float(meta['source_priority']),
        )
        db.add(d)
    else:
        d.title = meta['title']
        d.storage_key = storage_key
        d.doc_code = meta['doc_code']
        d.version_label = meta['version_label']
        d.effective_date = meta['effective_date']
        d.source_type = meta['source_type']
        d.source_priority = float(meta['source_priority'])
        d.version = max(int(d.version or 1), 1)
    await db.commit()
    return d


async def _store_doc_bytes(*, doc_id: str, filename: str, data: bytes) -> str:
    storage_key = f'{doc_id}/{filename}'
    c = _minio()
    bucket = settings.minio_bucket
    if not c.bucket_exists(bucket):
        c.make_bucket(bucket)
    c.put_object(
        bucket,
        storage_key,
        io.BytesIO(data),
        length=len(data),
        content_type='application/octet-stream',
    )
    return storage_key


async def _index_document(db: AsyncSession, d: Document, chunks: list[dict[str, str]]) -> int:
    await db.execute(delete(RagChunk).where(RagChunk.doc_id == d.id))
    await db.commit()

    if not chunks:
        return 0

    batch_size = 128
    chunk_no = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        embs = await embed_texts([item['text'] for item in batch])
        for item, emb in zip(batch, embs):
            chunk_no += 1
            db.add(
                RagChunk(
                    doc_id=d.id,
                    title=d.title,
                    doc_code=d.doc_code,
                    version_label=d.version_label,
                    effective_date=d.effective_date,
                    source_type=d.source_type,
                    source_priority=float(d.source_priority or 1.0),
                    section=(item.get('section') or '')[:256],
                    section_path=(item.get('section_path') or item.get('section') or '')[:512],
                    chunk_type=(item.get('chunk_type') or 'paragraph')[:32],
                    risk_tags=(item.get('risk_tags') or '')[:256],
                    is_mandatory_step=str(item.get('is_mandatory_step') or '') in {'1', 'true', 'True'},
                    text=item['text'],
                    embedding=emb,
                )
            )
        await db.commit()
    return chunk_no


async def _load_doc_bytes(storage_key: str) -> bytes:
    c = _minio()
    bucket = settings.minio_bucket
    obj = c.get_object(bucket, storage_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


@router.post('/upload')
async def upload_doc(
    file: UploadFile = File(...),
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    doc_id = f'doc-{uuid.uuid4().hex[:10]}'
    meta, chunks = _meta_and_chunks(file.filename, data)
    storage_key = await _store_doc_bytes(doc_id=doc_id, filename=file.filename, data=data)
    d = await _upsert_document(db, doc_id=doc_id, storage_key=storage_key, meta=meta)
    indexed_chunks = await _index_document(db, d, chunks)

    return {
        'doc_id': d.id,
        'title': d.title,
        'doc_code': d.doc_code,
        'source_type': d.source_type,
        'version': d.version,
        'version_label': d.version_label,
        'indexed_chunks': indexed_chunks,
        'auto_indexed': True,
    }


@router.post('/bootstrap-seed')
async def bootstrap_seed(actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    seed_docs = iter_seed_docs(settings.rag_seed_dir)
    loaded = 0
    indexed_docs = 0
    indexed_chunks = 0

    for item in seed_docs:
        data = item.path.read_bytes()
        meta, chunks = _meta_and_chunks(item.title, data)
        doc_id = meta['doc_code'] or f'doc-{uuid.uuid4().hex[:10]}'
        storage_key = await _store_doc_bytes(doc_id=doc_id, filename=item.title, data=data)
        d = await _upsert_document(db, doc_id=doc_id, storage_key=storage_key, meta=meta)
        loaded += 1
        indexed_chunks += await _index_document(db, d, chunks)
        indexed_docs += 1

    return {
        'seed_dir': str(Path(settings.rag_seed_dir)),
        'loaded_docs': loaded,
        'indexed_docs': indexed_docs,
        'indexed_chunks': indexed_chunks,
    }


@router.post('/reindex')
async def reindex(
    doc_id: str | None = Query(default=None),
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    q = select(Document)
    if doc_id:
        q = q.where(Document.id == doc_id)
    docs = (await db.execute(q)).scalars().all()
    if doc_id and not docs:
        raise HTTPException(status_code=404, detail='document not found')

    indexed = 0
    indexed_chunks = 0

    for d in docs:
        data = await _load_doc_bytes(d.storage_key)
        meta, chunks = _meta_and_chunks(Path(d.storage_key).name, data)
        d.title = meta['title']
        d.doc_code = meta['doc_code']
        d.version_label = meta['version_label']
        d.effective_date = meta['effective_date']
        d.source_type = meta['source_type']
        d.source_priority = float(meta['source_priority'])
        await db.commit()

        indexed += 1
        indexed_chunks += await _index_document(db, d, chunks)

    return {
        'indexed_docs': indexed,
        'indexed_chunks': indexed_chunks,
        'batch_size': 128,
        'doc_id': doc_id,
    }


@router.get('')
async def list_docs(actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(Document).order_by(Document.created_at.desc()))).scalars().all()
    return {
        'items': [
            {
                'doc_id': d.id,
                'title': d.title,
                'doc_code': d.doc_code,
                'source_type': d.source_type,
                'version': d.version,
                'version_label': d.version_label,
                'effective_date': d.effective_date,
                'created_at': d.created_at.isoformat(),
            }
            for d in docs
        ]
    }


@router.get('/{doc_id}')
async def doc_meta(doc_id: str, actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    d = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not d:
        return {'error': 'not_found'}
    return {
        'doc_id': d.id,
        'title': d.title,
        'doc_code': d.doc_code,
        'source_type': d.source_type,
        'version': d.version,
        'version_label': d.version_label,
        'effective_date': d.effective_date,
        'storage_key': d.storage_key,
    }


@router.get('/{doc_id}/chunks')
async def doc_chunks(
    doc_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    d = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail='document not found')

    rows = (
        await db.execute(
            select(RagChunk)
            .where(RagChunk.doc_id == doc_id)
            .order_by(RagChunk.id.asc())
            .limit(limit)
        )
    ).scalars().all()

    return {
        'doc_id': doc_id,
        'title': d.title,
        'items': [
            {
                'id': row.id,
                'section': row.section,
                'section_path': row.section_path,
                'chunk_type': row.chunk_type,
                'risk_tags': row.risk_tags,
                'is_mandatory_step': row.is_mandatory_step,
                'text': row.text,
            }
            for row in rows
        ],
    }