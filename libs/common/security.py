from __future__ import annotations

from typing import Mapping

from fastapi import Depends, HTTPException, Request, status

from libs.common.internal_auth import verify_internal_headers


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)


def extract_actor_from_headers(headers: Mapping[str, str]) -> dict[str, str | None]:
    claims = verify_internal_headers(headers)
    role = claims.get('actor_role')
    aid = claims.get('actor_id')
    if not role or not aid:
        raise _bad('missing actor identity')
    return {
        'role': str(role),
        'id': str(aid),
        'issuer': str(claims.get('iss') or ''),
        'request_id': str(claims.get('request_id') or ''),
        'origin_role': str(claims.get('origin_actor_role') or '') or None,
        'origin_id': str(claims.get('origin_actor_id') or '') or None,
    }


async def require_internal_auth(request: Request):
    return verify_internal_headers(request.headers)


async def require_actor(claims=Depends(require_internal_auth)):
    role = claims.get('actor_role')
    aid = claims.get('actor_id')
    if not role or not aid:
        raise _bad('missing actor identity')
    return {
        'role': str(role),
        'id': str(aid),
        'issuer': str(claims.get('iss') or ''),
        'request_id': str(claims.get('request_id') or ''),
        'origin_role': str(claims.get('origin_actor_role') or '') or None,
        'origin_id': str(claims.get('origin_actor_id') or '') or None,
    }


async def require_operator(actor=Depends(require_actor)):
    if actor['role'] != 'operator':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='operator only')
    return actor


async def require_service(actor=Depends(require_actor)):
    if actor['role'] != 'service':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='service only')
    return actor