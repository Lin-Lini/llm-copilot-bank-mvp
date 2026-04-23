from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=False)


class Intent(str, Enum):
    BlockCard = 'BlockCard'
    UnblockReissue = 'UnblockReissue'
    LostStolen = 'LostStolen'
    SuspiciousTransaction = 'SuspiciousTransaction'
    CardNotWorking = 'CardNotWorking'
    StatusWhatNext = 'StatusWhatNext'
    Unknown = 'Unknown'


class Phase(str, Enum):
    Collect = 'Collect'
    Act = 'Act'
    Explain = 'Explain'


class RiskLevel(str, Enum):
    low = 'low'
    medium = 'medium'
    high = 'high'


class Severity(str, Enum):
    low = 'low'
    medium = 'medium'
    high = 'high'


class ToolName(str, Enum):
    create_case = 'create_case'
    get_case_status = 'get_case_status'
    get_transactions = 'get_transactions'
    block_card = 'block_card'
    unblock_card = 'unblock_card'
    reissue_card = 'reissue_card'
    get_card_limits = 'get_card_limits'
    set_card_limits = 'set_card_limits'
    toggle_online_payments = 'toggle_online_payments'


class QuickCardKind(str, Enum):
    question = 'question'
    confirmation = 'confirmation'
    instruction = 'instruction'
    status = 'status'


class ChannelHint(str, Enum):
    online = 'online'
    pos = 'pos'
    atm = 'atm'
    unknown = 'unknown'


class DisputeSubtype(str, Enum):
    unknown = 'unknown'
    suspicious = 'suspicious'
    recurring_subscription = 'recurring_subscription'
    duplicate_charge = 'duplicate_charge'
    reversal_pending = 'reversal_pending'
    cash_withdrawal = 'cash_withdrawal'
    card_present = 'card_present'
    merchant_dispute = 'merchant_dispute'


class CardState(str, Enum):
    unknown = 'unknown'
    with_client = 'with_client'
    lost = 'lost'
    stolen = 'stolen'
    blocked = 'blocked'
    damaged = 'damaged'


class RequestedAction(str, Enum):
    block_card = 'block_card'
    unblock_card = 'unblock_card'
    reissue_card = 'reissue_card'
    get_case_status = 'get_case_status'
    investigate_transaction = 'investigate_transaction'


class StatusContext(str, Enum):
    unknown = 'unknown'
    case_known = 'case_known'
    case_unknown = 'case_unknown'
    waiting_review = 'waiting_review'
    resolved = 'resolved'


class CompromiseSignal(str, Enum):
    sms_code_shared = 'sms_code_shared'
    safe_account = 'safe_account'
    remote_access = 'remote_access'
    spoofed_call = 'spoofed_call'
    cvv_shared = 'cvv_shared'


class SourceOut(StrictModel):
    doc_id: str
    title: str
    section: str
    quote: str = Field(max_length=200)
    relevance: float = Field(ge=0.0, le=1.0)


class PlanStep(StrictModel):
    id: str
    title: str
    done: bool


class Plan(StrictModel):
    current_step_id: str
    steps: list[PlanStep]


class FactsPreview(StrictModel):
    card_hint: str | None
    txn_hint: str | None
    amount: float | None
    datetime_hint: str | None
    merchant_hint: str | None


class ToolUI(StrictModel):
    tool: ToolName
    label: str
    enabled: bool
    reason: str


class DangerFlag(StrictModel):
    type: str
    severity: Severity
    text: str


class RiskChecklistItem(StrictModel):
    id: str
    severity: Severity
    text: str


class MissingFieldMeta(StrictModel):
    field_name: str
    label: str
    why_needed: str
    severity: Severity
    blocks_tools: list[ToolName]
    confirmable: bool = True
    suggested_question: str | None = None


class ReadinessStatus(str, Enum):
    needs_info = 'needs_info'
    ready = 'ready'
    in_progress = 'in_progress'
    completed = 'completed'


class ReadinessToolState(StrictModel):
    tool: ToolName
    ready: bool
    reason: str


class CaseReadiness(StrictModel):
    score: int = Field(ge=0, le=100)
    status: ReadinessStatus
    blockers: list[str]
    missing_fields: list[MissingFieldMeta]
    ready_tools: list[ReadinessToolState]
    blocked_tools: list[ReadinessToolState]
    next_action: str

class DossierRiskSummary(StrictModel):
    risk_level: RiskLevel
    danger_flags: list[str]
    security_notes: list[str]


class DossierAction(StrictModel):
    kind: str
    summary: str
    created_at: str


