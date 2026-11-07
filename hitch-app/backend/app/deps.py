import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Role, User
from app.security import decode_token
from app.services.valhalla import ValhallaClient

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        user_id = uuid.UUID(decode_token(token)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = await db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user


def require_role(role: Role):
    async def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{role.value} role required")
        return user

    return checker


def get_valhalla(request: Request) -> ValhallaClient:
    return request.app.state.valhalla
