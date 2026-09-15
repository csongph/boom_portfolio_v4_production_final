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

_gmail.install(_booking)

# Register Gmail's status/test endpoints before booking's compatibility
# endpoints so FastAPI resolves the Gmail versions for identical paths.
_main.app.include_router(_gmail.router)
_main.app.include_router(_booking.router)
_main.app.include_router(_qr.router)
