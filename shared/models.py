from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, ForeignKey, Integer
from pgvector.sqlalchemy import Vector
from shared.config import settings


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = 'conversations'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class RagChunk(Base):
    __tablename__ = 'rag_chunks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    section: Mapped[str] = mapped_column(String(256), default='')
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
