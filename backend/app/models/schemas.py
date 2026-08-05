from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str


class SessionRequest(BaseModel):
    device_id: str
    client_version: str


class SessionResponse(BaseModel):
    session_id: str
    expires_in_seconds: int


class TranscribeRequest(BaseModel):
    audio_base64: str
    language: str = "en-US"


class TranscribeResponse(BaseModel):
    text: str
    confidence: float


class ContextItem(BaseModel):
    source: str
    label: str
    content: str


class ReasonRequest(BaseModel):
    context: List[ContextItem]
    question: str
    available_tools: List[str] = Field(default_factory=list)


class ProposedToolCall(BaseModel):
    tool_name: str
    arguments: Dict[str, str] = Field(default_factory=dict)
    target: str = ""


class ReasonResponse(BaseModel):
    spoken_answer: str
    proposed_tool_call: Optional[ProposedToolCall] = None
    requires_confirmation: bool = False


class VisionAnalyzeRequest(BaseModel):
    image_base64: str
    question: str


class VisionAnalyzeResponse(BaseModel):
    answer: str
    confidence: float
    detected_text: Optional[str] = None
    uncertainty_note: Optional[str] = None
    suggested_follow_up: Optional[str] = None


class SpeechSynthesizeRequest(BaseModel):
    text: str
    voice_id: str = "en-US-calm-default"


class SpeechSynthesizeResponse(BaseModel):
    audio_base64: str
    format: str = "wav"


class MemoryCreateRequest(BaseModel):
    type: str
    title: str
    original_user_text: str
    normalized_summary: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    coarse_location_name: Optional[str] = None
    extracted_text: Optional[str] = None
    object_labels: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    retention_policy: str = "days365"
    sensitivity: str = "normal"


class MemoryResponse(BaseModel):
    id: str
    type: str
    title: str
    original_user_text: str
    normalized_summary: str
    timestamp: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    coarse_location_name: Optional[str] = None
    extracted_text: Optional[str] = None
    object_labels: List[str]
    tags: List[str]
    retention_policy: str
    sensitivity: str


class MemorySearchResponse(BaseModel):
    results: List[MemoryResponse]


class ToolProposeRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, str] = Field(default_factory=dict)
    target: str = ""
    granted_permissions: List[str] = Field(default_factory=list)


class ToolProposeResponse(BaseModel):
    decision: str  # "allow" | "require_confirmation" | "deny"
    reason: Optional[str] = None
    confirmation_summary: Optional[str] = None
    risk_level: Optional[str] = None


class ToolExecuteRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, str] = Field(default_factory=dict)
    target: str = ""
    confirmed: bool = False


class ToolExecuteResponse(BaseModel):
    success: bool
    output: str


class PCStatusResponse(BaseModel):
    host_name: str
    operating_system: str
    cpu_utilization: float
    memory_utilization: float
    disk_utilization: float
    uptime_seconds: float
