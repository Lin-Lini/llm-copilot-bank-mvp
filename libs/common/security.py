from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from libs.common.config import settings


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)


async def require_internal_auth(x_internal_auth: str | None = Header(default=None, alias='X-Internal-Auth')):
    if not x_internal_auth or x_internal_auth != settings.internal_auth_token:
        raise _bad('invalid internal auth')


async def require_actor(
    _=Depends(require_internal_auth),
    x_actor_role: str | None = Header(default=None, alias='X-Actor-Role'),
    x_actor_id: str | None = Header(default=None, alias='X-Actor-Id'),
):
    if not x_actor_role or not x_actor_id:
        raise _bad('missing actor headers')
    return {'role': x_actor_role, 'id': x_actor_id}


async def require_operator(actor=Depends(require_actor)):
    if actor['role'] != 'operator':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='operator only')
    return actor


async def require_service(actor=Depends(require_actor)):
    if actor['role'] != 'service':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='service only')
    return actor
