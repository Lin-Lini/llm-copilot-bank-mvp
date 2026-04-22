from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from libs.common.case_dossier_store import get_case_dossier_payload, is_terminal_case_status
from libs.common.models import CaseDossierSnapshot


def _timeline_row(idx: int, kind: str, payload: dict):
    return SimpleNamespace(
        id=idx,
        kind=kind,
        payload=payload,
        payload_json=payload,
        created_at=datetime(2026, 4, 21, 1, idx, 0, tzinfo=timezone.utc),
    )


class _ExecResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, record=None):
        self.record = record
        self.commit_calls = 0
        self.refresh_calls = 0
        self.added = []

    async def execute(self, stmt):
        return _ExecResult(self.record)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, CaseDossierSnapshot):
            self.record = obj

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, obj):
        self.refresh_calls += 1


def _closed_case():
    return SimpleNamespace(
        id='case-closed-1',
        case_type='StatusWhatNext',
        summary_public='Клиент запросил финальный статус обращения.',
        dispute_reason='',
        facts_confirmed_json='["case_id"]',
        facts_pending_json='[]',
        priority='medium',
        status='closed',
    )


@pytest.mark.asyncio
async def test_terminal_case_status_helper():
    assert is_terminal_case_status('closed') is True
    assert is_terminal_case_status('resolved') is True
    assert is_terminal_case_status('done') is True
    assert is_terminal_case_status('open') is False
    assert is_terminal_case_status(None) is False


@pytest.mark.asyncio
async def test_terminal_dossier_is_persisted():
    db = _FakeDB()
    case_obj = _closed_case()
    timeline = [
        _timeline_row(1, 'case_created', {'intent': 'StatusWhatNext'}),
        _timeline_row(2, 'case_updated', {'status': True}),
    ]

    payload = await get_case_dossier_payload(db, case_obj, timeline_rows=timeline)

    assert payload['case_id'] == 'case-closed-1'
    assert db.record is not None
    assert db.record.case_id == 'case-closed-1'
    assert db.record.current_status == 'closed'
    assert db.record.built_from_timeline_event_id == 2
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_existing_terminal_snapshot_is_reused_when_not_stale():
    timeline = [
        _timeline_row(1, 'case_created', {'intent': 'StatusWhatNext'}),
        _timeline_row(2, 'case_updated', {'status': True}),
    ]
    existing_payload = {
        'case_id': 'case-closed-1',
        'current_status': 'closed',
        'operator_safe_context': 'cached dossier',
    }
    snapshot = CaseDossierSnapshot(
        case_id='case-closed-1',
        schema_version='1.0',
        current_status='closed',
        built_from_timeline_event_id=2,
        payload_json=existing_payload,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db = _FakeDB(record=snapshot)
    case_obj = _closed_case()

    payload = await get_case_dossier_payload(db, case_obj, timeline_rows=timeline)

    assert payload == existing_payload
    assert db.commit_calls == 0
    assert db.refresh_calls == 0