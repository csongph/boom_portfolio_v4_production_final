from .main import app
from . import booking
from . import gmail_email

# Swap only the email delivery layer; booking data/API remain in booking.py.
gmail_email.install(booking)

# Gmail status/test routes must be registered before the legacy Resend routes
# so FastAPI resolves the Gmail endpoints first.
app.include_router(gmail_email.router)
app.include_router(booking.router)
