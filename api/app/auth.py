"""Password authentication and signed bearer tokens."""

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row

from .conversation_store import db_pool, get_jwt_signing_key


JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))
password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def create_user(email: str, password: str, display_name: str) -> dict[str, Any]:
    normalized = email.strip().lower()
    with db_pool.connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO users (email, display_name, password_hash)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                RETURNING id::text, email, display_name, created_at
                """,
                (normalized, display_name.strip(), password_hasher.hash(password)),
            )
            user = cursor.fetchone()
    if user is None:
        raise ValueError("이미 가입된 이메일입니다.")
    return user


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    with db_pool.connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id::text, email, display_name, password_hash FROM users WHERE email = %s",
                (email.strip().lower(),),
            )
            user = cursor.fetchone()
    if user is None:
        return None
    try:
        password_hasher.verify(user["password_hash"], password)
    except (VerifyMismatchError, InvalidHashError):
        return None
    if password_hasher.check_needs_rehash(user["password_hash"]):
        with db_pool.connection() as connection:
            connection.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (password_hasher.hash(password), UUID(user["id"])),
            )
    user.pop("password_hash", None)
    return user


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)},
        get_jwt_signing_key(),
        algorithm=JWT_ALGORITHM,
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    try:
        payload = jwt.decode(
            credentials.credentials, get_jwt_signing_key(), algorithms=[JWT_ALGORITHM]
        )
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 로그인입니다.") from exc
    with db_pool.connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id::text, email, display_name, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            user = cursor.fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
