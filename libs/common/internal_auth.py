from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Mapping

from fastapi import HTTPException, status

from libs.common.config import settings


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)


def _header_get(headers: Mapping[str, str], key: str) -> str | None:
    wanted = key.lower()
    for k, v in headers.items():
        if k.lower() == wanted:
            return v
    return None


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')


def _b64d(data: str) -> bytes:
    pad = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sign(encoded_claims: str) -> str:
    return hmac.new(
        settings.internal_auth_signing_key.encode('utf-8'),
        encoded_claims.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def issue_internal_claims(
    *,
    actor_role: str,
    actor_id: str,
    request_id: str,
    issuer: str,
    origin_actor_role: str | None = None,
    origin_actor_id: str | None = None,
    ttl_sec: int | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    ttl = int(ttl_sec or settings.internal_auth_ttl_sec)

    payload = {
        'iss': issuer,
        'jti': uuid.uuid4().hex,
        'iat': now,
        'exp': now + ttl,
        'request_id': request_id,
        'actor_role': actor_role,
        'actor_id': actor_id,
    }
    if origin_actor_role:
        payload['origin_actor_role'] = origin_actor_role
    if origin_actor_id:
        payload['origin_actor_id'] = origin_actor_id
    return payload


def sign_internal_claims(payload: dict[str, Any]) -> tuple[str, str]:
    encoded = _b64e(_canonical(payload))
    signature = _sign(encoded)
    return encoded, signature


def build_internal_headers(
    *,
    actor_role: str,
    actor_id: str,
    request_id: str,
    issuer: str,
    origin_actor_role: str | None = None,
    origin_actor_id: str | None = None,
) -> dict[str, str]:
    payload = issue_internal_claims(
        actor_role=actor_role,
        actor_id=actor_id,
        request_id=request_id,
        issuer=issuer,
        origin_actor_role=origin_actor_role,
        origin_actor_id=origin_actor_id,
    )
    encoded, signature = sign_internal_claims(payload)

    headers = {
        'X-Actor-Role': actor_role,
        'X-Actor-Id': actor_id,
        'X-Request-Id': request_id,
        'X-Internal-Claims': encoded,
        'X-Internal-Signature': signature,
    }
    if origin_actor_role:
        headers['X-Origin-Actor-Role'] = origin_actor_role
    if origin_actor_id:
        headers['X-Origin-Actor-Id'] = origin_actor_id
    if settings.internal_auth_allow_legacy_token:
        headers['X-Internal-Auth'] = settings.internal_auth_token
    return headers


def verify_internal_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    encoded = _header_get(headers, 'X-Internal-Claims')
    signature = _header_get(headers, 'X-Internal-Signature')

    if encoded and signature:
        expected = _sign(encoded)
        if not hmac.compare_digest(signature, expected):
            raise _bad('invalid internal signature')

        try:
            claims = json.loads(_b64d(encoded).decode('utf-8'))
        except Exception as e:
            raise _bad(f'invalid claims payload: {e}') from e

        now = int(time.time())
        exp = int(claims.get('exp') or 0)
        iat = int(claims.get('iat') or 0)
        if not exp or exp < now:
            raise _bad('internal claims expired')
        if iat and iat > now + 10:
            raise _bad('internal claims issued in future')

        request_id_header = _header_get(headers, 'X-Request-Id')
        if request_id_header and claims.get('request_id') != request_id_header:
            raise _bad('request_id mismatch')

        actor_role_header = _header_get(headers, 'X-Actor-Role')
        actor_id_header = _header_get(headers, 'X-Actor-Id')
        if actor_role_header and claims.get('actor_role') != actor_role_header:
            raise _bad('actor_role mismatch')
        if actor_id_header and claims.get('actor_id') != actor_id_header:
            raise _bad('actor_id mismatch')

        origin_role_header = _header_get(headers, 'X-Origin-Actor-Role')
        origin_id_header = _header_get(headers, 'X-Origin-Actor-Id')
        if origin_role_header and claims.get('origin_actor_role') != origin_role_header:
            raise _bad('origin_actor_role mismatch')
        if origin_id_header and claims.get('origin_actor_id') != origin_id_header:
            raise _bad('origin_actor_id mismatch')

        if not claims.get('actor_role') or not claims.get('actor_id'):
            raise _bad('missing actor claims')
        return claims

    if settings.internal_auth_allow_legacy_token:
        token = _header_get(headers, 'X-Internal-Auth')
        if token and token == settings.internal_auth_token:
            role = _header_get(headers, 'X-Actor-Role')
            aid = _header_get(headers, 'X-Actor-Id')
            if not role or not aid:
                raise _bad('missing actor headers')
            return {
                'iss': 'legacy-token',
                'request_id': _header_get(headers, 'X-Request-Id') or '',
                'actor_role': role,
                'actor_id': aid,
                'origin_actor_role': _header_get(headers, 'X-Origin-Actor-Role'),
                'origin_actor_id': _header_get(headers, 'X-Origin-Actor-Id'),
            }

    raise _bad('missing internal auth')