from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.common.config import settings


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = 'conversations'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    owner_actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Message(Base):
    __tablename__ = 'messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey('conversations.id'), index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Case(Base):
    __tablename__ = 'cases'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    case_type: Mapped[str] = mapped_column(String(64), default='Unknown')
    priority: Mapped[str] = mapped_column(String(32), default='medium')
    sla_deadline: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_ref_masked: Mapped[str] = mapped_column(String(128), default='')
    card_ref_masked: Mapped[str] = mapped_column(String(128), default='')
    operation_ref: Mapped[str] = mapped_column(String(128), default='')
    dispute_reason: Mapped[str] = mapped_column(String(256), default='')
    facts_confirmed_json: Mapped[str] = mapped_column(Text, default='[]')
    facts_pending_json: Mapped[str] = mapped_column(Text, default='[]')
    decision_summary: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(32), default='open')
    summary_public: Mapped[str] = mapped_column(Text, default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CaseTimeline(Base):
    __tablename__ = 'case_timeline'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey('cases.id'), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CaseProfileField(Base):
    __tablename__ = 'case_profile_fields'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey('cases.id'), index=True)
    field_name: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(64))
    confirmed_by: Mapped[str] = mapped_column(String(128))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Document(Base):
    __tablename__ = 'documents'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    storage_key: Mapped[str] = mapped_column(String(512))
    version: Mapped[int] = mapped_column(Integer, default=1)
    doc_code: Mapped[str] = mapped_column(String(64), default='')
    version_label: Mapped[str] = mapped_column(String(32), default='1.0')
    effective_date: Mapped[str] = mapped_column(String(32), default='')
    source_type: Mapped[str] = mapped_column(String(32), default='procedure', index=True)
    source_priority: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class RagChunk(Base):
    __tablename__ = 'rag_chunks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    doc_code: Mapped[str] = mapped_column(String(64), default='', index=True)
    version_label: Mapped[str] = mapped_column(String(32), default='1.0')
    effective_date: Mapped[str] = mapped_column(String(32), default='', index=True)
    source_type: Mapped[str] = mapped_column(String(32), default='procedure', index=True)
    source_priority: Mapped[float] = mapped_column(Float, default=1.0)
    section: Mapped[str] = mapped_column(String(256), default='')
    section_path: Mapped[str] = mapped_column(String(512), default='')
    chunk_type: Mapped[str] = mapped_column(String(32), default='paragraph', index=True)
    risk_tags: Mapped[str] = mapped_column(String(256), default='')
    is_mandatory_step: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.rag_dim))


class AuditEvent(Base):
    __tablename__ = 'audit_events'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))

    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)

    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)

    retrieval_snapshot_json: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    state_before_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    state_after_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    cache_info_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)

    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)