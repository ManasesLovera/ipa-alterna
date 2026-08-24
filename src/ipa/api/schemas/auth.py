"""Pydantic request/response models for the auth API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Body for email+password login."""

    model_config = ConfigDict(extra="forbid")

    email: str
    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """Body for changing a password."""

    model_config = ConfigDict(extra="forbid")

    old_password: str
    new_password: str = Field(min_length=8)


class ApiKeyCreate(BaseModel):
    """Body for creating an API key."""

    model_config = ConfigDict(extra="forbid")

    name: str
    scopes: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    """Body for creating a user."""

    model_config = ConfigDict(extra="forbid")

    email: str
    full_name: str
    password: str = Field(min_length=8)
    role: str = "viewer"


class ApiKeyResult(BaseModel):
    """An API key creation result; the raw key appears exactly once."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    prefix: str
    scopes: list[str]
    raw_key: str | None = None
