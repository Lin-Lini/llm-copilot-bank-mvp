from types import SimpleNamespace
from datetime import datetime, timezone

from contracts.schemas import (
    Intent,
    RequestedAction,
    StatusContext,
)
from apps.backend.app.api.v1.routes.internal import build_case_seed
from contracts.schemas import InternalCreateCaseRequest
from libs.common.case_dossier import build_analyze_from_case_context, build_case_dossier
from libs.common.case_readiness import build_readiness
from libs.common.state_engine import resolve_tools


def _timeline_row(idx: int, kind: str, payload: dict):
    return SimpleNamespace(
        id=idx,
        kind=kind,
        payload=payload,
        payload_json=payload,
        created_at=datetime(2026, 4, 23, 1, idx, 0, tzinfo=timezone.utc),
    )


def test_build_case_seed_uses_analyze_subtype_and_context():
    analyze = build_analyze_from_case_context(
        SimpleNamespace(
            id='case-x',
            case_type=Intent.SuspiciousTransaction.value,
            summary_public='Клиент жалуется на двойное списание.',
            dispute_reason='',
            facts_confirmed_json='[]',
            facts_pending_json='["txn_amount_confirm","txn_datetime_confirm"]',
            decision_summary='',
            status='open',
        ),
        [
            _timeline_row(
                1,
                'case_created',
                {
                    'analyze_snapshot': {
                        'schema_version': '1.0',
                        'intent': 'SuspiciousTransaction',
                        'phase': 'Collect',
                        'confidence': 0.9,
                        'summary_public': 'Клиент жалуется на двойное списание.',
                        'risk_level': 'medium',
                        'facts': {
                            'card_hint': None,
                            'txn_hint': None,
                            'amount': None,
                            'currency': None,
                            'datetime_hint': None,
                            'merchant_hint': None,
                            'channel_hint': 'unknown',
                            'customer_claim': 'unknown',
                            'card_in_possession': 'yes',
                            'delivery_pref': None,
                            'previous_actions': [],
                            'dispute_subtype': 'duplicate_charge',
                            'card_state': 'with_client',
                            'requested_actions': ['investigate_transaction'],
                            'status_context': 'unknown',
                            'compromise_signals': [],
                        },
                        'profile_update': {
                            'client_card_context': '',
                            'recurring_issues': [],
                            'notes_for_case_file': '',
                        },
                        'missing_fields': ['txn_amount_confirm', 'txn_datetime_confirm'],
                        'next_questions': [],
                        'tools_suggested': [],
                        'danger_flags': [],
                        'risk_checklist': [
                            {'id': 'no_cvv', 'severity': 'high', 'text': 'Не запрашивать CVV/CVC.'}
                        ],
                        'analytics_tags': ['duplicate_charge'],
                    }
                },
            )
        ],
    )

    seed = build_case_seed(
        InternalCreateCaseRequest(
            conversation_id='conv-1',
            summary_public='Тестовый кейс',
            intent=Intent.SuspiciousTransaction,
        ),
        analyze,
    )

    assert seed['dispute_reason'] == 'duplicate_charge'
    assert 'dispute_subtype' in seed['facts_confirmed']
    assert seed['priority'] == 'medium'


def test_build_case_dossier_surfaces_domain_context():
    case_obj = SimpleNamespace(
        id='case-1',
        case_type=Intent.LostStolen.value,
        summary_public='Клиент потерял карту и просит помочь.',
        dispute_reason='lost',
        facts_confirmed_json='["card_state","requested_actions"]',
        facts_pending_json='[]',
        priority='high',
        decision_summary='Приоритетная блокировка и последующий перевыпуск.',
        status='open',
    )

    timeline = [
        _timeline_row(
            1,
            'case_created',
            {
                'domain_context': {
                    'dispute_subtype': 'unknown',
                    'card_state': 'lost',
                    'requested_actions': ['block_card', 'reissue_card'],
                    'status_context': 'unknown',
                    'compromise_signals': ['sms_code_shared'],
                }
            },
        ),
        _timeline_row(2, 'tool_result', {'tool': 'block_card', 'result': {'blocked': True}}),
    ]

    analyze = build_analyze_from_case_context(case_obj, timeline)
    tools = resolve_tools(Intent.LostStolen, analyze.phase, missing_fields=[], analyze=analyze)
    readiness = build_readiness(intent=Intent.LostStolen, missing_fields=[], tools=tools, case_status='open', analyze=analyze)
    dossier = build_case_dossier(case_obj, readiness=readiness, timeline_rows=timeline)

    assert dossier.intent == Intent.LostStolen
    assert any('утрат' in fact.lower() or 'краж' in fact.lower() or 'состояние карты' in fact.lower() for fact in dossier.confirmed_facts)
    assert any('sms' in flag.lower() for flag in dossier.risk_summary.danger_flags)
    assert any('блокиров' in action.summary.lower() for action in dossier.actions_taken)
    assert 'Сценарий:' in dossier.operator_safe_context


def test_build_analyze_from_case_context_for_status_case():
    case_obj = SimpleNamespace(
        id='case-status',
        case_type=Intent.StatusWhatNext.value,
        summary_public='Клиент уточняет статус обращения.',
        dispute_reason='',
        facts_confirmed_json='["case_id"]',
        facts_pending_json='[]',
        decision_summary='',
        status='closed',
    )
    timeline = [
        _timeline_row(1, 'case_created', {'domain_context': {'status_context': 'case_known', 'requested_actions': ['get_case_status']}})
    ]

    analyze = build_analyze_from_case_context(case_obj, timeline)
    assert analyze.intent == Intent.StatusWhatNext
    assert analyze.facts.status_context == StatusContext.case_known
    assert RequestedAction.get_case_status in analyze.facts.requested_actions