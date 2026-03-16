from fastapi import HTTPException

from apps.backend.app.core.access import require_task_access


def test_require_task_access_allows_task_owner():
    actor = {"role": "operator", "id": "op-1"}
    meta = {"actor_role": "operator", "actor_id": "op-1"}

    assert require_task_access(actor, meta) == meta


def test_require_task_access_denies_other_operator():
    actor = {"role": "operator", "id": "op-2"}
    meta = {"actor_role": "operator", "actor_id": "op-1"}

    try:
        require_task_access(actor, meta)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403


def test_require_task_access_denies_legacy_task_without_owner_binding():
    actor = {"role": "operator", "id": "op-1"}
    meta = {"conversation_id": "conv-1"}

    try:
        require_task_access(actor, meta)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403
