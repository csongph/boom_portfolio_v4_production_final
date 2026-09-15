import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from fastapi import APIRouter, Depends, HTTPException

from .security import require_admin

router = APIRouter()
_booking = None


def _gmail_config():
    user = os.getenv("GMAIL_SMTP_USER", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    name = os.getenv("GMAIL_FROM_NAME", "CS PHOTO BY BOOM").strip() or "CS PHOTO BY BOOM"
    notify = os.getenv("BOOKING_NOTIFICATION_EMAIL", "").strip() or user
    return {
        # api_key/from keep the old booking status contract compatible.
        "api_key": password,
        "from": formataddr((name, user)) if user else "",
        "to": notify,
        "user": user,
        "app_password": password,
        "from_name": name,
        "host": "smtp.gmail.com",
        "port": 465,
    }


def _smtp_send(cfg, to, subject, html_body, reply_to=None):
    message_id = make_msgid(domain="gmail.com")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["from_name"], cfg["user"]))
    msg["To"] = to
    msg["Message-ID"] = message_id
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(
        "CS PHOTO BY BOOM booking notification. "
        "Please view this message in an HTML-capable email client."
    )
    msg.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=20) as smtp:
        smtp.login(cfg["user"], cfg["app_password"])
        smtp.send_message(msg)
    return message_id


async def _send_email(to, subject, html_body, booking_id=None, reply_to=None):
    cfg = _gmail_config()
    if not cfg["user"]:
        result = {"sent": False, "error": "GMAIL_SMTP_USER is not configured"}
        _booking._log_email(booking_id, to or "", subject, result)
        return result
    if not cfg["app_password"]:
        result = {"sent": False, "error": "GMAIL_APP_PASSWORD is not configured"}
        _booking._log_email(booking_id, to or "", subject, result)
        return result
    if not to:
        result = {"sent": False, "error": "Recipient email is not configured"}
        _booking._log_email(booking_id, "", subject, result)
        return result
    try:
        message_id = await asyncio.to_thread(
            _smtp_send, cfg, to, subject, html_body, reply_to
        )
        result = {"sent": True, "id": message_id}
    except smtplib.SMTPAuthenticationError:
        result = {
            "sent": False,
            "error": (
                "Gmail authentication failed. Use a Google App Password "
                "instead of the normal Gmail password."
            ),
        }
    except Exception as exc:
        result = {"sent": False, "error": f"Gmail SMTP: {str(exc)[:420]}"}
    _booking._log_email(booking_id, to, subject, result)
    return result


def install(booking_module):
    global _booking
    _booking = booking_module

    # Preserve the existing Booking/Supabase implementation and replace only
    # the delivery layer. All existing booking notifications now use Gmail.
    booking_module._email_config = _gmail_config
    booking_module._send_email = _send_email

    original_notification_email = booking_module._notification_email

    def notification_email():
        explicit = os.getenv("BOOKING_NOTIFICATION_EMAIL", "").strip()
        user = os.getenv("GMAIL_SMTP_USER", "").strip()
        return explicit or user or original_notification_email()

    booking_module._notification_email = notification_email


@router.get("/api/admin/booking/email-status")
async def email_status(admin=Depends(require_admin)):
    cfg = _gmail_config()
    configured = bool(cfg["user"] and cfg["app_password"] and cfg["to"])
    return {
        "configured": configured,
        "provider": "Gmail SMTP",
        "from": cfg["from"],
        "notification_email": cfg["to"],
        "production_ready": configured,
        "smtp_host": cfg["host"],
    }


@router.post("/api/admin/booking/email-test")
async def email_test(admin=Depends(require_admin)):
    if _booking is None:
        raise HTTPException(503, "Booking email backend is not initialized")
    cfg = _gmail_config()
    target = cfg["to"]
    body = (
        '<p style="color:#cbd5e1;line-height:1.6">'
        'ถ้าคุณได้รับอีเมลนี้ แปลว่าระบบแจ้งเตือน Booking ของ '
        'CS PHOTO BY BOOM เชื่อมต่อ Gmail SMTP สำเร็จแล้ว</p>'
        '<p style="color:#78d89a;font-weight:800">'
        '✅ Gmail email notification is working.</p>'
    )
    result = await _send_email(
        target,
        "✅ CS PHOTO BY BOOM — Gmail booking email test",
        _booking._email_shell(
            "Booking Email Test",
            "ทดสอบระบบแจ้งเตือนอีเมลผ่าน Gmail",
            body,
        ),
    )
    if not result.get("sent"):
        raise HTTPException(502, result.get("error") or "Email test failed")
    return {"ok": True, "email_id": result.get("id"), "sent_to": target}
