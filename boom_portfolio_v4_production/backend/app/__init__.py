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

# Dynamic /photography/:slug and /projects/:slug pages are rendered by Render,
# not by the static Vercel HTML files. Keep them on the same lightweight UI and
# performance layer as the rest of the public site without duplicating templates.
_original_seo_document = _main.seo_document

def _optimized_seo_document(*, title, description, canonical, image_url, body, schema):
    body = (
        body.replace('href="/work.html"', 'href="/work"')
        .replace('href="/photography.html"', 'href="/photography"')
        .replace('href="/about.html"', 'href="/about"')
        .replace('href="/contact.html"', 'href="/contact"')
    )
    response = _original_seo_document(
        title=title,
        description=description,
        canonical=canonical,
        image_url=image_url,
        body=body,
        schema=schema,
    )
    page = response.body.decode("utf-8")
    page = page.replace(
        '<link rel="stylesheet" href="/static/css/style.css?v=54">',
        '<link rel="stylesheet" href="/static/css/style.css?v=54">'
        '<link rel="stylesheet" href="/static/css/system.css?v=1">'
        '<link rel="stylesheet" href="/static/css/performance.css?v=1">',
    )
    page = page.replace(
        '<script src="/static/js/site.js?v=57"></script>',
        '<script src="/static/js/site.js?v=57" defer></script>'
        '<script src="/static/js/performance.js?v=2" defer></script>'
        '<script src="/static/js/unified.js?v=2" defer></script>',
    )
    return _main.Response(
        page,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": response.headers.get("Cache-Control", "public,max-age=300,stale-while-revalidate=3600")},
    )

_main.seo_document = _optimized_seo_document
_main.app.include_router(_qr.router)
