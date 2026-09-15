import hashlib
import html
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from .config import get_settings
from .database import db
from .security import require_admin

router = APIRouter()
s = get_settings()
BKK = ZoneInfo("Asia/Bangkok")
ACTIVE_STATUSES = ("pending", "confirmed")
SERVICES = ("Portrait", "Event", "Fashion", "Graduation", "Studio", "Other")
_rate = defaultdict(deque)


class BookingRequestIn(BaseModel):
    # availability_id remains for backward compatibility with an older cached
    # booking page. New clients send availability_ids and may choose 1–24 slots.
    availability_id: str | None = Field(default=None, min_length=30, max_length=50)
    availability_ids: list[str] = Field(default_factory=list, max_length=24)
    service_type: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    contact_method: str = Field(pattern=r"^(instagram|line|phone|email)$")
    contact_value: str = Field(min_length=2, max_length=180)
    location: str = Field(min_length=2, max_length=300)
    details: str | None = Field(default=None, max_length=3000)
    website: str | None = Field(default=None, max_length=300)


class BookingStatusIn(BaseModel):
    status: str = Field(pattern=r"^(pending|confirmed|rejected|cancelled|completed)$")
    admin_note: str | None = Field(default=None, max_length=1200)


class BookingCancelIn(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)


class DayAvailabilityIn(BaseModel):
    slot_ids: list[str] = Field(default_factory=list, max_length=24)


def _now():
    return datetime.now(BKK)


def _today():
    return _now().date()


def _iso_now():
    return _now().isoformat()


def _safe(value):
    return html.escape(str(value or ""), quote=True)


def _token_hash(token: str):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _rate_limit(request: Request):
    key = request.client.host if request.client else "unknown"
    now = time.time()
    queue = _rate[key]
    while queue and queue[0] < now - 600:
        queue.popleft()
    if len(queue) >= 8:
        raise HTTPException(429, "ส่งคำขอจองบ่อยเกินไป กรุณาลองใหม่ภายหลัง")
    queue.append(now)


def _missing_multi_slot_table(exc: Exception):
    text = str(exc).lower()
    return "booking_slots" in text and any(word in text for word in ("relation", "schema cache", "does not exist", "could not find"))


def _db_ready_error(exc: Exception):
    text = str(exc).lower()
    if _missing_multi_slot_table(exc):
        raise HTTPException(503, "Multi-slot booking database is not ready. Run migration 0003_multi_slot_booking.sql in Supabase.")
    if "booking_" in text or "relation" in text or "schema cache" in text:
        raise HTTPException(503, "Booking database is not ready. Run migration 0002_booking_system.sql in Supabase.")
    raise exc


def _time_text(value):
    value = str(value or "")
    return value[:5] if len(value) >= 5 else value


def _slot_text(slot):
    return slot.get("label") or f"{_time_text(slot.get('start_time'))} – {_time_text(slot.get('end_time'))}"


def _templates(active_only=True):
    try:
        query = db().table("booking_slot_templates").select("*")
        if active_only:
            query = query.eq("is_active", True)
        return query.order("sort_order").order("start_time").execute().data or []
    except Exception as exc:
        _db_ready_error(exc)


def _availability_rows(start: date | None = None, end: date | None = None, open_only=False):
    try:
        query = db().table("booking_availability").select("*")
        if start:
            query = query.gte("work_date", start.isoformat())
        if end:
            query = query.lte("work_date", end.isoformat())
        if open_only:
            query = query.eq("is_open", True)
        return query.order("work_date").execute().data or []
    except Exception as exc:
        _db_ready_error(exc)


def _bookings(active_only=False):
    try:
        query = db().table("bookings").select("*")
        if active_only:
            query = query.in_("status", list(ACTIVE_STATUSES))
        return query.order("created_at", desc=True).execute().data or []
    except Exception as exc:
        _db_ready_error(exc)


def _booking_slot_links(booking_id=None, reserved_only=False, required=False):
    try:
        query = db().table("booking_slots").select("*")
        if booking_id:
            query = query.eq("booking_id", str(booking_id))
        if reserved_only:
            query = query.eq("is_reserved", True)
        return query.order("created_at").execute().data or []
    except Exception as exc:
        if _missing_multi_slot_table(exc):
            if required:
                _db_ready_error(exc)
            return []
        _db_ready_error(exc)


