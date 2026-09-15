from io import BytesIO
from urllib.parse import urlparse

import qrcode
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

router = APIRouter()


@router.get("/api/qr")
def qr_code(
    data: str = Query(..., min_length=1, max_length=2048),
    size: int = Query(320, ge=128, le=1024),
):
    """Generate QR codes locally so the frontend does not depend on a third-party QR service."""
    value = data.strip()
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme not in {"http", "https", "mailto", "tel", "line"}:
        raise HTTPException(400, "Unsupported QR URL scheme")

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if image.width != size:
        image = image.resize((size, size))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
