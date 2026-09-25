from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC (SQLite drops tzinfo) and always returns timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; timestamps must be timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class TokenRow(Base):
    __tablename__ = "tokens"
    __table_args__ = (UniqueConstraint("chain", "token_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(32))
    token_address: Mapped[str] = mapped_column(String(128))
    symbol: Mapped[str] = mapped_column(String(128))
    deployed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    feeds: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    snapshots: Mapped[list["SnapshotRow"]] = relationship(back_populates="token")


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    score: Mapped[float] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(8))
    vetoes: Mapped[list[str]] = mapped_column(JSON)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    components: Mapped[dict[str, float]] = mapped_column(JSON)
    smart_money: Mapped[dict[str, Any]] = mapped_column(JSON)
    bundle_status: Mapped[str] = mapped_column(String(16))
    bundle_supply_pct: Mapped[float] = mapped_column(Float)
    sm_net_selling: Mapped[bool] = mapped_column(Boolean)
    market_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_age_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    token: Mapped[TokenRow] = relationship(back_populates="snapshots")
    clusters: Mapped[list["ClusterRow"]] = relationship(back_populates="snapshot")


class ClusterRow(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), index=True)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    funders: Mapped[list[str]] = mapped_column(JSON)
    similar_size: Mapped[bool] = mapped_column(Boolean)

    snapshot: Mapped[SnapshotRow] = relationship(back_populates="clusters")
    wallets: Mapped[list["ClusterWalletRow"]] = relationship(back_populates="cluster")


class ClusterWalletRow(Base):
    __tablename__ = "cluster_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), index=True)
    wallet: Mapped[str] = mapped_column(String(128))

    cluster: Mapped[ClusterRow] = relationship(back_populates="wallets")


class CreditUsageRow(Base):
    __tablename__ = "credit_usage"

    day: Mapped[date] = mapped_column(primary_key=True)
    credits_used: Mapped[int] = mapped_column(Integer)


class AlertSentRow(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime)
    message: Mapped[str] = mapped_column(String(4096))


class HeartbeatRow(Base):
    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    beat_at: Mapped[datetime] = mapped_column(UTCDateTime)
