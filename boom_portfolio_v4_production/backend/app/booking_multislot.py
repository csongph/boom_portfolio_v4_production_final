"""Multi-slot booking compatibility layer.

This router is registered before the legacy booking router so customers can
reserve more than one time block in a single booking. Existing booking rows
remain compatible because ``bookings.availability_id`` still stores the first
selected slot, while ``booking_slots`` stores every selected slot.
"""
from datetime import date, timedelta
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from . import booking as legacy
from .database import db
from .security import require_admin

router = APIRouter()
ACTIVE_STATUSES = legacy.ACTIVE_STATUSES
SERVICES = legacy.SERVICES


class MultiBookingRequestIn(BaseModel):
    availability_ids: list[str] = Field(default_factory=list, max_length=24)
    availability_id: str | None = Field(default=None, min_length=30, max_length=50)
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


def _migration_error(exc: Exception | None = None):
    text = str(exc or "").lower()
    if not text or "booking_slots" in text or "relation" in text or "schema cache" in text:
        raise HTTPException(
            503,
            "Multi-slot booking database is not ready. Run migration 0003_multi_slot_booking.sql in Supabase.",
        )
    raise exc


def _unique_ids(payload: MultiBookingRequestIn):
    raw = list(payload.availability_ids or [])
    if payload.availability_id:
        raw.append(payload.availability_id)
    result = []
    seen = set()
    for value in raw:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        if len(value) < 30 or len(value) > 50:
            raise HTTPException(400, "ช่วงเวลาที่เลือกไม่ถูกต้อง")
        seen.add(value)
        result.append(value)
    if not result:
        raise HTTPException(400, "กรุณาเลือกอย่างน้อย 1 ช่วงเวลา")
    if len(result) > 24:
        raise HTTPException(400, "เลือกช่วงเวลามากเกินไป")
    return result


