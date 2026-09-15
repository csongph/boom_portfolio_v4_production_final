import base64
import os
from email.message import EmailMessage
from email.utils import formataddr

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .config import get_settings
from .integrations import google_access_token, google_auth_url, load_google_token
from .security import require_admin

router = APIRouter()
_booking = None
_s = get_settings()
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def _owner_email():
    return (_s.primary_admin_email or "").strip().lower()


def _gmail_config():
    owner = _owner_email()
    name = os.getenv("GMAIL_FROM_NAME", "CS PHOTO BY BOOM").strip() or "CS PHOTO BY BOOM"
    notify = os.getenv("BOOKING_NOTIFICATION_EMAIL", "").strip() or owner
    token = load_google_token(owner) if owner else None
    scopes = set(str((token or {}).get("scope") or "").split())
    has_scope = GMAIL_SEND_SCOPE in scopes
    return {
        # Keep the old booking.py compatibility contract, but delivery is Gmail API.
        "api_key": "oauth" if token and has_scope else "",
        "from": formataddr((name, owner)) if owner else "",
        "to": notify,
        "user": owner,
        "from_name": name,
        "connected": bool(token),
        "has_gmail_send_scope": has_scope,
    }


def _build_raw_message(cfg, to, subject, html_body, reply_to=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["from_name"], cfg["user"]))
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(
        "CS PHOTO BY BOOM booking notification. "
        "Please view this message in an HTML-capable email client."
    )
    msg.add_alternative(html_body, subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")


async def _send_email(to, subject, html_body, booking_id=None, reply_to=None):
    cfg = _gmail_config()
    if not cfg["user"]:
        result = {"sent": False, "error": "PRIMARY_ADMIN_EMAIL is not configured"}
        _booking._log_email(booking_id, to or "", subject, result)
        return result
    if not cfg["connected"]:
        result = {"sent": False, "error": "Google account is not connected. Reconnect Google in CMS Integrations."}
        _booking._log_email(booking_id, to or "", subject, result)
        return result
    if not cfg["has_gmail_send_scope"]:
        result = {
            "sent": False,
            "error": "Google connection does not have Gmail send permission. Reconnect Google and allow Gmail send access.",
        }
        _booking._log_email(booking_id, to or "", subject, result)
        return result
    if not to:
        result = {"sent": False, "error": "Recipient email is not configured"}
        _booking._log_email(booking_id, "", subject, result)
        return result

    try:
        access_token = await google_access_token(cfg["user"])
        if not access_token:
            raise RuntimeError("Google access token is unavailable")
        raw = _build_raw_message(cfg, to, subject, html_body, reply_to)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"raw": raw},
            )
        if response.status_code >= 400:
            detail = response.text[:700]
            if response.status_code in (401, 403):
                detail = (
                    "Google rejected Gmail send permission. Enable Gmail API in the Google Cloud project, "
                    "then reconnect Google in CMS and grant Gmail send access. " + detail
                )
            result = {"sent": False, "error": f"Gmail API {response.status_code}: {detail}"}
        else:
            data = response.json()
            result = {"sent": True, "id": data.get("id") or data.get("threadId")}
    except Exception as exc:
        result = {"sent": False, "error": f"Gmail API: {str(exc)[:600]}"}

    _booking._log_email(booking_id, to, subject, result)
    return result


def install(booking_module):
    global _booking
    _booking = booking_module
    booking_module._email_config = _gmail_config
    booking_module._send_email = _send_email

    original_notification_email = booking_module._notification_email

    def notification_email():
        explicit = os.getenv("BOOKING_NOTIFICATION_EMAIL", "").strip()
        return explicit or _owner_email() or original_notification_email()

    booking_module._notification_email = notification_email


@router.get("/api/admin/booking/email-status")
async def email_status(admin=Depends(require_admin)):
    cfg = _gmail_config()
    configured = bool(cfg["connected"] and cfg["has_gmail_send_scope"] and cfg["to"])
    reconnect_url = None
    try:
        reconnect_url = google_auth_url(admin["email"])
    except Exception:
        pass
    return {
        "configured": configured,
        "provider": "Gmail API",
        "from": cfg["from"],
        "notification_email": cfg["to"],
        "production_ready": configured,
        "google_connected": cfg["connected"],
        "gmail_send_scope": cfg["has_gmail_send_scope"],
        "needs_reconnect": not configured,
        "reconnect_url": reconnect_url,
        "transport": "HTTPS/443",
    }


@router.post("/api/admin/booking/email-test")
async def email_test(admin=Depends(require_admin)):
    if _booking is None:
        raise HTTPException(503, "Booking email backend is not initialized")
    cfg = _gmail_config()
    target = cfg["to"]
    body = (
        '<p style="color:#cbd5e1;line-height:1.6">'
        'ถ้าคุณได้รับอีเมลนี้ แปลว่าระบบ Booking ของ CS PHOTO BY BOOM '
        'ส่งอีเมลผ่าน Gmail API (HTTPS) สำเร็จแล้ว</p>'
        '<p style="color:#78d89a;font-weight:800">✅ Gmail API notification is working.</p>'
    )
    result = await _send_email(
        target,
        "✅ CS PHOTO BY BOOM — Gmail API booking email test",
        _booking._email_shell(
            "Booking Email Test",
            "ทดสอบระบบแจ้งเตือนผ่าน Gmail API",
            body,
        ),
    )
    if not result.get("sent"):
        raise HTTPException(502, result.get("error") or "Email test failed")
    return {"ok": True, "email_id": result.get("id"), "sent_to": target}