def _active_slot_owners():
    active_bookings = _bookings(True)
    booking_by_id = {str(item["id"]): item for item in active_bookings}
    owners = {}
    links = _booking_slot_links(reserved_only=True, required=False)
    linked_booking_ids = set()
    for link in links:
        booking = booking_by_id.get(str(link.get("booking_id")))
        if not booking:
            continue
        owners[str(link.get("availability_id"))] = booking
        linked_booking_ids.add(str(booking["id"]))
    # Compatibility for bookings created before migration 0003, or while an
    # older backend was still running.
    for booking in active_bookings:
        if str(booking["id"]) not in linked_booking_ids and booking.get("availability_id"):
            owners[str(booking["availability_id"])] = booking
    return owners


def _booking_availability_ids(item):
    if not item:
        return []
    links = _booking_slot_links(item.get("id"), reserved_only=False, required=False)
    ids = [str(row.get("availability_id")) for row in links if row.get("availability_id")]
    if not ids and item.get("availability_id"):
        ids = [str(item["availability_id"])]
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(ids))


def _set_booking_reservations(booking_id, reserved: bool, required=False):
    try:
        rows = _booking_slot_links(booking_id, reserved_only=False, required=required)
        if not rows:
            return False
        db().table("booking_slots").update({"is_reserved": bool(reserved)}).eq("booking_id", str(booking_id)).execute()
        return True
    except HTTPException:
        raise
    except Exception as exc:
        if reserved:
            # A unique-index conflict means another active booking owns at
            # least one of these slots now.
            raise HTTPException(409, "มีบางช่วงเวลาถูกจองไปแล้ว ไม่สามารถเปิด Booking นี้กลับมาได้")
        _db_ready_error(exc)


def _hydrate_booking(item):
    if not item:
        return None
    availability_ids = _booking_availability_ids(item)
    availability_rows = []
    template_rows = []
    try:
        if availability_ids:
            availability_rows = db().table("booking_availability").select("*").in_("id", availability_ids).execute().data or []
        template_ids = list({str(row.get("slot_template_id")) for row in availability_rows if row.get("slot_template_id")})
        if template_ids:
            template_rows = db().table("booking_slot_templates").select("*").in_("id", template_ids).execute().data or []
    except Exception as exc:
        _db_ready_error(exc)

    templates = {str(row["id"]): row for row in template_rows}
    availability_by_id = {str(row["id"]): row for row in availability_rows}
    slots = []
    for availability_id in availability_ids:
        availability = availability_by_id.get(str(availability_id))
        if not availability:
            continue
        slot = templates.get(str(availability.get("slot_template_id"))) or {}
        slots.append({
            "availability_id": str(availability_id),
            "label": _slot_text(slot),
            "start_time": _time_text(slot.get("start_time")),
            "end_time": _time_text(slot.get("end_time")),
            "work_date": str(availability.get("work_date") or ""),
        })
    slots.sort(key=lambda row: (row.get("work_date") or "", row.get("start_time") or ""))
    booking_date = slots[0]["work_date"] if slots else None
    labels = [row["label"] for row in slots]
    time_slot = " + ".join(labels) if labels else "-"
    return {
        **item,
        "availability_ids": [row["availability_id"] for row in slots],
        "booking_date": booking_date,
        "time_slot": time_slot,
        "time_slots": slots,
        "slot_count": len(slots),
        "start_time": slots[0]["start_time"] if slots else "",
        "end_time": slots[-1]["end_time"] if slots else "",
    }


def _public_booking(item):
    hydrated = _hydrate_booking(item)
    if not hydrated:
        return None
    keep = (
        "booking_code", "service_type", "name", "booking_date", "time_slot", "time_slots", "slot_count",
        "location", "details", "status", "admin_note", "created_at", "updated_at",
    )
    return {key: hydrated.get(key) for key in keep}


def _notification_email():
    explicit = os.getenv("BOOKING_NOTIFICATION_EMAIL", "").strip()
    if explicit:
        return explicit
    try:
        rows = db().table("site_settings").select("email").eq("id", 1).limit(1).execute().data or []
        if rows and (rows[0].get("email") or "").strip():
            return rows[0]["email"].strip()
    except Exception:
        pass
    return s.primary_admin_email or ""


