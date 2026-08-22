"""Pydantic request/response models for the tags API."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ipa.core.enums import FieldType
from ipa.db.dtos import TagFieldCreate as DbTagFieldCreate

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
RESERVED_KEYS = {"fields", "value", "confidence", "evidence", "page"}


class TagCreate(BaseModel):
    """Request body to create a tag, optionally with its fields."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    name: str
    description: str = ""
    auto_approve_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    prompt_template: str | None = None
    llm_model: str | None = None
    classification_hints: str | None = None
    fields: list[TagFieldCreate] | None = None


class TagUpdate(BaseModel):
    """Request body to patch a tag's non-schema attributes."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    auto_approve_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    prompt_template: str | None = None
    llm_model: str | None = None
    classification_hints: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> TagUpdate:
        """Require at least one changeable field.

        Returns:
            The model when valid.

        Raises:
            ValueError: When every field is None.
        """
        if all(
            value is None
            for value in (
                self.name,
                self.description,
                self.auto_approve_threshold,
                self.prompt_template,
                self.llm_model,
                self.classification_hints,
            )
        ):
            raise ValueError("provide at least one field to update")
        return self


class TagFieldCreate(DbTagFieldCreate):
    """Field creation model with schema-level validation."""

    position: int = 0

    @field_validator("key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        """Enforce a legal, non-reserved field key.

        Args:
            value: The candidate key.

        Returns:
            The validated key.

        Raises:
            ValueError: When the key is reserved or malformed.
        """
        if value in RESERVED_KEYS:
            raise ValueError(f"key '{value}' is reserved")
        if not KEY_PATTERN.match(value):
            raise ValueError("key must be lowercase snake_case starting with a letter")
        return value

    @model_validator(mode="after")
    def _type_constraints(self) -> TagFieldCreate:
        """Enforce type-specific constraint requirements.

        Returns:
            The model when valid.

        Raises:
            ValueError: When a type requires a constraint that is missing or
                inconsistent.
        """
        if self.field_type == FieldType.ENUM and not self.enum_values:
            raise ValueError("enum_values is required for ENUM fields")
        if self.field_type == FieldType.ARRAY and not self.item_type:
            raise ValueError("item_type is required for ARRAY fields")
        if self.field_type == FieldType.OBJECT and not self.object_schema:
            raise ValueError("object_schema is required for OBJECT fields")
        if self.regex:
            try:
                re.compile(self.regex)
            except re.error as exc:
                raise ValueError(f"regex does not compile: {exc}") from exc
        if self.min_value is not None and self.max_value is not None:  # noqa: SIM102 - nested form narrows for mypy
            if self.min_value > self.max_value:
                raise ValueError("min_value must be <= max_value")
        if self.min_length is not None and self.max_length is not None:  # noqa: SIM102 - nested form narrows for mypy
            if self.min_length > self.max_length:
                raise ValueError("min_length must be <= max_length")
        return self


class TagFieldUpdate(BaseModel):
    """Request body to patch one field."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    description: str | None = None
    is_required: bool | None = None
    enum_values: list[str] | None = None
    regex: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    min_length: int | None = None
    max_length: int | None = None


class ReorderFields(BaseModel):
    """Request body to reorder a tag's fields."""

    model_config = ConfigDict(extra="forbid")

    field_ids: list[str]


class TagRead(BaseModel):
    """A tag as exposed by the API."""

    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    name: str
    description: str
    version: int
    is_active: bool
    auto_approve_threshold: float | None
    prompt_template: str | None
    llm_model: str | None
    classification_hints: str | None
    fields: list[TagFieldRead]
    created_at: str
    updated_at: str


class TagFieldRead(BaseModel):
    """A field as exposed by the API."""

    model_config = ConfigDict(extra="forbid")

    id: str
    key: str
    label: str
    field_type: FieldType
    description: str | None
    is_required: bool
    position: int
    enum_values: list[str] | None
    regex: str | None
    min_value: float | None
    max_value: float | None
    min_length: int | None
    max_length: int | None
    item_type: str | None
    object_schema: dict[str, Any] | None


class CompiledSchema(BaseModel):
    """The compiled schema for a tag version, plus its prompt and hash."""

    model_config = ConfigDict(extra="forbid")

    json_schema: dict[str, Any]
    schema_hash: str
    prompt: str
    tag_version: int


class TagVersionRead(BaseModel):
    """An immutable tag snapshot as exposed by the API."""

    model_config = ConfigDict(extra="forbid")

    version: int
    snapshot: dict[str, Any]
    created_at: str


class Page(BaseModel):
    """A cursor-paginated result page."""

    model_config = ConfigDict(extra="forbid")

    items: list[Any]
    next_cursor: str | None


TagRead.model_rebuild()
