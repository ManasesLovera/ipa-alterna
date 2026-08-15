"""User and API key models.

`users` holds human accounts that sign in and review documents. `api_keys` is
populated by the auth task (T16); the table exists here so foreign keys and
migrations land once.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from ipa.db.base import Base, TimestampMixin, UuidPkMixin, str_enum
from ipa.db.enums import UserRole


class User(UuidPkMixin, TimestampMixin, Base):
    """A human account able to review documents or administer the platform."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole, "user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class ApiKey(UuidPkMixin, Base):
    """A machine credential; only the SHA-256 of the raw key is ever stored."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