def _email_config():
    return {
        "api_key": os.getenv("RESEND_API_KEY", "").strip(),
        "from": os.getenv("RESEND_FROM_EMAIL", "").strip(),
        "to": _notification_email(),
    }


def _log_email(booking_id, recipient, subject, result):
    try:
        db().table("booking_email_logs").insert({
            "booking_id": booking_id,
            "recipient": recipient,
            "subject": subject[:240],
            "status": "sent" if result.get("sent") else "failed",
            "provider_message_id": result.get("id"),
            "error_message": None if result.get("sent") else str(result.get("error") or "Unknown error")[:1500],
        }).execute()
    except Exception:
        pass


async def _send_email(to: str, subject: str, html_body: str, booking_id=None, reply_to: str | None = None):
    cfg = _email_config()
    if not cfg["api_key"]:
        result = {"sent": False, "error": "RESEND_API_KEY is not configured"}
        _log_email(booking_id, to or "", subject, result)
        return result
    if not cfg["from"]:
        result = {"sent": False, "error": "RESEND_FROM_EMAIL is not configured"}
        _log_email(booking_id, to or "", subject, result)
        return result
    if not to:
        result = {"sent": False, "error": "Recipient email is not configured"}
        _log_email(booking_id, "", subject, result)
        return result
    payload = {"from": cfg["from"], "to": [to], "subject": subject, "html": html_body}
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            result = {"sent": False, "error": f"Resend {response.status_code}: {response.text[:500]}"}
        else:
            data = response.json()
            result = {"sent": True, "id": data.get("id")}
    except Exception as exc:
        result = {"sent": False, "error": str(exc)[:500]}
    _log_email(booking_id, to, subject, result)
    return result


def _email_shell(title, intro, body, cta_label=None, cta_url=None, tone="#d7b46a"):
    button = ""
    if cta_label and cta_url:
        button = f'<p style="margin:26px 0"><a href="{_safe(cta_url)}" style="display:inline-block;background:{tone};color:#090909;text-decoration:none;font-weight:800;padding:13px 20px;border-radius:10px">{_safe(cta_label)}</a></p>'
    return f"""
    <div style="margin:0;padding:28px 14px;background:#07090e;font-family:Arial,sans-serif;color:#f8fafc">
      <div style="max-width:620px;margin:auto;background:#11151d;border:1px solid #272d39;border-radius:18px;overflow:hidden">
        <div style="padding:22px 24px;border-bottom:1px solid #272d39">
          <div style="font-size:12px;letter-spacing:2px;color:{tone};font-weight:800">CS PHOTO BY BOOM</div>
          <h1 style="font-size:25px;margin:9px 0 0;color:#fff">{_safe(title)}</h1>
        </div>
        <div style="padding:24px">
          <p style="margin:0 0 20px;color:#cbd5e1;line-height:1.6">{_safe(intro)}</p>
          {body}{button}
        </div>
        <div style="padding:16px 24px;border-top:1px solid #272d39;color:#778195;font-size:11px">Photography · Portrait · Event · Fashion · Bangkok</div>
      </div>
    </div>"""


def _detail_rows(item):
    b = _hydrate_booking(item)
    values = [
        ("Booking ID", b.get("booking_code")),
        ("ลูกค้า", b.get("name")),
        ("ประเภทงาน", b.get("service_type")),
        ("วันที่", b.get("booking_date")),
        ("เวลา", b.get("time_slot")),
        ("สถานที่", b.get("location")),
        ("ติดต่อ", f"{b.get('contact_method')}: {b.get('contact_value')}"),
        ("Email", b.get("email")),
    ]
    rows = "".join(
        f'<tr><td style="padding:8px 0;color:#8f9aab;width:130px;vertical-align:top">{_safe(label)}</td><td style="padding:8px 0;color:#f8fafc;font-weight:700">{_safe(value)}</td></tr>'
        for label, value in values
    )
    note = _safe(b.get("details") or "-")
    return f'<table style="width:100%;border-collapse:collapse">{rows}</table><div style="margin-top:16px;padding:14px;background:#0b0f16;border-radius:10px;color:#cbd5e1;line-height:1.55"><strong style="color:#fff">รายละเอียดงาน</strong><br>{note}</div>'


