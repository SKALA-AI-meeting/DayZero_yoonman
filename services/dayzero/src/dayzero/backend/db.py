import time
import uuid
from sqlalchemy import create_engine, String, Text, Float, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from dayzero.shared.config import settings


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = 'companies'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String, unique=True)
    password: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)


class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey('companies.id'), index=True)
    alias: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    expires: Mapped[float] = mapped_column(Float)
    consented: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String, default='invited')
    started: Mapped[float | None] = mapped_column(Float, nullable=True)
    ended: Mapped[float | None] = mapped_column(Float, nullable=True)
    binding: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_binding: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    contract: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scenario_version: Mapped[str] = mapped_column(String, default='looplab-v1')
    negotiation_rounds: Mapped[int] = mapped_column(Integer, default=0)
    division_agreed: Mapped[bool] = mapped_column(Boolean, default=False)


class Record(Base):
    __tablename__ = 'records'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    kind: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String)
    at: Mapped[float] = mapped_column(Float, default=time.time)
    elapsed: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSONB)
    operation_id: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint('session_id', 'operation_id'),)


class Part(Base):
    __tablename__ = 'parts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, default='')
    owner: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='todo')
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('session_id', 'number'),)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    key: Mapped[str] = mapped_column(String)
    due: Mapped[float] = mapped_column(Float)
    record_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stored_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint('session_id', 'key'),)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    flow: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str] = mapped_column(String)
    role_version: Mapped[str] = mapped_column(String, default='agents-v1')
    sequence: Mapped[int] = mapped_column(Integer)
    input_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='pending')
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_operations: Mapped[dict] = mapped_column(JSONB, default=dict)
    execution_started: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    lease_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (UniqueConstraint('session_id', 'agent_id', 'sequence'),)


class AgentMemory(Base):
    __tablename__ = 'agent_memories'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class Task(Base):
    __tablename__ = 'tasks'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    title: Mapped[str] = mapped_column(String)
    owner: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='todo')
    part_id: Mapped[str | None] = mapped_column(ForeignKey('parts.id'), nullable=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey('records.id'), nullable=True)
    note: Mapped[str] = mapped_column(Text, default='')
    due_elapsed: Mapped[float | None] = mapped_column(Float, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


engine = create_engine(settings().database_url, pool_pre_ping=True)
DB = sessionmaker(engine, expire_on_commit=False)
