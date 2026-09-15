from .main import app
from .booking import router as booking_router

app.include_router(booking_router)
