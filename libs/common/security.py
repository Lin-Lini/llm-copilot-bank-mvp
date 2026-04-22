from __future__ import annotations

from typing import Any, Mapping

from fastapi import Header, HTTPException, Request, status

from libs.common.config import settings
from libs.common.internal_auth import verify_internal_headers


ALLOWED_ACTOR_ROLES = {'operator', 'service'}


def _cfg_bool(name: str, default: bool) -> bool:
    try:
        value = getattr(settings, name, default)
    except Exception:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _build_actor(claims: dict[str, Any]) -> dict[str, Any]:
    return {
        'role': str(claims.get('actor_role') or ''),
        'id': str(claims.get('actor_id') or ''),
        'request_id': str(claims.get('request_id') or ''),
        'issuer': str(claims.get('iss') or ''),
        'audience': str(claims.get('aud') or ''),
        'auth_mode': str(claims.get('auth_mode') or ''),
        'origin_actor_role': claims.get('origin_actor_role'),
        'origin_actor_id': claims.get('origin_actor_id'),
    }


def _require_role(actor: dict[str, Any], expected_role: str) -> dict[str, Any]:
    if actor['role'] != expected_role:
        raise _forbidden(f'{expected_role} role required')
    return actor


def _validate_actor_shape(actor: dict[str, Any]) -> dict[str, Any]:
    if actor['role'] not in ALLOWED_ACTOR_ROLES:
        raise _forbidden('unsupported actor role')
    if not actor['id']:
        raise _unauthorized('missing actor id')
    return actor


def _claims_from_headers(
    headers: Mapping[str, str],
    *,
    require_request_id: bool = True,
) -> dict[str, Any]:
    try:
        claims = verify_internal_headers(
            headers,
            audience='internal',
            require_request_id=require_request_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _unauthorized(f'internal auth verification failed: {e}') from e
    return claims


def extract_actor_from_headers(
    headers: Mapping[str, str],
    *,
    require_request_id: bool = True,
) -> dict[str, Any]:
    claims = _claims_from_headers(headers, require_request_id=require_request_id)
    actor = _build_actor(claims)
    return _validate_actor_shape(actor)


async def require_actor(
    request: Request,
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
) -> dict[str, Any]:
    actor = extract_actor_from_headers(request.headers, require_request_id=True)

    if x_request_id and actor['request_id'] and actor['request_id'] != x_request_id:
        raise _unauthorized('request_id mismatch')

    require_signed_for_all = _cfg_bool('internal_auth_require_signed_for_all', False)
    if require_signed_for_all and actor['auth_mode'] != 'signed':
        raise _unauthorized('signed internal auth required')

    return actor


async def require_operator(
    request: Request,
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
) -> dict[str, Any]:
    actor = await require_actor(request, x_request_id=x_request_id)

    require_signed_for_operator = _cfg_bool('internal_auth_require_signed_for_operator', False)
    if require_signed_for_operator and actor['auth_mode'] != 'signed':
        raise _unauthorized('signed internal auth required for operator')

    return _require_role(actor, 'operator')


async def require_service(
    request: Request,
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
) -> dict[str, Any]:
    actor = await require_actor(request, x_request_id=x_request_id)
    actor = _require_role(actor, 'service')

    require_signed_for_service = _cfg_bool('internal_auth_require_signed_for_service', True)
    if require_signed_for_service and actor['auth_mode'] != 'signed':
        raise _unauthorized('signed internal auth required for service')

    allowed_issuers_raw = getattr(settings, 'internal_auth_allowed_issuers', 'backend,worker,mcp-tools')
    allowed_issuers = {
        part.strip()
        for part in str(allowed_issuers_raw).split(',')
        if part.strip()
    }
    if actor['issuer'] and actor['issuer'] not in allowed_issuers and actor['issuer'] != 'legacy-token':
        raise _unauthorized('service issuer not allowed')

    return actor