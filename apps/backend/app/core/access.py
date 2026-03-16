from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.models import Case, Conversation


def require_task_access(actor: dict[str, str], task_meta: dict) -> dict:
    if not task_meta:
        raise _not_found('task')

    task_actor_role = task_meta.get('actor_role')
    task_actor_id = task_meta.get('actor_id')

    if not task_actor_role or not task_actor_id:
        raise _forbidden()

    if task_actor_role != actor.get('role') or task_actor_id != actor.get('id'):
        raise _forbidden()

    return task_meta


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail='access denied')


def _not_found(kind: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f'{kind} not found')


def is_privileged_actor(actor: dict[str, str]) -> bool:
    return actor.get('role') in {'operator', 'service'}


async def require_conversation_access(
    db: AsyncSession,
    actor: dict[str, str],
    conversation_id: str,
) -> Conversation:
    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if not conversation:
        raise _not_found('conversation')

    if is_privileged_actor(actor):
        return conversation

    owner_role = conversation.owner_actor_role
    owner_id = conversation.owner_actor_id
    if owner_role and owner_id:
        if owner_role == actor['role'] and owner_id == actor['id']:
            return conversation
        raise _forbidden()

    # fallback for legacy rows created before owner columns existed
    first_participant = (
        await db.execute(
            select(Conversation.id)
            .where(Conversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if first_participant is None:
        raise _not_found('conversation')
    raise _forbidden()


async def require_case_access(
    db: AsyncSession,
    actor: dict[str, str],
    case_id: str,
) -> Case:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise _not_found('case')

    await require_conversation_access(db, actor, case.conversation_id)
    return case
