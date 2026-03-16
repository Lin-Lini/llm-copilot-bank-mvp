from __future__ import annotations

from fastapi import APIRouter

from .routes.audit import router as audit_router
from .routes.cases import router as cases_router
from .routes.chat import router as chat_router
from .routes.copilot import router as copilot_router
from .routes.docs import router as docs_router
from .routes.internal import router as internal_router
from .routes.rag import router as rag_router


router = APIRouter(prefix='/api/v1')
router.include_router(chat_router)
router.include_router(cases_router)
router.include_router(copilot_router)
router.include_router(rag_router, include_in_schema=False)
router.include_router(docs_router, include_in_schema=False)
router.include_router(audit_router, include_in_schema=False)
router.include_router(internal_router, include_in_schema=False)
