from __future__ import annotations

from fastapi import APIRouter

from .routes.tools import router as tools_router


router = APIRouter(prefix='/api/v1')
router.include_router(tools_router)
