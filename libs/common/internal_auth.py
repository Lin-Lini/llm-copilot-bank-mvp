from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Iterable, Mapping

from fastapi import HTTPException, status

from libs.common.config import settings


CLAIMS_VERSION = '1.0'


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


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except Exception:
        return default



def _cfg_str(name: str, default: str) -> str:
    try:
        value = getattr(settings, name, default)
    except Exception:
        return default
    if value is None:
        return default
    return str(value)


def _cfg_bool(name: str, default: bool) -> bool:
    try:
        value = getattr(settings, name, default)
    except Exception:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _cfg_csv(name: str, default: Iterable[str]) -> set[str]:
    raw = _cfg_str(name, '')
    if not raw:
        return {str(x).strip() for x in default if str(x).strip()}
    parts = {part.strip() for part in raw.split(',') if part.strip()}
    return parts or {str(x).strip() for x in default if str(x).strip()}


def _internal_audience() -> str:
    return _cfg_str('internal_auth_audience', 'internal')


def _allowed_issuers() -> set[str]:
    return _cfg_csv('internal_auth_allowed_issuers', {'backend', 'worker', 'mcp-tools'})


def _clock_skew_sec() -> int:
    return _cfg_int('internal_auth_clock_skew_sec', 10)


def _require_request_id() -> bool:
    return _cfg_bool('internal_auth_require_request_id', True)


def issue_internal_claims(
    *,
    actor_role: str,
    actor_id: str,
    request_id: str,
    issuer: str,
    audience: str | None = None,
    origin_actor_role: str | None = None,
    origin_actor_id: str | None = None,
    ttl_sec: int | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    ttl = int(ttl_sec or settings.internal_auth_ttl_sec)
    aud = audience or _internal_audience()

    payload = {
        'ver': CLAIMS_VERSION,
        'iss': issuer,
        'aud': aud,
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
    audience: str | None = None,
    origin_actor_role: str | None = None,
    origin_actor_id: str | None = None,
) -> dict[str, str]:
    payload = issue_internal_claims(
        actor_role=actor_role,
        actor_id=actor_id,
        request_id=request_id,
        issuer=issuer,
        audience=audience,
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


def _validate_common_header_binding(claims: dict[str, Any], headers: Mapping[str, str], *, require_request_id: bool) -> None:
    request_id_header = _header_get(headers, 'X-Request-Id')
    claim_request_id = str(claims.get('request_id') or '')

    if require_request_id and not claim_request_id:
        raise _bad('missing request_id in internal claims')
    if request_id_header and claim_request_id and claim_request_id != request_id_header:
        raise _bad('request_id mismatch')
    if require_request_id and not request_id_header:
        raise _bad('missing X-Request-Id header')

    actor_role_header = _header_get(headers, 'X-Actor-Role')
    actor_id_header = _header_get(headers, 'X-Actor-Id')
    if actor_role_header and str(claims.get('actor_role') or '') != actor_role_header:
        raise _bad('actor_role mismatch')
    if actor_id_header and str(claims.get('actor_id') or '') != actor_id_header:
        raise _bad('actor_id mismatch')

    origin_role_header = _header_get(headers, 'X-Origin-Actor-Role')
    origin_id_header = _header_get(headers, 'X-Origin-Actor-Id')
    if origin_role_header and str(claims.get('origin_actor_role') or '') != origin_role_header:
        raise _bad('origin_actor_role mismatch')
    if origin_id_header and str(claims.get('origin_actor_id') or '') != origin_id_header:
        raise _bad('origin_actor_id mismatch')



def _normalize_claims(claims: dict[str, Any], *, auth_mode: str) -> dict[str, Any]:
    return {
        'ver': str(claims.get('ver') or CLAIMS_VERSION),
        'iss': str(claims.get('iss') or ''),
        'aud': str(claims.get('aud') or ''),
        'jti': str(claims.get('jti') or ''),
        'iat': int(claims.get('iat') or 0),
        'exp': int(claims.get('exp') or 0),
        'request_id': str(claims.get('request_id') or ''),
        'actor_role': str(claims.get('actor_role') or ''),
        'actor_id': str(claims.get('actor_id') or ''),
        'origin_actor_role': str(claims.get('origin_actor_role') or '') or None,
        'origin_actor_id': str(claims.get('origin_actor_id') or '') or None,
        'auth_mode': auth_mode,
    }


def verify_internal_headers(
    headers: Mapping[str, str],
    *,
    audience: str | None = None,
    require_request_id: bool | None = None,
) -> dict[str, Any]:
    encoded = _header_get(headers, 'X-Internal-Claims')
    signature = _header_get(headers, 'X-Internal-Signature')

    require_req = _require_request_id() if require_request_id is None else require_request_id
    expected_audience = audience or _internal_audience()
    allowed_issuers = _allowed_issuers()
    skew = _clock_skew_sec()

    if encoded and signature:
        expected = _sign(encoded)
        if not hmac.compare_digest(signature, expected):
            raise _bad('invalid internal signature')

        try:
            claims = json.loads(_b64d(encoded).decode('utf-8'))
        except Exception as e:
            raise _bad(f'invalid claims payload: {e}') from e

        if not isinstance(claims, dict):
            raise _bad('invalid claims payload type')

        iss = str(claims.get('iss') or '')
        aud = str(claims.get('aud') or '')
        if not iss:
            raise _bad('missing issuer in internal claims')
        if iss not in allowed_issuers:
            raise _bad('issuer not allowed')
        if not aud:
            raise _bad('missing audience in internal claims')
        if aud != expected_audience:
            raise _bad('invalid internal audience')

        now = int(time.time())
        exp = int(claims.get('exp') or 0)
        iat = int(claims.get('iat') or 0)
        if not exp:
            raise _bad('missing expiration in internal claims')
        if exp < now - skew:
            raise _bad('internal claims expired')
        if iat and iat > now + skew:
            raise _bad('internal claims issued in future')
        if iat and exp and exp <= iat:
            raise _bad('invalid claims lifetime')

        if not claims.get('actor_role') or not claims.get('actor_id'):
            raise _bad('missing actor claims')

        _validate_common_header_binding(claims, headers, require_request_id=require_req)
        return _normalize_claims(claims, auth_mode='signed')

    if settings.internal_auth_allow_legacy_token:
        token = _header_get(headers, 'X-Internal-Auth')
        if token and token == settings.internal_auth_token:
            role = _header_get(headers, 'X-Actor-Role')
            aid = _header_get(headers, 'X-Actor-Id')
            req_id = _header_get(headers, 'X-Request-Id') or ''
            if not role or not aid:
                raise _bad('missing actor headers')
            if require_req and not req_id:
                raise _bad('missing X-Request-Id header')
            claims = {
                'ver': CLAIMS_VERSION,
                'iss': 'legacy-token',
                'aud': expected_audience,
                'jti': '',
                'iat': 0,
                'exp': 0,
                'request_id': req_id,
                'actor_role': role,
                'actor_id': aid,
                'origin_actor_role': _header_get(headers, 'X-Origin-Actor-Role'),
                'origin_actor_id': _header_get(headers, 'X-Origin-Actor-Id'),
            }
            _validate_common_header_binding(claims, headers, require_request_id=require_req)
            return _normalize_claims(claims, auth_mode='legacy')

    raise _bad('missing internal auth')