def _slot_links(booking_id: str):
    try:
        return (
            db()
            .table("booking_slots")
            .select("*")
            .eq("booking_id", booking_id)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        # Status/email rendering should not take the whole booking system down
        # while migration 0003 is still propagating. Legacy hydration can still
        # resolve the primary availability_id.
        if legacy._missing_multi_slot_table(exc):
            return []
        _migration_error(exc)


def _reserved_links():
    try:
        return (
            db()
            .table("booking_slots")
            .select("booking_id,availability_id")
            .eq("is_reserved", True)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        if legacy._missing_multi_slot_table(exc):
            return []
        _migration_error(exc)


def _set_reserved(booking_id: str, reserved: bool):
    try:
        rows = (
            db()
            .table("booking_slots")
            .update({"is_reserved": reserved})
            .eq("booking_id", booking_id)
            .execute()
            .data
            or []
        )
        return rows
    except Exception as exc:
        if legacy._missing_multi_slot_table(exc):
            # A pre-migration single-slot booking is already protected by the
            # active bookings table, so releasing/restoring does not need to
            # break admin/customer status flows.
            return []
        if reserved:
            raise HTTPException(409, "มีบางช่วงเวลาถูกจองไปแล้ว กรุณาเลือกเวลาใหม่")
        _migration_error(exc)


def _availability_for_ids(ids: list[str], open_only=False):
    if not ids:
        return []
    try:
        query = db().table("booking_availability").select("*").in_("id", ids)
        if open_only:
            query = query.eq("is_open", True)
        return query.execute().data or []
    except Exception as exc:
        legacy._db_ready_error(exc)


def _templates_map():
    return {str(row["id"]): row for row in legacy._templates(False)}


def _hydrate_booking(item):
    if not item:
        return None
    links = _slot_links(str(item["id"]))
    ids = [str(row["availability_id"]) for row in links]
    if not ids and item.get("availability_id"):
        ids = [str(item["availability_id"])]
    availability = _availability_for_ids(ids, False)
    templates = _templates_map()
    rows = []
    for row in availability:
        slot = templates.get(str(row.get("slot_template_id")))
        if not slot:
            continue
        rows.append(
            {
                "availability_id": row["id"],
                "work_date": str(row.get("work_date") or ""),
                "label": legacy._slot_text(slot),
                "start_time": legacy._time_text(slot.get("start_time")),
                "end_time": legacy._time_text(slot.get("end_time")),
            }
        )
    rows.sort(key=lambda x: (x["work_date"], x["start_time"]))
    booking_date = rows[0]["work_date"] if rows else None
    labels = [row["label"] for row in rows]
    time_slot = "-"
    if rows:
        contiguous = all(rows[i - 1]["end_time"] == rows[i]["start_time"] for i in range(1, len(rows)))
        if len(rows) > 1 and contiguous:
            time_slot = f"{rows[0]['start_time']} – {rows[-1]['end_time']} ({len(rows)} ช่วง)"
        else:
            time_slot = ", ".join(labels)
    return {
        **item,
        "booking_date": booking_date,
        "time_slot": time_slot,
        "time_slots": rows,
        "availability_ids": [row["availability_id"] for row in rows],
        "start_time": rows[0]["start_time"] if rows else "",
        "end_time": rows[-1]["end_time"] if rows else "",
    }


# Existing email/status helpers call legacy._hydrate_booking dynamically.
# Replace it once so emails, admin cards and customer status all show every slot.
legacy._hydrate_booking = _hydrate_booking


@router.get("/api/booking/availability")
def public_availability(days: int = Query(90, ge=1, le=180)):
    start = legacy._today()
    end = start + timedelta(days=days)
    templates = {str(row["id"]): row for row in legacy._templates(True)}
    rows = legacy._availability_rows(start, end, True)

    # This helper gracefully falls back to active legacy bookings when the
    # booking_slots table is not available yet. The public booking page should
    # never become completely unusable just because migration 0003 is pending.
    active = set(legacy._active_slot_owners().keys())

    grouped = {}
    for row in rows:
        if str(row["id"]) in active:
            continue
        slot = templates.get(str(row.get("slot_template_id")))
        if not slot:
            continue
        day = str(row["work_date"])
        grouped.setdefault(day, []).append(
            {
                "availability_id": row["id"],
                "slot_id": slot["id"],
                "label": legacy._slot_text(slot),
                "start_time": legacy._time_text(slot.get("start_time")),
                "end_time": legacy._time_text(slot.get("end_time")),
            }
        )
    for day_slots in grouped.values():
        day_slots.sort(key=lambda x: x["start_time"])
    return {
        "timezone": "Asia/Bangkok",
        "days": dict(sorted(grouped.items())),
        "services": list(SERVICES),
        "multi_slot": True,
    }


@router.post("/api/booking", status_code=201)
async def create_booking(payload: MultiBookingRequestIn, request: Request):
    if payload.website:
        return {"message": "received"}
    legacy._rate_limit(request)
    if payload.service_type not in SERVICES:
        raise HTTPException(400, "ประเภทงานไม่ถูกต้อง")

    ids = _unique_ids(payload)
    availability = _availability_for_ids(ids, True)
    if len(availability) != len(ids):
        raise HTTPException(409, "มีบางช่วงเวลาที่ไม่ได้เปิดรับงานแล้ว กรุณาเลือกเวลาใหม่")

    dates = {str(row.get("work_date") or "") for row in availability}
    if len(dates) != 1:
        raise HTTPException(400, "การจองหนึ่งรายการต้องเลือกช่วงเวลาในวันเดียวกัน")
    try:
        work_date = date.fromisoformat(next(iter(dates)))
    except ValueError:
        raise HTTPException(409, "วันที่จองไม่ถูกต้อง")
    if work_date < legacy._today():
        raise HTTPException(409, "คิวนี้หมดเวลาแล้ว")

    templates = _templates_map()
    availability.sort(
        key=lambda row: legacy._time_text((templates.get(str(row.get("slot_template_id"))) or {}).get("start_time"))
    )
    ids = [str(row["id"]) for row in availability]

    # Use the resilient owner map for the first conflict check. It works both
    # before and after migration 0003.
    active_owners = legacy._active_slot_owners()
    if any(availability_id in active_owners for availability_id in ids):
        raise HTTPException(409, "มีบางช่วงเวลาถูกจองไปแล้ว กรุณาเลือกเวลาใหม่")

    manage_token = secrets.token_urlsafe(32)
    booking_code = f"BK-{work_date.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    data = {
        "booking_code": booking_code,
        "availability_id": ids[0],
        "service_type": payload.service_type,
        "name": payload.name.strip(),
        "email": str(payload.email).lower(),
        "contact_method": payload.contact_method,
        "contact_value": payload.contact_value.strip(),
        "location": payload.location.strip(),
        "details": (payload.details or "").strip() or None,
        "status": "pending",
        "manage_token_hash": legacy._token_hash(manage_token),
    }
    try:
        created = db().table("bookings").insert(data).execute().data or []
    except Exception:
        raise HTTPException(409, "ช่วงเวลานี้เพิ่งถูกจอง กรุณาเลือกเวลาใหม่")
    if not created:
        raise HTTPException(500, "สร้าง Booking ไม่สำเร็จ")
    item = created[0]

    try:
        db().table("booking_slots").insert(
            [
                {"booking_id": item["id"], "availability_id": availability_id, "is_reserved": True}
                for availability_id in ids
            ]
        ).execute()
    except Exception as exc:
        if legacy._missing_multi_slot_table(exc) and len(ids) == 1:
            # Keep one-slot bookings working while migration 0003 is pending.
            # The legacy availability_id remains protected by the active
            # booking row and _active_slot_owners().
            pass
        else:
            try:
                db().table("booking_slots").delete().eq("booking_id", item["id"]).execute()
            except Exception:
                pass
            try:
                db().table("bookings").delete().eq("id", item["id"]).execute()
            except Exception:
                pass
            if legacy._missing_multi_slot_table(exc):
                _migration_error(exc)
            raise HTTPException(409, "มีบางช่วงเวลาถูกจองไปแล้ว กรุณาเลือกเวลาใหม่")

    admin_mail, customer_mail = await legacy._notify_new_booking(item, manage_token)
    return {
        "message": "ส่งคำขอจองเรียบร้อย",
        "booking_id": booking_code,
        "status": "pending",
        "manage_token": manage_token,
        "selected_slots": len(ids),
        "notification_sent": bool(admin_mail.get("sent")),
        "customer_email_sent": bool(customer_mail.get("sent")),
    }


@router.get("/api/booking/status/{booking_code}")
def booking_status(booking_code: str, token: str = Query(..., min_length=16, max_length=128)):
    rows = db().table("bookings").select("*").eq("booking_code", booking_code).limit(1).execute().data or []
    if not rows or not secrets.compare_digest(str(rows[0].get("manage_token_hash") or ""), legacy._token_hash(token)):
        raise HTTPException(404, "Booking not found")
    return legacy._public_booking(rows[0])


@router.post("/api/booking/{booking_code}/cancel")
async def customer_cancel(booking_code: str, payload: BookingCancelIn):
    rows = db().table("bookings").select("*").eq("booking_code", booking_code).limit(1).execute().data or []
    if not rows or not secrets.compare_digest(str(rows[0].get("manage_token_hash") or ""), legacy._token_hash(payload.token)):
        raise HTTPException(404, "Booking not found")
    item = rows[0]
    if item.get("status") in {"cancelled", "rejected", "completed"}:
        return legacy._public_booking(item)
    patch = {"status": "cancelled", "cancellation_reason": (payload.reason or "").strip() or None}
    updated = db().table("bookings").update(patch).eq("id", item["id"]).execute().data or []
    item = updated[0] if updated else {**item, **patch}
    _set_reserved(str(item["id"]), False)
    await legacy._notify_admin_cancelled(item, patch["cancellation_reason"])
    return legacy._public_booking(item)


@router.get("/api/admin/booking/availability")
async def admin_availability(days: int = Query(120, ge=30, le=365), admin=Depends(require_admin)):
    start = legacy._today()
    end = start + timedelta(days=days)
    templates = legacy._templates(False)
    rows = legacy._availability_rows(start, end, False)

    # Same resilient mapping as the public page; admin availability should
    # remain usable while migration 0003 is being applied.
    slot_to_booking = legacy._active_slot_owners()

    grouped = {}
    for row in rows:
        grouped.setdefault(str(row["work_date"]), {})[str(row["slot_template_id"])] = {
            "availability_id": row["id"],
            "is_open": bool(row.get("is_open")),
            "booking": slot_to_booking.get(str(row["id"])),
        }
    return {"timezone": "Asia/Bangkok", "slot_templates": templates, "days": grouped, "multi_slot": True}


@router.get("/api/admin/bookings")
async def admin_bookings(admin=Depends(require_admin)):
    return [_hydrate_booking(item) for item in legacy._bookings(False)]


@router.patch("/api/admin/bookings/{booking_code}")
async def admin_update_booking(booking_code: str, payload: BookingStatusIn, admin=Depends(require_admin)):
    rows = db().table("bookings").select("*").eq("booking_code", booking_code).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "Booking not found")
    item = rows[0]
    previous = item.get("status")
    was_active = previous in ACTIVE_STATUSES
    will_be_active = payload.status in ACTIVE_STATUSES

    if will_be_active and not was_active:
        _set_reserved(str(item["id"]), True)

    patch = {"status": payload.status, "admin_note": (payload.admin_note or "").strip() or None}
    try:
        updated = db().table("bookings").update(patch).eq("id", item["id"]).execute().data or []
    except Exception:
        if will_be_active and not was_active:
            _set_reserved(str(item["id"]), False)
        raise
    item = updated[0] if updated else {**item, **patch}

    if was_active and not will_be_active:
        _set_reserved(str(item["id"]), False)

    email_result = {"sent": False, "error": None}
    if payload.status != previous:
        email_result = await legacy._notify_customer_status(item)
    return {
        "booking": _hydrate_booking(item),
        "customer_email_sent": bool(email_result.get("sent")),
        "customer_email_error": None if email_result.get("sent") else email_result.get("error"),
    }
