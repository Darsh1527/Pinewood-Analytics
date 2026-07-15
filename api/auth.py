"""JWT authentication + server-side role-based authorization.

Why JWT: stateless, standard, and the role/scope claims travel inside the
signed token so the server never trusts anything the client asserts.

Roles:
  corporate_admin      -> all 14 communities
  regional_director    -> only communities in token's `region` claim
  executive_director   -> only the token's `community_id` claim

Authorization is enforced server-side: the allowed community list is computed
here, from the token, against dim_community — never from query parameters.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import duckdb
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pipeline import config

JWT_SECRET = os.environ.get("PINEWOOD_JWT_SECRET", "dev-secret-change-in-production")
JWT_ALG = "HS256"
TOKEN_TTL_DAYS = 30

ROLES = {"corporate_admin", "regional_director", "executive_director"}

_bearer = HTTPBearer(auto_error=False)


def create_token(sub: str, role: str, region: str | None = None,
                 community_id: str | None = None) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    payload = {
        "sub": sub,
        "role": role,
        "region": region,
        "community_id": community_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials)


def allowed_communities(user: dict, con: duckdb.DuckDBPyConnection) -> list[str]:
    """The security kernel: derive the community scope from the signed token."""
    role = user.get("role")
    if role == "corporate_admin":
        rows = con.execute("SELECT community_id FROM gold.dim_community").fetchall()
    elif role == "regional_director":
        rows = con.execute(
            "SELECT community_id FROM gold.dim_community WHERE region = ?",
            [user.get("region")],
        ).fetchall()
    elif role == "executive_director":
        rows = con.execute(
            "SELECT community_id FROM gold.dim_community WHERE community_id = ?",
            [user.get("community_id")],
        ).fetchall()
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown role")
    scope = [r[0] for r in rows]
    if not scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token grants no community access")
    return scope


def enforce_community_param(requested: str | None, scope: list[str]) -> list[str]:
    """If the caller asks for a specific community, it must be inside their scope.
    Asking for something outside scope is a 403, not an empty 200 — we want the
    caller to know they are not authorized rather than silently filtered."""
    if requested is None:
        return scope
    if requested not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Not authorized for community {requested}")
    return [requested]
