"""Accounts and sessions.

The SvelteKit app calls these from its server and keeps the token in an
httpOnly cookie, so browsers never see it and the API needs no CORS.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, status

from realestate.api.deps import AuthServiceDep, BearerTokenDep, CurrentUserDep
from realestate.application.dto.auth import (
    LoginRequest,
    RegisterRequest,
    SessionRead,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["auth"])

UserAgentHeader = Annotated[str | None, Header(alias="User-Agent")]


@router.post(
    "/register",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
)
async def register(
    body: RegisterRequest, auth: AuthServiceDep, user_agent: UserAgentHeader = None
) -> SessionRead:
    issued = await auth.register(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        user_agent=user_agent,
    )
    return SessionRead.from_issued(issued)


@router.post("/login", response_model=SessionRead, summary="Exchange credentials for a session")
async def login(
    body: LoginRequest, auth: AuthServiceDep, user_agent: UserAgentHeader = None
) -> SessionRead:
    issued = await auth.login(email=body.email, password=body.password, user_agent=user_agent)
    return SessionRead.from_issued(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End the current session")
async def logout(auth: AuthServiceDep, token: BearerTokenDep) -> None:
    await auth.logout(token)


@router.get("/me", response_model=UserRead, summary="The signed-in account")
async def me(user: CurrentUserDep) -> UserRead:
    return UserRead.from_domain(user)
