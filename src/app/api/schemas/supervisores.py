from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SupervisorForm(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    phone: str | None = Field(default=None, max_length=40)
    category: str = Field(min_length=1, max_length=240)
    region: str = Field(min_length=1, max_length=180)
    leads_received: int = Field(default=0, ge=0, le=2_147_483_647)
    is_active: bool = False
    minimum_order: str | None = Field(default=None, max_length=240)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "category", "region")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Este campo é obrigatório.")
        return normalized

    @field_validator("phone", "minimum_order")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None