async def _notify_new_booking(item, manage_token):
    base = s.frontend_public_url.rstrip("/")
    admin_url = f"{base}/admin-booking?booking={item['booking_code']}"
    customer_url = f"{base}/booking?booking={item['booking_code']}&token={manage_token}"
    subject = f"📸 New Booking Request — {item['service_type']} | {_hydrate_booking(item).get('booking_date')}"
    body = _detail_rows(item) + '<p style="margin:18px 0 0;color:#f6c96d;font-weight:800">สถานะ: 🟡 Pending</p>'
    admin_result = await _send_email(
        _notification_email(), subject,
        _email_shell("New Booking Request", "มีลูกค้าส่งคำขอจองคิวใหม่", body, "View Booking", admin_url),
        booking_id=item.get("id"), reply_to=item.get("email"),
    )
    customer_subject = f"📸 Booking Request Received — CS PHOTO BY BOOM | {_hydrate_booking(item).get('booking_date')}"
    customer_body = _detail_rows(item) + '<p style="margin:18px 0 0;color:#f6c96d;font-weight:800">สถานะ: 🟡 Pending — รอ BOOM ตรวจสอบและยืนยันคิว</p>'
    customer_result = await _send_email(
        item["email"], customer_subject,
        _email_shell("รับคำขอจองแล้ว", "ขอบคุณที่ส่งคำขอจองคิว ระบบได้รับข้อมูลเรียบร้อยแล้ว", customer_body, "View Booking", customer_url),
        booking_id=item.get("id"),
    )
    return admin_result, customer_result


async def _notify_customer_status(item, manage_token=None):
    status = item.get("status")
    meta = {
        "confirmed": ("✅ Booking Confirmed — CS PHOTO BY BOOM", "Your booking is confirmed.", "#78d89a", "🟢 Confirmed"),
        "rejected": ("Booking Update — CS PHOTO BY BOOM", "ขณะนี้ยังไม่สามารถรับคิวนี้ได้", "#f0a36f", "🔴 Not available"),
        "cancelled": ("❌ Booking Cancelled — CS PHOTO BY BOOM", "การจองนี้ถูกยกเลิกแล้ว", "#ef8181", "🔴 Cancelled"),
        "completed": ("✅ Booking Completed — CS PHOTO BY BOOM", "ขอบคุณที่ไว้วางใจให้ดูแลงานถ่ายภาพ", "#78d89a", "✅ Completed"),
    }.get(status)
    if not meta:
        return {"sent": False, "error": "No email for this status"}
    title, intro, tone, status_label = meta
    hydrated = _hydrate_booking(item)
    subject = f"{title} | {hydrated.get('booking_date')}"
    body = _detail_rows(item) + f'<p style="margin:18px 0 0;color:{tone};font-weight:800">สถานะ: {_safe(status_label)}</p>'
    if item.get("admin_note"):
        body += f'<div style="margin-top:14px;padding:14px;border:1px solid #303746;border-radius:10px;color:#cbd5e1"><strong style="color:#fff">ข้อความจาก BOOM</strong><br>{_safe(item["admin_note"])}</div>'
    cta_label = None
    cta_url = None
    if manage_token:
        cta_label = "View Booking"
        cta_url = f"{s.frontend_public_url.rstrip('/')}/booking?booking={item['booking_code']}&token={manage_token}"
    return await _send_email(item["email"], subject, _email_shell(title, intro, body, cta_label, cta_url, tone), booking_id=item.get("id"))


async def _notify_admin_cancelled(item, reason):
    hydrated = _hydrate_booking(item)
    admin_url = f"{s.frontend_public_url.rstrip('/')}/admin-booking?booking={item['booking_code']}"
    body = _detail_rows(item)
    if reason:
        body += f'<div style="margin-top:14px;padding:14px;background:#0b0f16;border-radius:10px;color:#cbd5e1"><strong style="color:#fff">เหตุผลที่ยกเลิก</strong><br>{_safe(reason)}</div>'
    body += '<p style="margin:18px 0 0;color:#ef8181;font-weight:800">สถานะ: 🔴 Cancelled</p>'
    return await _send_email(
        _notification_email(),
        f"❌ Booking Cancelled — {item['service_type']} | {hydrated.get('booking_date')}",
        _email_shell("Booking Cancelled", "ลูกค้ายกเลิกการจอง", body, "View Booking", admin_url, "#ef8181"),
        booking_id=item.get("id"), reply_to=item.get("email"),
    )


