"""Validated, non-secret execution context supplied by Pipefacil v2."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PipefacilExecution(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    protocolVersion: Literal[2]
    executionId: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    agentId: str = Field(min_length=1)
    dealId: str = Field(min_length=1)
    generation: int = Field(ge=0)
    agentVersion: int = Field(ge=0)
    channelId: str = Field(min_length=1)
    conversationId: str = Field(min_length=1)
    expiresAt: AwareDatetime
