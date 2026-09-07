import json
from datetime import datetime, timezone
import httpx
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import HTTPException
from .config import get_settings
from .database import db

SCOPES="https://www.googleapis.com/auth/drive.readonly"

def _fernet():
    key=get_settings().token_encryption_key
    if not key: raise HTTPException(503,"TOKEN_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode())

def oauth_state(admin_email: str):
    s=get_settings(); return URLSafeTimedSerializer(s.oauth_state_secret).dumps({"email":admin_email})

def decode_state(state: str):
    try: return URLSafeTimedSerializer(get_settings().oauth_state_secret).loads(state,max_age=900)
    except (BadSignature,SignatureExpired): raise HTTPException(400,"Invalid OAuth state")

def google_auth_url(admin_email: str):
    s=get_settings()
    if not s.google_client_id or not s.google_oauth_redirect_uri or not s.oauth_state_secret:
        raise HTTPException(503,"Google OAuth is not configured")
    params={"client_id":s.google_client_id,"redirect_uri":s.google_oauth_redirect_uri,"response_type":"code","scope":SCOPES,"access_type":"offline","prompt":"consent","state":oauth_state(admin_email)}
    return str(httpx.URL("https://accounts.google.com/o/oauth2/v2/auth",params=params))

async def google_exchange(code: str):
    s=get_settings()
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.post("https://oauth2.googleapis.com/token",data={"code":code,"client_id":s.google_client_id,"client_secret":s.google_client_secret,"redirect_uri":s.google_oauth_redirect_uri,"grant_type":"authorization_code"})
    if r.is_error: raise HTTPException(400,"Google OAuth token exchange failed")
    return r.json()

def save_google_token(admin_email: str, token: dict):
    existing=db().table("integration_tokens").select("encrypted_token").eq("provider","google_drive").eq("owner_email",admin_email).limit(1).execute()
    if existing.data and not token.get("refresh_token"):
        try:
            old=json.loads(_fernet().decrypt(existing.data[0]["encrypted_token"].encode()))
            if old.get("refresh_token"): token["refresh_token"]=old["refresh_token"]
        except Exception: pass
    token["saved_at"] = datetime.now(timezone.utc).timestamp()
    enc=_fernet().encrypt(json.dumps(token).encode()).decode()
    db().table("integration_tokens").upsert({"provider":"google_drive","owner_email":admin_email,"encrypted_token":enc,"updated_at":datetime.now(timezone.utc).isoformat()},on_conflict="provider,owner_email").execute()

def load_google_token(admin_email: str):
    r=db().table("integration_tokens").select("encrypted_token").eq("provider","google_drive").eq("owner_email",admin_email).limit(1).execute()
    if not r.data: return None
    try: return json.loads(_fernet().decrypt(r.data[0]["encrypted_token"].encode()))
    except Exception: return None

async def google_access_token(admin_email: str):
    token=load_google_token(admin_email)
    if not token: return None
    expires_in=int(token.get("expires_in") or 3600); saved=float(token.get("saved_at") or 0)
    if token.get("access_token") and datetime.now(timezone.utc).timestamp() < saved + expires_in - 60:
        return token["access_token"]
    refresh=token.get("refresh_token")
    if not refresh: return token.get("access_token")
    s=get_settings()
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.post("https://oauth2.googleapis.com/token",data={"client_id":s.google_client_id,"client_secret":s.google_client_secret,"refresh_token":refresh,"grant_type":"refresh_token"})
    if r.is_error: raise HTTPException(401,"Google Drive connection expired; reconnect Google")
    new=r.json(); new["refresh_token"]=refresh; save_google_token(admin_email,new)
    return new.get("access_token")

async def google_status(admin_email: str):
    r=db().table("integration_tokens").select("id,updated_at").eq("provider","google_drive").eq("owner_email",admin_email).limit(1).execute()
    if not r.data:
        return {"connected":False,"healthy":False,"updated_at":None,"message":"Not connected"}
    try:
        await google_access_token(admin_email)
        return {"connected":True,"healthy":True,"updated_at":r.data[0]["updated_at"],"message":"Connected"}
    except HTTPException as exc:
        return {"connected":False,"healthy":False,"updated_at":r.data[0]["updated_at"],"message":exc.detail,"needs_reconnect":True}

def disconnect_google(admin_email: str):
    db().table("integration_tokens").delete().eq("provider","google_drive").eq("owner_email",admin_email).execute()
