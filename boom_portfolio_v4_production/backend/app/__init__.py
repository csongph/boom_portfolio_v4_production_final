"""Application package bootstrap.

Render may start with either ``app.main:app`` or ``app.bootstrap:app``.
Only portfolio-related extension routers are registered here.
"""
from importlib import import_module

_main = import_module(".main", __name__)
_qr = import_module(".qr", __name__)
_drive_fallback = import_module(".drive_fallback", __name__)

# Keep published Drive images resilient when the connected OAuth account is
# not the same address as PRIMARY_ADMIN_EMAIL.
_main.optimized_image_bytes = _drive_fallback.optimized_image_bytes
_main.image_exif = _drive_fallback.image_exif
_main.warm_cache = _drive_fallback.warm_cache

_main.app.include_router(_qr.router)
