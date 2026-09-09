import base64
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import and_, or_, select

from app.api.dependencies import CurrentIdentity, Session
from app.core.errors import AppError
from app.models import Note
from app.modules.auth.service import audit
from app.modules.notes.schemas import NoteCreate, NotePage, NoteRead, NoteUpdate

router = APIRouter(prefix="/notes", tags=["Notes"])


@router.get("", response_model=NotePage)
async def list_notes(
    session: Session,
    identity: CurrentIdentity,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    archived: bool | None = None,
) -> NotePage:
    query = select(Note).where(Note.owner_id == identity.user.id)
    if archived is not None:
        query = query.where(Note.archived == archived)
    if search:
        query = query.where(Note.title.contains(search, autoescape=True))
    if cursor:
        try:
            created, raw_id = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if not isinstance(created, str) or not isinstance(raw_id, str):
                raise ValueError("Invalid cursor types")
            created_at, note_id = datetime.fromisoformat(created), uuid.UUID(raw_id)
            if created_at.tzinfo is None:
                raise ValueError("Missing timezone")
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise AppError(422, "invalid_cursor", "Invalid pagination cursor.") from exc
        query = query.where(
            or_(Note.created_at < created_at, and_(Note.created_at == created_at, Note.id < note_id))
        )
    notes = list(
        (await session.scalars(query.order_by(Note.created_at.desc(), Note.id.desc()).limit(limit + 1))).all()
    )
    has_more = len(notes) > limit
    items = notes[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([last.created_at.isoformat(), str(last.id)]).encode()
        ).decode()
    return NotePage(items=[NoteRead.model_validate(note) for note in items], next_cursor=next_cursor)


@router.post("", response_model=NoteRead, status_code=201)
async def create_note(
    data: NoteCreate, request: Request, session: Session, identity: CurrentIdentity
) -> NoteRead:
    note = Note(owner_id=identity.user.id, **data.model_dump())
    session.add(note)
    await session.flush()
    audit(session, identity.user.id, "note.created", request.state.request_id, note.id)
    await session.commit()
    return NoteRead.model_validate(note)


async def owned_note(session: Session, user_id: uuid.UUID, note_id: uuid.UUID, lock: bool = False) -> Note:
    query = select(Note).where(Note.id == note_id, Note.owner_id == user_id)
    if lock:
        query = query.with_for_update()
    note = await session.scalar(query)
    if note is None:
        raise AppError(404, "not_found", "Note not found.")
    return note


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(note_id: uuid.UUID, session: Session, identity: CurrentIdentity) -> NoteRead:
    return NoteRead.model_validate(await owned_note(session, identity.user.id, note_id))


@router.patch("/{note_id}", response_model=NoteRead)
async def update_note(
    note_id: uuid.UUID, data: NoteUpdate, request: Request, session: Session, identity: CurrentIdentity
) -> NoteRead:
    note = await owned_note(session, identity.user.id, note_id, lock=True)
    if note.version != data.expected_version:
        raise AppError(409, "version_conflict", "This note changed. Reload it before saving.")
    for key, value in data.model_dump(exclude_unset=True, exclude={"expected_version"}).items():
        if value is None:
            raise AppError(422, "invalid_field", f"{key} cannot be null.")
        setattr(note, key, value)
    note.version += 1
    audit(session, identity.user.id, "note.updated", request.state.request_id, note.id)
    await session.commit()
    await session.refresh(note)
    return NoteRead.model_validate(note)


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    note_id: uuid.UUID,
    request: Request,
    session: Session,
    identity: CurrentIdentity,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    note = await owned_note(session, identity.user.id, note_id, lock=True)
    if note.version != expected_version:
        raise AppError(409, "version_conflict", "This note changed. Reload it before deleting.")
    audit(session, identity.user.id, "note.deleted", request.state.request_id, note.id)
    await session.delete(note)
    await session.commit()
    return Response(status_code=204)
