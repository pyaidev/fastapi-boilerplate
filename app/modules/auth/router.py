from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import Config, CurrentIdentity, Session, endpoint_limit
from app.core.errors import AppError
from app.modules.auth import service
from app.modules.auth.schemas import (
    ChangePassword,
    Message,
    Refresh,
    Register,
    ResetConfirm,
    ResetRequest,
    TokenPair,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register", response_model=UserRead, status_code=201, dependencies=[Depends(endpoint_limit("register"))]
)
async def register(data: Register, request: Request, session: Session) -> UserRead:
    return UserRead.model_validate(await service.register(session, data, request.state.request_id))


@router.post("/token", response_model=TokenPair, dependencies=[Depends(endpoint_limit("login"))])
async def token(
    request: Request,
    session: Session,
    settings: Config,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenPair:
    if len(form.password) > 128 or len(form.username) > 320:
        raise AppError(401, "invalid_credentials", "Invalid email or password.")
    return await service.login(
        session,
        form.username,
        form.password,
        request.app.state.dummy_hash,
        settings,
        request.state.request_id,
    )


@router.post("/refresh", response_model=TokenPair, dependencies=[Depends(endpoint_limit("refresh"))])
async def refresh(data: Refresh, request: Request, session: Session, settings: Config) -> TokenPair:
    return await service.rotate(
        session, data.refresh_token.get_secret_value(), settings, request.state.request_id
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, session: Session, identity: CurrentIdentity) -> Response:
    await service.logout(session, identity.session, request.state.request_id)
    return Response(status_code=204)


@router.get("/me", response_model=UserRead)
async def me(identity: CurrentIdentity) -> UserRead:
    return UserRead.model_validate(identity.user)


@router.post(
    "/password-reset",
    response_model=Message,
    status_code=202,
    dependencies=[Depends(endpoint_limit("reset"))],
)
async def reset_request(data: ResetRequest, request: Request, session: Session, settings: Config) -> Message:
    await service.request_reset(session, str(data.email), settings, request.state.request_id)
    return Message(message="If the account exists, a reset email will be sent.")


@router.post(
    "/password-reset/confirm", response_model=Message, dependencies=[Depends(endpoint_limit("reset"))]
)
async def reset_confirm(data: ResetConfirm, request: Request, session: Session) -> Message:
    await service.reset_password(
        session, data.token.get_secret_value(), data.password.get_secret_value(), request.state.request_id
    )
    return Message(message="Password updated. Log in again.")


@router.post("/password", response_model=Message)
async def password_change(
    data: ChangePassword, request: Request, session: Session, identity: CurrentIdentity
) -> Message:
    await service.change_password(
        session,
        identity.user.id,
        data.current_password.get_secret_value(),
        data.new_password.get_secret_value(),
        request.state.request_id,
    )
    return Message(message="Password updated. All sessions have been revoked.")