def _normalize_requested_availability(payload: BookingRequestIn):
    raw_ids = list(payload.availability_ids or [])
    if payload.availability_id:
        raw_ids.insert(0, payload.availability_id)
    raw_ids = list(dict.fromkeys(str(value).strip() for value in raw_ids if str(value).strip()))
    if not raw_ids:
        raise HTTPException(400, "กรุณาเลือกอย่างน้อย 1 ช่วงเวลา")
    if len(raw_ids) > 24:
        raise HTTPException(400, "เลือกช่วงเวลามากเกินไป")
    normalized = []
    for value in raw_ids:
        try:
            normalized.append(str(UUID(value)))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "ช่วงเวลาที่เลือกไม่ถูกต้อง")
    return list(dict.fromkeys(normalized))


@router.get("/api/booking/availability")
def public_availability(days: int = Query(90, ge=1, le=180)):
    start = _today()
    end = start + timedelta(days=days)
    templates = {str(row["id"]): row for row in _templates(True)}
    rows = _availability_rows(start, end, True)
    active = set(_active_slot_owners().keys())
    grouped = defaultdict(list)
    for row in rows:
        if str(row["id"]) in active:
            continue
        slot = templates.get(str(row.get("slot_template_id")))
        if not slot:
            continue
        grouped[str(row["work_date"])].append({
            "availability_id": row["id"],
            "slot_id": slot["id"],
            "label": _slot_text(slot),
            "start_time": _time_text(slot.get("start_time")),
            "end_time": _time_text(slot.get("end_time")),
        })
    for day_slots in grouped.values():
        day_slots.sort(key=lambda x: x["start_time"])
    return {"timezone": "Asia/Bangkok", "days": dict(sorted(grouped.items())), "services": list(SERVICES)}


@router.post("/api/booking", status_code=201)
async def create_booking(payload: BookingRequestIn, request: Request):
    if payload.website:
        return {"message": "received"}
    _rate_limit(request)
    if payload.service_type not in SERVICES:
        raise HTTPException(400, "ประเภทงานไม่ถูกต้อง")

    selected_ids = _normalize_requested_availability(payload)
    try:
        avail_rows = db().table("booking_availability").select("*").in_("id", selected_ids).eq("is_open", True).execute().data or []
    except Exception as exc:
        _db_ready_error(exc)
    by_id = {str(row["id"]): row for row in avail_rows}
    if any(availability_id not in by_id for availability_id in selected_ids):
        raise HTTPException(409, "มีบางช่วงเวลาที่ไม่ได้เปิดรับงานแล้ว กรุณาเลือกใหม่")

    work_dates = {str(by_id[availability_id].get("work_date")) for availability_id in selected_ids}
    if len(work_dates) != 1:
        raise HTTPException(400, "การจองหนึ่งครั้งต้องเลือกช่วงเวลาในวันเดียวกัน")
    try:
        work_date = date.fromisoformat(next(iter(work_dates)))
    except ValueError:
        raise HTTPException(409, "วันที่จองไม่ถูกต้อง")
    if work_date < _today():
        raise HTTPException(409, "คิวนี้หมดเวลาแล้ว")

    active_owners = _active_slot_owners()
    conflicts = [availability_id for availability_id in selected_ids if availability_id in active_owners]
    if conflicts:
        raise HTTPException(409, "มีบางช่วงเวลาถูกจองไปแล้ว กรุณาเลือกเวลาใหม่")

    manage_token = secrets.token_urlsafe(32)
    booking_code = f"BK-{work_date.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    data = {
        # Keep the first slot in the legacy column so older admin code and
        # historical queries remain compatible.
        "booking_code": booking_code,
        "availability_id": selected_ids[0],
        "service_type": payload.service_type,
        "name": payload.name.strip(),
        "email": str(payload.email).lower(),
        "contact_method": payload.contact_method,
        "contact_value": payload.contact_value.strip(),
        "location": payload.location.strip(),
        "details": (payload.details or "").strip() or None,
        "status": "pending",
        "manage_token_hash": _token_hash(manage_token),
    }
    try:
        created = db().table("bookings").insert(data).execute().data or []
    except Exception:
        raise HTTPException(409, "ช่วงเวลานี้เพิ่งถูกจอง กรุณาเลือกเวลาอื่น")
    if not created:
        raise HTTPException(500, "สร้าง Booking ไม่สำเร็จ")
    item = created[0]

    # The unique partial index in migration 0003 is the final protection
    # against two customers reserving any of the same slots simultaneously.
    try:
        reservation_rows = [
            {"booking_id": item["id"], "availability_id": availability_id, "is_reserved": True}
            for availability_id in selected_ids
        ]
        db().table("booking_slots").insert(reservation_rows).execute()
    except Exception as exc:
        try:
            db().table("bookings").delete().eq("id", item["id"]).execute()
        except Exception:
            pass
        if _missing_multi_slot_table(exc):
            if len(selected_ids) > 1:
                _db_ready_error(exc)
            # For one slot, an older database can still use the legacy unique
            # booking column without breaking bookings during migration.
        else:
            raise HTTPException(409, "มีบางช่วงเวลาที่เพิ่งถูกจอง กรุณาเลือกเวลาใหม่")

    # Re-fetch so hydration sees booking_slots immediately.
    refreshed = db().table("bookings").select("*").eq("id", item["id"]).limit(1).execute().data or []
    item = refreshed[0] if refreshed else item
    admin_mail, customer_mail = await _notify_new_booking(item, manage_token)
    return {
        "message": "ส่งคำขอจองเรียบร้อย",
        "booking_id": booking_code,
        "status": "pending",
        "slot_count": len(selected_ids),
        "manage_token": manage_token,
        "notification_sent": bool(admin_mail.get("sent")),
        "customer_email_sent": bool(customer_mail.get("sent")),
    }


