import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.auth.schemas import Input


class NoteCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)


class NoteUpdate(Input):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=20000)
    archived: bool | None = None
    expected_version: int = Field(ge=1)


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    body: str
    archived: bool
    version: int
    created_at: datetime
    updated_at: datetime


class NotePage(BaseModel):
    items: list[NoteRead]
    next_cursor: str | None
