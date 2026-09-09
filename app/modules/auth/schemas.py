import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(Input):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=12, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class Refresh(Input):
    refresh_token: SecretStr = Field(min_length=32, max_length=256)


class ResetRequest(Input):
    email: EmailStr


class ResetConfirm(Input):
    token: SecretStr = Field(min_length=32, max_length=256)
    password: SecretStr = Field(min_length=12, max_length=128)


class ChangePassword(Input):
    current_password: SecretStr = Field(max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    name: str
    role: str
    is_active: bool
    created_at: datetime


class Message(BaseModel):
    message: str