@router.get("/api/booking/status/{booking_code}")
def booking_status(booking_code: str, token: str = Query(..., min_length=16, max_length=128)):
    rows = db().table("bookings").select("*").eq("booking_code", booking_code).limit(1).execute().data or []
    if not rows or not secrets.compare_digest(str(rows[0].get("manage_token_hash") or ""), _token_hash(token)):
        raise HTTPException(404, "Booking not found")
    return _public_booking(rows[0])


@router.post("/api/booking/{booking_code}/cancel")
async def customer_cancel(booking_code: str, payload: BookingCancelIn):
    rows = db().table("bookings").select("*").eq("booking_code", booking_code).limit(1).execute().data or []
    if not rows or not secrets.compare_digest(str(rows[0].get("manage_token_hash") or ""), _token_hash(payload.token)):
        raise HTTPException(404, "Booking not found")
    item = rows[0]
    if item.get("status") in {"cancelled", "rejected", "completed"}:
        return _public_booking(item)
    patch = {"status": "cancelled", "cancellation_reason": (payload.reason or "").strip() or None}
    updated = db().table("bookings").update(patch).eq("id", item["id"]).execute().data or []
    item = updated[0] if updated else {**item, **patch}
    _set_booking_reservations(item["id"], False, required=False)
    await _notify_admin_cancelled(item, patch["cancellation_reason"])
    return _public_booking(item)


@router.get("/api/admin/booking/email-status")
async def admin_email_status(admin=Depends(require_admin)):
    cfg = _email_config()
    configured = bool(cfg["api_key"] and cfg["from"] and cfg["to"])
    return {
        "configured": configured,
        "provider": "Resend",
        "from": cfg["from"],
        "notification_email": cfg["to"],
        "production_ready": configured and "onboarding@resend.dev" not in cfg["from"],
    }


