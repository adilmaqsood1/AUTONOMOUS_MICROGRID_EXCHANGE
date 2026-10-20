"""Blockchain database models: ParticipantWallets, WalletChallenges, and BlockchainTransactions."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class ParticipantWallet(Base, IdMixin, TimestampMixin):
    __tablename__ = "participant_wallets"

    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    address: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    encrypted_private_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    wallet_type: Mapped[str] = mapped_column(String(20), default="MANAGED_DEV")  # MANAGED_DEV, EXTERNAL
    chain_id: Mapped[int] = mapped_column(Integer, default=31337)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE, SUSPENDED, UNVERIFIED

    # Relationship
    participant = relationship("Participant", backref="wallets")


class WalletChallenge(Base, IdMixin, TimestampMixin):
    __tablename__ = "wallet_challenges"

    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    address: Mapped[str] = mapped_column(String(42), index=True)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    message: Mapped[str] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class BlockchainTransaction(Base, IdMixin, TimestampMixin):
    __tablename__ = "blockchain_transactions"

    business_entity_type: Mapped[str] = mapped_column(String(50), index=True)  # COMMITMENT, SETTLEMENT, RESERVE, CERTIFICATE, REGISTRATION
    business_entity_id: Mapped[str] = mapped_column(String(100), index=True)
    chain_id: Mapped[int] = mapped_column(Integer, default=31337)
    contract_address: Mapped[str] = mapped_column(String(42))
    method: Mapped[str] = mapped_column(String(100))
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True, index=True)
    from_address: Mapped[str] = mapped_column(String(42), index=True)
    nonce: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="CREATED", index=True)  # CREATED, SIGNED, SUBMITTED, CONFIRMED, REVERTED, DROPPED, REPLACED
    revert_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    receipt_data: Mapped[dict | None] = mapped_column(SQLITE_JSON, nullable=True)
