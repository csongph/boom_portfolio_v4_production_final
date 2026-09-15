# Compatibility entrypoint for Render services configured with app.bootstrap:app.
# The app package registers Booking + Gmail routes once in app/__init__.py,
# so this module should only re-export the FastAPI application.
from .main import app