@router.post("/api/admin/booking/email-test")
async def admin_email_test(admin=Depends(require_admin)):
    target = _notification_email()
    body = '<p style="color:#cbd5e1;line-height:1.6">ถ้าคุณได้รับอีเมลนี้ แปลว่าระบบแจ้งเตือน Booking ของ CS PHOTO BY BOOM เชื่อมต่อ Resend สำเร็จแล้ว</p><p style="color:#78d89a;font-weight:800">✅ Email notification is working.</p>'
    result = await _send_email(target, "✅ CS PHOTO BY BOOM — Booking email test", _email_shell("Booking Email Test", "ทดสอบระบบแจ้งเตือนอีเมล", body))
    if not result.get("sent"):
        raise HTTPException(502, result.get("error") or "Email test failed")
    return {"ok": True, "email_id": result.get("id"), "sent_to": target}


@router.get("/api/admin/booking/availability")
async def admin_availability(days: int = Query(120, ge=30, le=365), admin=Depends(require_admin)):
    start = _today()
    end = start + timedelta(days=days)
    templates = _templates(False)
    rows = _availability_rows(start, end, False)
    active = _active_slot_owners()
    grouped = defaultdict(dict)
    for row in rows:
        grouped[str(row["work_date"])][str(row["slot_template_id"])] = {
            "availability_id": row["id"],
            "is_open": bool(row.get("is_open")),
            "booking": active.get(str(row["id"])),
        }
    return {"timezone": "Asia/Bangkok", "slot_templates": templates, "days": grouped}


@router.put("/api/admin/booking/availability/{work_date}")
async def admin_save_day(work_date: date, payload: DayAvailabilityIn, admin=Depends(require_admin)):
    if work_date < _today():
        raise HTTPException(400, "ไม่สามารถแก้วันย้อนหลังได้")
    templates = _templates(False)
    valid = {str(row["id"]) for row in templates if row.get("is_active", True)}
    selected = {str(slot_id) for slot_id in payload.slot_ids}
    if not selected.issubset(valid):
        raise HTTPException(400, "มีช่วงเวลาที่ไม่ถูกต้อง")
    existing = db().table("booking_availability").select("*").eq("work_date", work_date.isoformat()).execute().data or []
    by_slot = {str(row["slot_template_id"]): row for row in existing}
    for slot_id in valid:
        row = by_slot.get(slot_id)
        should_open = slot_id in selected
        if row:
            if bool(row.get("is_open")) != should_open:
                db().table("booking_availability").update({"is_open": should_open}).eq("id", row["id"]).execute()
        elif should_open:
            db().table("booking_availability").insert({
                "work_date": work_date.isoformat(),
                "slot_template_id": slot_id,
                "is_open": True,
            }).execute()
    return {"ok": True, "work_date": work_date.isoformat(), "open_slot_ids": sorted(selected)}


@router.get("/api/admin/bookings")
async def admin_bookings(admin=Depends(require_admin)):
    return [_hydrate_booking(item) for item in _bookings(False)]


@router.get("/api/admin/booking/email-logs")
async def admin_email_logs(limit: int = Query(50, ge=1, le=200), admin=Depends(require_admin)):
    try:
        return db().table("booking_email_logs").select("*").order("created_at", desc=True).limit(limit).execute().data or []
    except Exception as exc:
        _db_ready_error(exc)


@router.patch("/api/admin/bookings/{booking_code}")
async def admin_update_booking(booking_code: str, payload: BookingStatusIn, admin=Depends(require_admin)):
    rows = db().table("bookings").select("*").eq("booking_code", booking_code).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "Booking not found")
    item = rows[0]
    previous = item.get("status")
    was_active = previous in ACTIVE_STATUSES
    will_be_active = payload.status in ACTIVE_STATUSES

    # When restoring a cancelled/rejected booking, reserve every slot first.
    # The unique index prevents taking a slot that another booking now owns.
    if will_be_active and not was_active:
        _set_booking_reservations(item["id"], True, required=False)

    patch = {"status": payload.status, "admin_note": (payload.admin_note or "").strip() or None}
    updated = db().table("bookings").update(patch).eq("id", item["id"]).execute().data or []
    item = updated[0] if updated else {**item, **patch}

    if was_active and not will_be_active:
        _set_booking_reservations(item["id"], False, required=False)

    email_result = {"sent": False, "error": None}
    if payload.status != previous:
        email_result = await _notify_customer_status(item)
    return {
        "booking": _hydrate_booking(item),
        "customer_email_sent": bool(email_result.get("sent")),
        "customer_email_error": None if email_result.get("sent") else email_result.get("error"),
    }
