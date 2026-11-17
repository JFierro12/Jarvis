"""Canonical v2 wire models; exported by scripts/export_contract.py."""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Enrollment(WireModel):
    code: str = Field(min_length=20, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class Invitation(WireModel):
    grants: List[str] = Field(default_factory=list, max_length=32)


class TurnRequest(WireModel):
    conversation_id: str = Field(default="", max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=16000)
    target_device: str = Field(default="desktop", max_length=128)


class SessionCredentials(WireModel):
    contract_version: Literal["2"] = "2"
    access_token: str
    approval_key: str
    session_id: str
    device_id: str
    expires_in_seconds: int


class Capability(WireModel):
    name: str
    description: str
    risk_level: str
    requires_confirmation: bool
    required_permissions: List[str]
    target_device: str


class TurnResponse(WireModel):
    contract_version: Literal["2"] = "2"
    conversation_id: str
    turn_id: str
    spoken_answer: str
    action_id: str = ""
    decision: Literal["answer", "allow", "require_confirmation", "deny"]
    confirmation_summary: str = ""
    provider: str
    model: str
    prompt_version: str
