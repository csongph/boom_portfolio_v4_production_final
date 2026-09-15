"""Application package bootstrap.

Render may start this project with either ``app.main:app`` or
``app.bootstrap:app`` depending on how the service was created. Register the
extra routers while the package is imported so both start commands expose the
same API.
"""
from importlib import import_module

_main = import_module(".main", __name__)
_booking = import_module(".booking", __name__)
_gmail = import_module(".gmail_email", __name__)
_qr = import_module(".qr", __name__)
_drive_fallback = import_module(".drive_fallback", __name__)

_gmail.install(_booking)

# main.py imports these Drive helpers directly. Replace the bound callables
# with resilient wrappers so published photos keep working when Google OAuth
# belongs to a different allowed admin account than PRIMARY_ADMIN_EMAIL.
_main.optimized_image_bytes = _drive_fallback.optimized_image_bytes
_main.image_exif = _drive_fallback.image_exif
_main.warm_cache = _drive_fallback.warm_cache

# Register Gmail's status/test endpoints before booking's compatibility
# endpoints so FastAPI resolves the Gmail versions for identical paths.
_main.app.include_router(_gmail.router)
_main.app.include_router(_booking.router)
_main.app.include_router(_qr.router)
