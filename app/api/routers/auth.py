"""Authentication, registration and current user endpoints."""
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, admin, current_principal
from app.core.enums import ParticipantType, UserRole
from app.core.security import create_access_token, hash_password, verify_password
from app.models.core import Microgrid, Participant, User
from app.schemas.schemas import LoginRequest, RegisterRequest, Token, UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(),
                                 session: AsyncSession = SessionDep):
    user = (await session.execute(select(User).where(User.email == form_data.username))).scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect email or password",
                            headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token(user.id, user.role, user.participant_id)
    return Token(access_token=token, role=user.role, participant_id=user.participant_id)


@router.post("/login", response_model=Token)
async def login_json(req: LoginRequest, session: AsyncSession = SessionDep):
    user = (await session.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect email or password",
                            headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token(user.id, user.role, user.participant_id)
    return Token(access_token=token, role=user.role, participant_id=user.participant_id)


@router.post("/register", response_model=UserResponse)
async def register_participant(req: RegisterRequest, session: AsyncSession = SessionDep):
    mg = await session.get(Microgrid, req.microgrid_id)
    if not mg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "microgrid not found")
    existing = (await session.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    p = Participant(microgrid_id=req.microgrid_id, name=req.name, type=req.participant_type.value)
    session.add(p)
    await session.flush()

    user = User(email=req.email, password_hash=hash_password(req.password),
                role=UserRole.PARTICIPANT.value, participant_id=p.id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
async def get_current_user(principal: Principal = Depends(current_principal),
                           session: AsyncSession = SessionDep):
    user = await session.get(User, uuid.UUID(principal.user_id))
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


@router.get("/users", response_model=List[UserResponse], dependencies=[Depends(admin)])
async def list_users(session: AsyncSession = SessionDep):
    users = (await session.execute(select(User).order_by(User.email))).scalars().all()
    return users
