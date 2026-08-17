from fastapi import Header, HTTPException
from .config import get_settings
from .database import db

async def require_admin(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Admin login required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        result = db().auth.get_user(token)
        user = result.user
    except Exception:
        raise HTTPException(401, "Invalid or expired session")
    email = (getattr(user, "email", "") or "").lower()
    if email not in get_settings().admin_email_set:
        raise HTTPException(403, "This account is not an admin")
    return {"id": str(user.id), "email": email}
