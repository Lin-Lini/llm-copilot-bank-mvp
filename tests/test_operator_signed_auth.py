import pytest
from fastapi import HTTPException
from starlette.requests import Request

from libs.common.config import settings
from libs.common.internal_auth import build_internal_headers
from libs.common.security import require_operator


def _make_request(headers: dict[str, str]) -> Request:
    return Request(
        {
            'type': 'http',
            'headers': [(k.lower().encode('utf-8'), v.encode('utf-8')) for k, v in headers.items()],
        }
    )


@pytest.mark.asyncio
async def test_require_operator_accepts_signed_headers_when_required(monkeypatch):
    monkeypatch.setattr(settings, 'internal_auth_require_signed_for_operator', True, raising=False)
    monkeypatch.setattr(settings, 'internal_auth_allow_legacy_token', True, raising=False)

    headers = build_internal_headers(
        actor_role='operator',
        actor_id='op-1',
        request_id='req-signed',
        issuer='backend',
    )

    actor = await require_operator(_make_request(headers), x_request_id='req-signed')
    assert actor['role'] == 'operator'
    assert actor['id'] == 'op-1'
    assert actor['auth_mode'] == 'signed'


@pytest.mark.asyncio
async def test_require_operator_rejects_legacy_headers_when_signed_required(monkeypatch):
    monkeypatch.setattr(settings, 'internal_auth_require_signed_for_operator', True, raising=False)
    monkeypatch.setattr(settings, 'internal_auth_allow_legacy_token', True, raising=False)

    headers = {
        'X-Internal-Auth': settings.internal_auth_token,
        'X-Actor-Role': 'operator',
        'X-Actor-Id': 'op-legacy',
        'X-Request-Id': 'req-legacy',
    }

    with pytest.raises(HTTPException) as exc:
        await require_operator(_make_request(headers), x_request_id='req-legacy')

    assert exc.value.status_code == 401
    assert 'signed internal auth required for operator' in str(exc.value.detail)