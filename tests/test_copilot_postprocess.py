from contracts.schemas import AnalyzeFacts, AnalyzeV1, ChannelHint, Intent, Phase, ProfileUpdate, RiskLevel, Severity
from libs.common.copilot_postprocess import repair_draft, repair_explain
from libs.common.llm_stub import draft as stub_draft, explain as stub_explain
from libs.common.state_engine import build_plan


def _analyze() -> AnalyzeV1:
    return AnalyzeV1(
        schema_version='1.0',
        intent=Intent.SuspiciousTransaction,
        phase=Phase.Collect,
        confidence=0.9,
        summary_public='Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.',
        risk_level=RiskLevel.high,
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=None,
            currency=None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint=ChannelHint.online,
            customer_claim='not_mine',
            card_in_possession='unknown',
            delivery_pref=None,
            previous_actions=[],
        ),
        profile_update=ProfileUpdate(
            client_card_context='Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.',
            recurring_issues=['suspicious_transaction'],
            notes_for_case_file='Уточнить детали операции, факт владения картой, признаки компрометации и необходимость блокировки.',
        ),
        missing_fields=['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block'],
        next_questions=[
            'Подтвердите, пожалуйста, что карта сейчас находится у вас на руках (да/нет).',
            'Подтвердите сумму и примерное время операции, чтобы сверить данные.',
            'Подтвердите, пожалуйста, что вы не совершали эту операцию.',
        ],
        tools_suggested=[],
        danger_flags=[
            {
                'type': 'scam_suspected',
                'severity': Severity.high,
                'text': 'Есть признаки мошенничества или социальной инженерии; не запрашивайте коды из SMS/Push и предупредите клиента о риске.',
            }
        ],
        risk_checklist=[
            {'id': 'no_sms_codes', 'severity': Severity.high, 'text': 'Не запрашивать одноразовые коды из SMS/Push.'}
        ],
        analytics_tags=['suspicious_transaction'],
    )


def test_repair_draft_fixes_truncated_phrase():
    an = _analyze()
    raw = stub_draft(an, build_plan(Intent.SuspiciousTransaction), [], [])
    raw = raw.model_copy(
        update={
            'ghost_text': 'Пожалуйста, уточните сумму и примерное время операции, о которой идет речь.\nТакже подтвердите, пожалуйста, что вы не совершали'
        }
    )

    fixed = repair_draft(raw, an)

    assert fixed.ghost_text.endswith('.')
    assert 'что вы не совершали эту операцию' in fixed.ghost_text


def test_repair_draft_fixes_numbered_list_truncation():
    an = _analyze()
    raw = stub_draft(an, build_plan(Intent.SuspiciousTransaction), [], [])
    raw = raw.model_copy(
        update={
            'ghost_text': 'Понимаю ваше беспокойство. Чтобы мы могли проверить информацию по операции, пожалуйста, уточните:\n\n1. Сумму и пример'
        }
    )

    fixed = repair_draft(raw, an)

    assert fixed.ghost_text.endswith('.')
    assert 'что карта у вас на руках' in fixed.ghost_text
    assert 'что вы не совершали эту операцию' in fixed.ghost_text


def test_repair_explain_carries_forward_risk_context():
    an = _analyze()
    raw = stub_explain('create_case', {'case_id': 'case-1'}, build_plan(Intent.SuspiciousTransaction))

    fixed = repair_explain(
        raw,
        state_before={'last_analyze': an.model_dump()},
        tool_name='create_case',
    )

    assert fixed.danger_flags
    assert fixed.risk_checklist
    assert fixed.ghost_text.endswith('.')