from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from shared.rag_search import hybrid_search
from shared.security import require_actor
from services.backend.app.deps import get_db


router = APIRouter(prefix='/rag', tags=['rag'])


@router.post('/search')
async def rag_search(body: dict, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    query = (body.get('query') or '').strip()
    top_k = int(body.get('top_k') or 5)
    top_k = max(1, min(top_k, 10))

    out = await hybrid_search(db, query, top_k=top_k)
    return {'sources': out}
