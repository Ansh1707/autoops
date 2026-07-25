import jwt
import os
import secrets
import hashlib
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

DEFAULT_DEV_SECRET = "super_secret_jwt_key_for_resume_demo"
PRODUCTION_ENVS = {"prod", "production"}
ROLE_ORDER = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
    "owner": 4,
}

# Mock token for demo/dev use — matches what the frontend sends
MOCK_TOKEN = "valid_mock_token"

security = HTTPBearer()


def _is_production() -> bool:
    return os.getenv("AUTOOPS_ENV", "development").lower() in PRODUCTION_ENVS


def _secret_key() -> str:
    secret = os.getenv("JWT_SECRET_KEY", DEFAULT_DEV_SECRET)
    if _is_production() and secret == DEFAULT_DEV_SECRET:
        raise RuntimeError("JWT_SECRET_KEY must be set to a non-default value in production.")
    return secret


def _algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _allow_mock_token() -> bool:
    default = "false" if _is_production() else "true"
    return os.getenv("AUTOOPS_ALLOW_MOCK_TOKEN", default).lower() == "true"


def create_access_token(data: dict, expires_delta: timedelta = timedelta(hours=24)):
    """Generates a real, cryptographically signed JWT."""
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _secret_key(), algorithm=_algorithm())


def hash_password(password: str, salt: str | None = None) -> str:
    if not password:
        raise ValueError("Password cannot be empty.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return f"pbkdf2_sha256$200000${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iterations, salt, expected = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return secrets.compare_digest(digest, expected)
    except Exception:
        return False


def bootstrap_default_user(db: Session):
    from api.models import User

    if db.query(User).count() > 0:
        return None
    username = os.getenv("AUTOOPS_BOOTSTRAP_USERNAME", "admin")
    password = os.getenv("AUTOOPS_BOOTSTRAP_PASSWORD", "password")
    if _is_production() and password == "password":
        raise RuntimeError("AUTOOPS_BOOTSTRAP_PASSWORD must be set to a non-default value in production.")
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=os.getenv("AUTOOPS_BOOTSTRAP_ROLE", "owner"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str):
    from api.models import User

    bootstrap_default_user(db)
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return user

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Decodes and validates the incoming JWT token.
    Also accepts the mock token for demo/dev environments.
    """
    token = credentials.credentials

    # Allow the plain mock token (used by the frontend in dev/demo mode)
    if token == MOCK_TOKEN and _allow_mock_token():
        return {"sub": "demo_user", "username": "demo_user", "role": "owner"}

    # Otherwise, treat it as a real signed JWT
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[_algorithm()])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def role_allows(actual_role: str | None, required_roles: tuple[str, ...]) -> bool:
    if not required_roles:
        return True
    actual_rank = ROLE_ORDER.get(actual_role or "viewer", ROLE_ORDER["viewer"])
    return any(actual_rank >= ROLE_ORDER[role] for role in required_roles)


def require_roles(*roles: str):
    def dependency(claims: dict = Depends(verify_token)):
        role = claims.get("role")
        if not role_allows(role, roles):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return claims

    return dependency
