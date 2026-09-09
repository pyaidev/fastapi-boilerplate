import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.dependencies import AdminIdentity, CurrentIdentity, Session
from app.models import AuditEvent, User
from app.modules.auth.schemas import UserRead

router = APIRouter(tags=["Administration"])


@router.get("/admin/users", response_model=list[UserRead])
async def list_users(
    session: Session,
    identity: AdminIdentity,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> list[UserRead]:
    users = (
        await session.scalars(select(User).order_by(User.created_at, User.id).limit(limit).offset(offset))
    ).all()
    return [UserRead.model_validate(user) for user in users]


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action: str
    resource_id: uuid.UUID | None
    request_id: str
    created_at: datetime


@router.get("/audit", response_model=list[AuditRead], tags=["Audit"])
async def audit_events(
    session: Session, identity: CurrentIdentity, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[AuditRead]:
    query = (
        select(AuditEvent)
        .where(AuditEvent.actor_id == identity.user.id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    return [AuditRead.model_validate(event) for event in (await session.scalars(query)).all()]
