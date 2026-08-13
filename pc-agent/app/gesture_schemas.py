from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class CursorDeltaMessage(BaseModel):
    type: Literal["cursor_delta"] = "cursor_delta"
    dx: float
    dy: float


class ClickMessage(BaseModel):
    type: Literal["click"] = "click"


class ScrollMessage(BaseModel):
    type: Literal["scroll"] = "scroll"
    dy: float


class ZoomMessage(BaseModel):
    type: Literal["zoom"] = "zoom"
    delta: float


GestureMessage = Annotated[
    Union[CursorDeltaMessage, ClickMessage, ScrollMessage, ZoomMessage],
    Field(discriminator="type"),
]
GestureMessageAdapter: TypeAdapter[GestureMessage] = TypeAdapter(GestureMessage)