class CaseDossier(StrictModel):
    case_id: str
    intent: Intent
    client_problem_summary: str
    confirmed_facts: list[str]
    pending_facts: list[str]
    risk_summary: DossierRiskSummary
    actions_taken: list[DossierAction]
    current_status: str
    next_expected_step: str
    operator_safe_context: str

class Sidebar(StrictModel):
    phase: Phase
    intent: Intent
    plan: Plan
    facts_preview: FactsPreview
    sources: list[SourceOut]
    tools: list[ToolUI]
    missing_fields_meta: list[MissingFieldMeta]
    readiness: CaseReadiness
    risk_checklist: list[RiskChecklistItem]
    danger_flags: list[DangerFlag]
    operator_notes: str


class QuickCard(StrictModel):
    title: str
    insert_text: str
    kind: QuickCardKind


class FormField(StrictModel):
    key: str
    label: str
    value: Any | None


class FormCard(StrictModel):
    title: str
    fields: list[FormField]


class AnalyzeFacts(StrictModel):
    card_hint: str | None
    txn_hint: str | None
    amount: float | None
    currency: str | None
    datetime_hint: str | None
    merchant_hint: str | None
    channel_hint: ChannelHint
    customer_claim: str
    card_in_possession: str
    delivery_pref: str | None
    previous_actions: list[str]
    dispute_subtype: DisputeSubtype = DisputeSubtype.unknown
    card_state: CardState = CardState.unknown
    requested_actions: list[RequestedAction] = Field(default_factory=list)
    status_context: StatusContext = StatusContext.unknown
    compromise_signals: list[CompromiseSignal] = Field(default_factory=list)


class ProfileUpdate(StrictModel):
    client_card_context: str
    recurring_issues: list[str]
    notes_for_case_file: str


class ToolSuggested(StrictModel):
    tool: ToolName
    reason: str
    params_hint: dict[str, Any] = Field(default_factory=dict)


class AnalyzeV1(StrictModel):
    schema_version: Literal['1.0']
    intent: Intent
    phase: Phase
    confidence: float = Field(ge=0.0, le=1.0)
    summary_public: str
    risk_level: RiskLevel
    facts: AnalyzeFacts
    profile_update: ProfileUpdate
    missing_fields: list[str]
    next_questions: list[str]
    tools_suggested: list[ToolSuggested]
    danger_flags: list[DangerFlag]
    risk_checklist: list[RiskChecklistItem]
    analytics_tags: list[str]


class DraftV1(StrictModel):
    schema_version: Literal['1.0']
    ghost_text: str
    quick_cards: list[QuickCard]
    form_cards: list[FormCard]
    sidebar: Sidebar


class ExplainUpdates(StrictModel):
    phase: Phase
    plan: Plan


class ExplainV1(StrictModel):
    schema_version: Literal['1.0']
    ghost_text: str
    updates: ExplainUpdates
    quick_cards: list[QuickCard]
    result_summary_public: str
    danger_flags: list[DangerFlag]
    risk_checklist: list[RiskChecklistItem]


class SuggestRequest(StrictModel):
    conversation_id: str
    max_messages: int = Field(default=20, ge=1, le=200)


class SuggestCreated(StrictModel):
    task_id: str


class TaskStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    succeeded = 'succeeded'
    failed = 'failed'
    canceled = 'canceled'


class SuggestStatusOut(StrictModel):
    task_id: str
    status: TaskStatus
    error: str | None = None
    result: DraftV1 | None = None


class ExecuteToolRequest(StrictModel):
    conversation_id: str
    tool: ToolName
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str


class ExecuteToolResponse(StrictModel):
    tool: ToolName
    result: dict[str, Any]
    explain: ExplainV1


class StateRequest(StrictModel):
    conversation_id: str


class CopilotState(StrictModel):
    conversation_id: str
    intent: Intent
    phase: Phase
    plan: Plan
    last_analyze: AnalyzeV1 | None = None
    last_draft: DraftV1 | None = None


class ProfileFieldConfirm(StrictModel):
    field_name: str
    value: str


class ProfileConfirmRequest(StrictModel):
    conversation_id: str
    case_id: str | None = None
    fields: list[ProfileFieldConfirm]
    trace_id: str | None = None


class ProfileConfirmResponse(StrictModel):
    stored: int


class InternalCreateCaseRequest(StrictModel):
    conversation_id: str
    summary_public: str
    intent: Intent = Intent.Unknown


class InternalCreateCaseResponse(StrictModel):
    case_id: str
    status: str


class InternalCaseStatusResponse(StrictModel):
    case_id: str
    status: str
    timeline: list[dict[str, Any]]


class ToolExecuteRequest(StrictModel):
    tool: ToolName
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    actor_role: str
    actor_id: str
    trace_id: str


class ToolExecuteResponse(StrictModel):
    tool: ToolName
    result: dict[str, Any]

