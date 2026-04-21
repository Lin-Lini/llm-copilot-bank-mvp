import time

import pytest
from fastapi import HTTPException

from libs.common.internal_auth import build_internal_headers, issue_internal_claims, sign_internal_claims, verify_internal_headers


def test_signed_internal_headers_roundtrip():
    headers = build_internal_headers(
        actor_role='operator',
        actor_id='op-1',
        request_id='req-1',
        issuer='backend',
    )
    claims = verify_internal_headers(headers)

    assert claims['actor_role'] == 'operator'
    assert claims['actor_id'] == 'op-1'
    assert claims['request_id'] == 'req-1'
    assert claims['iss'] == 'backend'


def test_signed_internal_headers_reject_expired_claims():
    payload = issue_internal_claims(
        actor_role='service',
        actor_id='mcp-tools',
        request_id='req-expired',
        issuer='mcp-tools',
        ttl_sec=1,
    )
    payload['exp'] = int(time.time()) - 1
    encoded, signature = sign_internal_claims(payload)

    headers = {
        'X-Actor-Role': 'service',
        'X-Actor-Id': 'mcp-tools',
        'X-Request-Id': 'req-expired',
        'X-Internal-Claims': encoded,
        'X-Internal-Signature': signature,
    }

    with pytest.raises(HTTPException) as exc:
        verify_internal_headers(headers)

    assert exc.value.status_code == 401