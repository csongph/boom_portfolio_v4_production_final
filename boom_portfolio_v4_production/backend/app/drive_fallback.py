"""Fallback helpers for published Google Drive images.

Public image routes historically used PRIMARY_ADMIN_EMAIL as the OAuth owner.
If the Google connection was created by a different allowed admin account,
published images could fail even though Drive was connected. These wrappers
retry with every stored Google Drive OAuth owner before giving up.
"""
from . import drive as _drive
from .database import db


def _connected_google_owners(preferred=None):
    owners=[]
    if preferred:
        owners.append(str(preferred).strip().lower())
    try:
        rows=(
            db().table("integration_tokens")
            .select("owner_email")
            .eq("provider", "google_drive")
            .execute()
            .data
            or []
        )
        for row in rows:
            value=str(row.get("owner_email") or "").strip().lower()
            if value and value not in owners:
                owners.append(value)
    except Exception:
        pass
    return owners


async def optimized_image_bytes(file_id, admin_email=None, width=1600, quality=84):
    last=None
    owners=_connected_google_owners(admin_email)
    if not owners:
        owners=[admin_email]
    for owner in owners:
        try:
            return await _drive.optimized_image_bytes(
                file_id, owner, width=width, quality=quality
            )
        except Exception as exc:
            last=exc
    if last:
        raise last
    return await _drive.optimized_image_bytes(
        file_id, admin_email, width=width, quality=quality
    )


async def image_exif(file_id, admin_email=None):
    last=None
    owners=_connected_google_owners(admin_email)
    if not owners:
        owners=[admin_email]
    for owner in owners:
        try:
            return await _drive.image_exif(file_id, owner)
        except Exception as exc:
            last=exc
    if last:
        raise last
    return await _drive.image_exif(file_id, admin_email)


async def warm_cache(file_ids, admin_email=None, widths=(64,480,800,1200)):
    owners=_connected_google_owners(admin_email)
    owner=owners[0] if owners else admin_email
    return await _drive.warm_cache(file_ids, owner, widths=widths)
