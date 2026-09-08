import copy, hashlib, html, json, re, time
from collections import defaultdict, deque
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import Response, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
try:from redis import Redis
except ImportError:Redis=None
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from .database import db
from .security import require_admin
from .drive import folder_id_from, list_images, image_bytes, optimized_image_bytes, image_exif, folder_name, warm_cache
from .schemas import DriveImport, AlbumIn, AlbumUpdate, PhotoOrderIn, PhotoMetadataIn, ProjectIn, GitHubImportIn, SettingsIn, ContactIn, AIContentIn, VisitorIn, PhotoEventIn, AlbumViewIn
from .config import get_settings
from .github_import import analyze_repo
from .integrations import google_auth_url, decode_state, google_exchange, save_google_token, google_status, disconnect_google
from .instagram import instagram_media

app=FastAPI(title="BOOM Portfolio API",version="4.0.0",docs_url="/api/docs",openapi_url="/api/openapi.json")
s=get_settings()
app.add_middleware(CORSMiddleware,allow_origins=s.cors_origin_list,allow_credentials=False,allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],allow_headers=["Content-Type","Authorization"])

_rate=defaultdict(deque)
_public_cache={}
_PUBLIC_CACHE_TTL=300
_redis_client=None
_redis_checked=False

def shared_cache():
    global _redis_client,_redis_checked
    if _redis_checked:return _redis_client
    _redis_checked=True
    if Redis and s.redis_url:
        try:
            client=Redis.from_url(s.redis_url,decode_responses=True,socket_connect_timeout=1,socket_timeout=1)
            client.ping();_redis_client=client
        except Exception:_redis_client=None
    return _redis_client

def retry_query(fn, attempts=3):
    last_error=None
    for attempt in range(attempts):
        try:return fn()
        except Exception as exc:
            last_error=exc
            if attempt+1<attempts:time.sleep(.25*(2**attempt))
    raise last_error

def public_cache_get(key, allow_stale=False):
    redis=shared_cache();redis_key="boom:public:"+hashlib.sha256(repr(key).encode()).hexdigest()
    if redis:
        try:
            remote=json.loads(redis.get(redis_key) or "null")
            if remote and (allow_stale or time.time()-remote["created"]<=_PUBLIC_CACHE_TTL):return remote["value"]
        except Exception:pass
    item=_public_cache.get(key)
    if not item:return None
    created,value=item
    if not allow_stale and time.time()-created>_PUBLIC_CACHE_TTL:return None
    return copy.deepcopy(value)

def public_cache_set(key,value):
    _public_cache[key]=(time.time(),copy.deepcopy(value))
    redis=shared_cache()
    if redis:
        try:
            redis_key="boom:public:"+hashlib.sha256(repr(key).encode()).hexdigest()
            redis.setex(redis_key,21600,json.dumps({"created":time.time(),"value":value},default=str))
        except Exception:pass
    return value

def invalidate_public_cache():
    _public_cache.clear();redis=shared_cache()
    if redis:
        try:
            keys=list(redis.scan_iter(match="boom:public:*",count=100))
            if keys:redis.delete(*keys)
        except Exception:pass
def nowiso(): return datetime.now(timezone.utc).isoformat()
def throttle(key: str, limit: int, seconds: int):
    redis=shared_cache()
    if redis:
        try:
            redis_key="boom:rate:"+hashlib.sha256(key.encode()).hexdigest();count=redis.incr(redis_key)
            if count==1:redis.expire(redis_key,seconds)
            if count>limit:raise HTTPException(429,"Too many requests")
            return
        except HTTPException:raise
        except Exception:pass
    now=time.time(); dq=_rate[key]
    while dq and dq[0] < now-seconds:dq.popleft()
    if len(dq)>=limit: raise HTTPException(429,"Too many requests")
    dq.append(now)
def clean_visitor(v: str) -> str:
    v=(v or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{12,96}",v): raise HTTPException(400,"Invalid visitor id")
    return v
def safe_filename(v: str) -> str:
    v=re.sub(r"[^A-Za-z0-9ก-๙._-]+","-",v or "").strip("-._")
    return v[:80] or "Photography"
def slugify(v):
    v=re.sub(r"[^a-z0-9ก-๙]+","-",(v or "").strip().lower()).strip("-")
    if not v: raise HTTPException(400,"slug ไม่ถูกต้อง")
    return v

def unique_slug(table: str, value: str, current_id: str | None = None):
    base=slugify(value); slug=base; i=2
    while True:
        q=db().table(table).select("id").eq("slug",slug).limit(1)
        rows=q.execute().data or []
        if not rows or (current_id and str(rows[0].get("id"))==str(current_id)):
            return slug
        slug=f"{base}-{i}"; i+=1

def audit(actor,action,entity_type=None,entity_id=None,metadata=None):
    try: db().table("audit_logs").insert({"actor_email":actor,"action":action,"entity_type":entity_type,"entity_id":str(entity_id) if entity_id else None,"metadata":metadata or {}}).execute()
    except Exception: pass

def one(table,key,value,published=False):
    q=db().table(table).select("*").eq(key,value)
    if published:q=q.eq("is_published",True)
    r=q.limit(1).execute()
    if not r.data:raise HTTPException(404,"Not found")
    return r.data[0]

def photo_album(photo_id: str, published=True):
    pr=db().table("album_photos").select("*").eq("id",photo_id).eq("is_hidden",False).limit(1).execute().data or []
    if not pr: raise HTTPException(404,"Photo not found")
    photo=pr[0]
    aq=db().table("albums").select("*").eq("id",photo["album_id"])
    if published: aq=aq.eq("is_published",True)
    ar=aq.limit(1).execute().data or []
    if not ar: raise HTTPException(404,"Album not found")
    return photo,ar[0]

def add_event(event_type: str, visitor_id: str | None = None, album_id: str | None = None, photo_id: str | None = None):
    try:
        data={"event_type":event_type,"anonymous_visitor_id":visitor_id,"album_id":album_id,"photo_id":photo_id}
        db().table("photo_events").insert(data).execute()
    except Exception:
        pass

def ai_prompt(payload: AIContentIn) -> str:
    ctx={k:v for k,v in (payload.context or {}).items() if v not in (None,"",[],{})}
    return (
        "You are BOOM's portfolio CMS writing assistant. "
        "Write concise, honest portfolio copy for a Developer x Photographer personal site. "
        "Do not invent links, awards, clients, dates, or metrics. "
        "Use a minimal, modern, professional tone. "
        f"Language: {'Thai' if payload.language == 'th' else 'English'}. "
        f"Content type: {payload.kind}. Tone: {payload.tone}. "
        "Return JSON only. For project return keys: summary, description, role, problem, solution, features, seo_title, seo_description. "
        "For album return keys: title, description, category, seo_title, seo_description. "
        "For site return keys: headline, bio, seo_description. "
        "For contact return keys: contact_heading, contact_intro, availability_text. "
        "Keep descriptions easy to scan and avoid hype. "
        f"Existing admin fields/context: {json.dumps(ctx, ensure_ascii=False)[:6000]}"
    )

def parse_gemini_json(raw: str) -> dict:
    raw=(raw or "").strip()
    raw=re.sub(r"^```(?:json)?|```$","",raw,flags=re.I).strip()
    try:
        return json.loads(raw)
    except Exception:
        start=raw.find("{"); end=raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end+1])
            except Exception:
                pass
    raise HTTPException(502,"Gemini ส่งคำตอบกลับมาในรูปแบบที่อ่านไม่ได้")

def gemini_error(response) -> str:
    detail="Gemini สร้างข้อความไม่สำเร็จ"
    try:
        err=response.json().get("error") or {}
        msg=str(err.get("message") or "").strip()
        if msg:
            detail=f"Gemini error: {msg[:220]}"
    except Exception:
        pass
    return detail

async def gemini_generate_json(payload: AIContentIn) -> dict:
    gemini_key=s.effective_gemini_api_key
    if not gemini_key:
        raise HTTPException(503,"Gemini ยังไม่ได้ตั้งค่า GEMINI_API_KEY ใน Backend")
    prompt=ai_prompt(payload)
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
    body={
        "contents":[{"parts":[{"text":prompt}]}],
        "generationConfig":{
            "temperature":0.7,
            "maxOutputTokens":900,
            "response_mime_type":"application/json"
        }
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r=await client.post(url,headers={"x-goog-api-key":gemini_key,"Content-Type":"application/json"},json=body)
            if r.status_code>=400:
                fallback=await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/interactions",
                    headers={"x-goog-api-key":gemini_key,"Content-Type":"application/json"},
                    json={"model":s.gemini_model,"input":prompt}
                )
                if fallback.status_code>=400:
                    raise HTTPException(502,gemini_error(fallback))
                fdata=fallback.json()
                raw=fdata.get("output_text") or ""
                if not raw:
                    parts=[]
                    for step in fdata.get("steps") or []:
                        for item in step.get("content") or []:
                            if item.get("type")=="text":
                                parts.append(item.get("text",""))
                    raw="".join(parts)
                return parse_gemini_json(raw)
    except httpx.RequestError:
        raise HTTPException(502,"เชื่อมต่อ Gemini ไม่สำเร็จ")
    if r.status_code>=400:
        raise HTTPException(502,gemini_error(r))
    data=r.json()
    text=((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    raw="".join(str(p.get("text","")) for p in text).strip()
    return parse_gemini_json(raw)

def primary_admin(): return s.primary_admin_email

def image_token(file_id: str, admin_email: str):
    secret=s.oauth_state_secret or s.token_encryption_key or s.supabase_service_role_key
    return URLSafeTimedSerializer(secret).dumps({"file_id":file_id,"email":admin_email})

def decode_image_token(token: str, file_id: str):
    secret=s.oauth_state_secret or s.token_encryption_key or s.supabase_service_role_key
    try:data=URLSafeTimedSerializer(secret).loads(token,max_age=3600)
    except (BadSignature,SignatureExpired): raise HTTPException(403,"Invalid image preview token")
    if data.get("file_id") != file_id or data.get("email") not in s.admin_email_set:
        raise HTTPException(403,"Invalid image preview token")

def require_published_image(file_id: str):
    photos=db().table("album_photos").select("album_id").eq("drive_file_id",file_id).eq("is_hidden",False).execute().data or []
    album_ids=[p["album_id"] for p in photos if p.get("album_id")]
    if not album_ids: raise HTTPException(404,"Image not found")
    albums=db().table("albums").select("id").in_("id",album_ids).eq("is_published",True).limit(1).execute().data or []
    if not albums: raise HTTPException(404,"Image not found")

@app.middleware("http")
async def security_headers(request,call_next):
    response=await call_next(request)
    if request.method!="GET" and request.url.path.startswith("/api/admin/albums") and response.status_code<400:
        invalidate_public_cache()
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"]="max-age=63072000; includeSubDomains; preload"
    response.headers["Content-Security-Policy-Report-Only"]="default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob: https:; font-src 'self' https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; script-src 'self'; connect-src 'self' https://*.supabase.co"
    if request.method=="GET" and request.url.path in ("/api/albums","/api/projects","/api/photography/featured"):
        response.headers["Cache-Control"]="public,max-age=60,stale-while-revalidate=300"
    elif request.url.path.startswith("/api/"):response.headers["Cache-Control"]=response.headers.get("Cache-Control","no-store")
    return response

@app.get("/api/health")
def health(): return {"status":"ok","version":"4.0.0","time":nowiso()}

@app.get("/api/public/config")
def public_config(): return {"supabase_url":s.supabase_url,"supabase_anon_key":s.supabase_anon_key}

@app.get("/api/settings")
def settings():
    r=db().table("site_settings").select("*").eq("id",1).limit(1).execute(); return r.data[0] if r.data else {}

@app.get("/go/instagram")
def go_instagram():
    url=(settings().get("instagram_url") or "").strip()
    if not url:
        raise HTTPException(404,"Instagram link is not configured")
    if not re.match(r"^https?://(www\.)?(instagram\.com|instagr\.am)/",url,re.I):
        raise HTTPException(400,"Invalid Instagram URL")
    return RedirectResponse(url,status_code=302)

def absolute_public_url(value:str|None,base:str)->str|None:
    if not value:return None
    return value if re.match(r"^https?://",value,re.I) else base+"/"+value.lstrip("/")

def seo_document(*,title:str,description:str,canonical:str,image_url:str|None,body:str,schema:dict):
    safe=lambda value:html.escape(str(value or ""),quote=True)
    image_meta=f'<meta property="og:image" content="{safe(image_url)}"><meta name="twitter:image" content="{safe(image_url)}">' if image_url else ""
    schema_json=json.dumps(schema,ensure_ascii=False,separators=(",",":")).replace("</","<\\/")
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=5">
<title>{safe(title)}</title><meta name="description" content="{safe(description)}"><link rel="canonical" href="{safe(canonical)}">
<meta property="og:type" content="article"><meta property="og:title" content="{safe(title)}"><meta property="og:description" content="{safe(description)}"><meta property="og:url" content="{safe(canonical)}">{image_meta}<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#120806"><link rel="icon" href="/static/img/boom-profile.jpg"><link rel="stylesheet" href="/static/css/style.css?v=54"><script type="application/ld+json">{schema_json}</script></head><body>{body}<script src="/static/js/site.js?v=57"></script></body></html>'''
    return Response(page,media_type="text/html; charset=utf-8",headers={"Cache-Control":"public,max-age=300,stale-while-revalidate=3600"})

@app.get("/photography/{slug}",response_class=Response)
def photography_page(slug:str):
    album=one("albums","slug",slug,True);base=(settings().get("site_url") or s.frontend_public_url).rstrip("/")
    canonical=f"{base}/photography/{album['slug']}";title=f"{album.get('seo_title') or album.get('title') or 'Photography'} — BOOM"
    description=album.get("seo_description") or album.get("description") or "Photography album by BOOM."
    image=absolute_public_url(f"/api/drive/image/{album['cover_drive_file_id']}" if album.get("cover_drive_file_id") else None,base)
    schema={"@context":"https://schema.org","@type":"ImageGallery","name":album.get("title"),"description":description,"url":canonical,"image":image,"creator":{"@type":"Person","name":"BOOM","sameAs":"https://www.instagram.com/cs.photo_byboom/"}}
    body='<header class="container nav"><a class="brand" href="/" data-name>BOOM</a><button class="menu-btn" aria-label="Open menu" aria-expanded="false">☰</button><nav class="nav-links"><a href="/">Home</a><a href="/work.html">Coding</a><a href="/photography.html">Photography</a><a href="/about.html">About</a><a href="/contact.html">Contact</a></nav></header><main id="albumDetail" class="container section"></main><div class="lightbox" id="lightbox" role="dialog" aria-modal="true" aria-label="Photo viewer"><button id="lightboxClose" aria-label="Close image">Close ×</button><button class="lightbox-nav prev" id="lightboxPrev" aria-label="Previous image">←</button><img id="lightboxImage" alt=""><button class="lightbox-nav next" id="lightboxNext" aria-label="Next image">→</button></div><footer class="footer"><div class="container footer-inner"><strong data-name>BOOM</strong><span data-headline>Developer & Photographer</span></div></footer>'
    return seo_document(title=title,description=description,canonical=canonical,image_url=image,body=body,schema=schema)

@app.get("/projects/{slug}",response_class=Response)
def project_page(slug:str):
    item=one("projects","slug",slug,True);base=(settings().get("site_url") or s.frontend_public_url).rstrip("/")
    canonical=f"{base}/projects/{item['slug']}";title=f"{item.get('seo_title') or item.get('title') or 'Project'} — BOOM"
    description=item.get("seo_description") or item.get("summary") or item.get("description") or "Developer project by BOOM."
    image=absolute_public_url(item.get("cover_url"),base)
    schema={"@context":"https://schema.org","@type":"CreativeWork","name":item.get("title"),"description":description,"url":canonical,"image":image,"author":{"@type":"Person","name":"BOOM"},"keywords":item.get("tech_stack") or []}
    body='<header class="container nav"><a class="brand" href="/" data-name>BOOM</a><button class="menu-btn" aria-label="Open menu" aria-expanded="false">☰</button><nav class="nav-links"><a href="/">Home</a><a href="/work.html">Coding</a><a href="/photography.html">Photography</a><a href="/about.html">About</a><a href="/contact.html">Contact</a></nav></header><main id="projectDetail" class="container section"></main><footer class="footer"><div class="container footer-inner"><strong data-name>BOOM</strong><span data-headline>Developer & Photographer</span></div></footer>'
    return seo_document(title=title,description=description,canonical=canonical,image_url=image,body=body,schema=schema)

@app.get("/share/photography/{slug}/{photo_id}", response_class=Response)
def share_photography_handoff(slug: str, photo_id: str):
    album=one("albums","slug",slug,True)
    photo,photo_album_row=photo_album(photo_id,True)
    if str(photo.get("album_id")) != str(album.get("id")) or str(photo_album_row.get("id")) != str(album.get("id")):
        raise HTTPException(404,"Photo not found")
    base=(settings().get("site_url") or s.frontend_public_url).rstrip("/")
    photo_url=f"{base}/photography/{slug}?photo={photo_id}"
    image_url=f"{base}/api/drive/image/{photo['drive_file_id']}"
    title=f"{album.get('title') or 'Photography'} — CS.BOOM Photography"
    desc=album.get("seo_description") or album.get("description") or "View this photo and the full album on CS.BOOM Photography."
    safe=lambda v: html.escape(str(v or ""),quote=True)
    body=f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe(title)}</title><link rel="canonical" href="{safe(photo_url)}">
<meta name="description" content="{safe(desc)}">
<meta property="og:type" content="article"><meta property="og:title" content="{safe(title)}">
<meta property="og:description" content="{safe(desc)}"><meta property="og:url" content="{safe(photo_url)}">
<meta property="og:image" content="{safe(image_url)}"><meta name="twitter:card" content="summary_large_image">
<meta http-equiv="refresh" content="0; url={safe(photo_url)}"></head>
<body><p><a href="{safe(photo_url)}">Open photo</a></p></body></html>"""
    return Response(body,media_type="text/html; charset=utf-8",headers={"Cache-Control":"public,max-age=300"})

@app.get("/api/projects")
def projects(featured: bool|None=None,q: str|None=None):
    query=db().table("projects").select("*").eq("is_published",True).order("sort_order").order("created_at",desc=True)
    if featured is not None:query=query.eq("is_featured",featured)
    rows=query.execute().data or []
    if q:
        x=q.lower(); rows=[p for p in rows if x in (p.get("title") or "").lower() or any(x in str(t).lower() for t in p.get("tech_stack") or [])]
    return rows

@app.get("/api/instagram/media")
async def public_instagram_media():return await instagram_media()

@app.get("/api/projects/{slug}")
def project(slug:str): return one("projects","slug",slug,True)

@app.get("/api/albums")
def albums(category:str|None=None):
    cache_key=("albums",category or "*")
    cached=public_cache_get(cache_key)
    if cached is not None:return cached
    try:
        def load_albums():
            q=db().table("albums").select("*").eq("is_published",True).order("created_at",desc=True)
            if category:q=q.eq("category",category)
            return q.execute().data or []
        rows=retry_query(load_albums)
        ids=[a["id"] for a in rows]
        photos=retry_query(lambda:db().table("album_photos").select("id,album_id,drive_file_id,file_name,alt_text,width,height,sort_order").in_("album_id",ids).eq("is_hidden",False).order("sort_order").execute().data or []) if ids else []
        counts=defaultdict(int)
        previews=defaultdict(list)
        for photo in photos:
            album_id=str(photo.get("album_id"));counts[album_id]+=1
            if len(previews[album_id])<4:
                photo["image_url"]=f"/api/drive/image/{photo['drive_file_id']}"
                previews[album_id].append(photo)
        for a in rows:
            a["cover_url"]=f"/api/drive/image/{a['cover_drive_file_id']}" if a.get("cover_drive_file_id") else None
            a["photo_count"]=counts[str(a["id"])]
            a["preview_photos"]=previews[str(a["id"])]
        return public_cache_set(cache_key,rows)
    except Exception:
        stale=public_cache_get(cache_key,allow_stale=True)
        if stale is not None:return stale
        raise HTTPException(503,"Photography albums are temporarily unavailable")

@app.get("/api/albums/{slug}")
def album(slug:str, visitor_id: str|None=None):
    cache_key=("album",slug)
    base=public_cache_get(cache_key)
    try:
        if base is None:
            a=retry_query(lambda:one("albums","slug",slug,True))
            photos=retry_query(lambda:db().table("album_photos").select("*").eq("album_id",a["id"]).eq("is_hidden",False).order("sort_order").execute().data or [])
            base=public_cache_set(cache_key,{"album":a,"photos":photos})
    except HTTPException:raise
    except Exception:
        base=public_cache_get(cache_key,allow_stale=True)
        if base is None:raise HTTPException(503,"This album is temporarily unavailable")
    a=base["album"];photos=base["photos"]
    a.setdefault("allow_downloads",True); a.setdefault("allow_sharing",True); a.setdefault("show_likes",True); a.setdefault("download_quality","high")
    for p in photos:p["image_url"]=f"/api/drive/image/{p['drive_file_id']}"
    ids=[p["id"] for p in photos]
    like_counts={pid:0 for pid in ids}; liked=set()
    if ids and a.get("show_likes",True):
        try:
            likes=db().table("photo_likes").select("photo_id,anonymous_visitor_id").in_("photo_id",ids).execute().data or []
            v=clean_visitor(visitor_id) if visitor_id else None
            for row in likes:
                pid=row.get("photo_id"); like_counts[pid]=like_counts.get(pid,0)+1
                if v and row.get("anonymous_visitor_id")==v: liked.add(pid)
        except Exception:
            pass
    for p in photos:
        p["like_count"]=like_counts.get(p["id"],0)
        p["liked_by_visitor"]=p["id"] in liked
    a["photos"]=photos;a["cover_url"]=f"/api/drive/image/{a['cover_drive_file_id']}" if a.get("cover_drive_file_id") else None;return a

@app.post("/api/albums/view")
def track_album_view(payload:AlbumViewIn):
    try:
        visitor=clean_visitor(payload.anonymous_visitor_id); throttle(f"album_view:{payload.album_id}:{visitor}",8,600)
        add_event("album_view",visitor,payload.album_id,None)
        return {"ok":True,"tracked":True}
    except HTTPException as exc:
        if exc.status_code in (400,422,429):raise
        return {"ok":True,"tracked":False}
    except Exception:return {"ok":True,"tracked":False}

@app.get("/api/photography/featured")
def featured_photography(limit:int=Query(5,ge=1,le=12)):
    albums=db().table("albums").select("id,title,slug,category").eq("is_published",True).execute().data or []
    if not albums:return []
    album_map={str(a["id"]):a for a in albums}
    photos=db().table("album_photos").select("id,album_id,drive_file_id,file_name,alt_text,width,height,sort_order").in_("album_id",list(album_map)).eq("is_hidden",False).execute().data or []
    if not photos:return []
    photo_ids=[str(p["id"]) for p in photos]
    metrics={pid:{"likes":0,"shares":0,"downloads":0,"views":0} for pid in photo_ids}
    try:
        for row in db().table("photo_likes").select("photo_id").in_("photo_id",photo_ids).execute().data or []:
            pid=str(row.get("photo_id"))
            if pid in metrics:metrics[pid]["likes"]+=1
    except Exception:pass
    try:
        event_keys={"share":"shares","download":"downloads","photo_view":"views"}
        for row in db().table("photo_events").select("photo_id,event_type").in_("photo_id",photo_ids).execute().data or []:
            pid=str(row.get("photo_id"));key=event_keys.get(row.get("event_type"))
            if pid in metrics and key:metrics[pid][key]+=1
    except Exception:pass
    ranked=[]
    for p in photos:
        pid=str(p["id"]);m=metrics[pid];a=album_map.get(str(p["album_id"]),{})
        score=m["likes"]*6+m["shares"]*5+m["downloads"]*3+m["views"]
        ranked.append({**p,**m,"engagement_score":score,"image_url":f"/api/drive/image/{p['drive_file_id']}","album_title":a.get("title"),"album_slug":a.get("slug"),"category":a.get("category") or "Photography"})
    ranked.sort(key=lambda p:(-p["engagement_score"],p.get("sort_order") or 0,str(p["id"])))
    return ranked[:limit]

@app.post("/api/photos/{photo_id}/like")
def like_photo(photo_id:str,payload:VisitorIn):
    visitor=clean_visitor(payload.anonymous_visitor_id); photo,album=photo_album(photo_id,True)
    throttle(f"photo_like:{photo_id}:{visitor}",24,600)
    try: db().table("photo_likes").insert({"photo_id":photo_id,"anonymous_visitor_id":visitor}).execute()
    except Exception: raise HTTPException(503,"Photo likes are not ready. Run the database migration first.")
    try: count=len(db().table("photo_likes").select("id").eq("photo_id",photo_id).execute().data or [])
    except Exception: count=0
    return {"liked":True,"like_count":count}

@app.delete("/api/photos/{photo_id}/like")
def unlike_photo(photo_id:str,payload:VisitorIn):
    visitor=clean_visitor(payload.anonymous_visitor_id); photo,album=photo_album(photo_id,True)
    throttle(f"photo_like:{photo_id}:{visitor}",24,600)
    try: db().table("photo_likes").delete().eq("photo_id",photo_id).eq("anonymous_visitor_id",visitor).execute()
    except Exception: raise HTTPException(503,"Photo likes are not ready. Run the database migration first.")
    try: count=len(db().table("photo_likes").select("id").eq("photo_id",photo_id).execute().data or [])
    except Exception: count=0
    return {"liked":False,"like_count":max(0,count)}

@app.post("/api/photos/{photo_id}/event")
def photo_event(photo_id:str,payload:PhotoEventIn):
    # Analytics is best-effort and must never interrupt the gallery.
    try:
        visitor=clean_visitor(payload.anonymous_visitor_id); photo,album=photo_album(photo_id,True)
        throttle(f"photo_event:{payload.event_type}:{photo_id}:{visitor}",30,600)
        add_event(payload.event_type,visitor,photo["album_id"],photo_id)
        return {"ok":True,"tracked":True}
    except HTTPException as exc:
        if exc.status_code in (400,404,422,429):raise
        return {"ok":True,"tracked":False}
    except Exception:
        return {"ok":True,"tracked":False}

@app.get("/api/photos/{photo_id}/download")
async def download_photo(photo_id:str,visitor_id:str|None=None):
    photo,album=photo_album(photo_id,True)
    if not album.get("allow_downloads",True): raise HTTPException(403,"Downloads are disabled for this album")
    visitor=clean_visitor(visitor_id) if visitor_id else None
    if visitor: throttle(f"photo_download:{photo_id}:{visitor}",12,600)
    widths={"web":1200,"high":2000,"original":2000};width=widths.get(album.get("download_quality","high"),2000)
    content,ctype,_,_=await optimized_image_bytes(photo["drive_file_id"],primary_admin(),width=width,quality=88)
    add_event("download",visitor,photo["album_id"],photo_id)
    index=(photo.get("sort_order") or 0)+1
    name=f"CSBOOM_{safe_filename(album.get('title'))}_{index:03d}.jpg"
    return Response(content,media_type=ctype,headers={"Content-Disposition":f'attachment; filename="{name}"',"Cache-Control":"private,max-age=0"})

@app.get("/api/drive/image/{file_id}")
async def drive_image(file_id:str,token:str|None=None,w:int=Query(1600,ge=64,le=2000),q:int=Query(84,ge=45,le=90)):
    if token: decode_image_token(token,file_id)
    else: require_published_image(file_id)
    content,ctype,width,height=await optimized_image_bytes(file_id,primary_admin(),width=w,quality=q)
    return Response(content=content,media_type=ctype,headers={"Cache-Control":"public,max-age=86400,stale-while-revalidate=604800","X-Image-Width":str(width),"X-Image-Height":str(height)})

@app.get("/api/photos/{photo_id}/exif")
async def photo_exif(photo_id:str):
    photo,_=photo_album(photo_id,True)
    return await image_exif(photo["drive_file_id"],primary_admin())

@app.get("/api/qr")
async def qr_code(data:str=Query(...,min_length=8,max_length=800)):
    if not re.match(r"^https?://",data): raise HTTPException(400,"Invalid QR data")
    url="https://api.qrserver.com/v1/create-qr-code/"
    async with httpx.AsyncClient(timeout=12) as client:
        r=await client.get(url,params={"size":"220x220","margin":"14","data":data})
    if r.status_code>=400: raise HTTPException(502,"Could not create QR")
    return Response(r.content,media_type="image/png",headers={"Cache-Control":"public,max-age=86400"})

@app.post("/api/contact",status_code=201)
def contact(payload:ContactIn,request:Request):
    if payload.website: return {"message":"sent"}
    row=db().table("site_settings").select("show_contact_form").eq("id",1).limit(1).execute()
    if row.data and not row.data[0].get("show_contact_form",True):raise HTTPException(403,"Contact form is disabled")
    key=request.client.host if request.client else "unknown"; now=time.time(); dq=_rate[key]
    while dq and dq[0] < now-600:dq.popleft()
    if len(dq)>=s.contact_rate_limit_per_10_min:raise HTTPException(429,"ส่งข้อความบ่อยเกินไป กรุณาลองใหม่ภายหลัง")
    dq.append(now); data=payload.model_dump();data.pop("website",None)
    r=db().table("contact_messages").insert(data).execute();return {"message":"ส่งข้อความเรียบร้อย","id":r.data[0]["id"] if r.data else None}

@app.get("/api/sitemap.xml",response_class=Response)
def sitemap():
    base=(settings().get("site_url") or s.frontend_public_url).rstrip("/")
    urls=[base,base+"/work",base+"/photography",base+"/about",base+"/contact"]
    urls += [f"{base}/projects/{p['slug']}" for p in projects()]
    urls += [f"{base}/photography/{a['slug']}" for a in albums()]
    xml='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join(f'<url><loc>{u}</loc></url>' for u in urls)+'</urlset>'
    return Response(xml,media_type="application/xml")

@app.get("/api/robots.txt",response_class=PlainTextResponse)
def robots():
    base=(settings().get("site_url") or s.frontend_public_url).rstrip("/");return f"User-agent: *\nAllow: /\nDisallow: /admin\nSitemap: {base}/api/sitemap.xml\n"

# ADMIN
@app.get("/api/admin/me")
async def me(admin=Depends(require_admin)):return admin

@app.get("/api/admin/dashboard")
async def dashboard(admin=Depends(require_admin)):
    d=db()
    try:
        result=d.rpc("dashboard_counts",{}).execute().data
        if isinstance(result,dict):return result
    except Exception:pass
    return {"projects":len(d.table("projects").select("id").execute().data or []),"published_projects":len(d.table("projects").select("id").eq("is_published",True).execute().data or []),"albums":len(d.table("albums").select("id").execute().data or []),"photos":len(d.table("album_photos").select("id").execute().data or []),"published_albums":len(d.table("albums").select("id").eq("is_published",True).execute().data or [])}

@app.get("/api/admin/photography/analytics")
async def photo_analytics(days:int=Query(90,ge=1,le=365),admin=Depends(require_admin)):
    since=datetime.fromtimestamp(time.time()-days*86400,tz=timezone.utc).isoformat()
    try: events=db().table("photo_events").select("event_type,photo_id,album_id").gte("created_at",since).limit(10000).execute().data or []
    except Exception: events=[]
    try: likes=db().table("photo_likes").select("photo_id").gte("created_at",since).limit(10000).execute().data or []
    except Exception: likes=[]
    photos=db().table("album_photos").select("id,file_name,album_id,drive_file_id").execute().data or []
    albums=db().table("albums").select("id,title").execute().data or []
    pmap={p["id"]:p for p in photos}; amap={a["id"]:a for a in albums}
    totals={"album_views":0,"photo_views":0,"likes":len(likes),"downloads":0,"shares":0}
    counts={}
    album_counts={}
    for e in events:
        t=e.get("event_type")
        if t=="album_view": totals["album_views"]+=1
        if t=="photo_view": totals["photo_views"]+=1
        if t=="download": totals["downloads"]+=1
        if t=="share": totals["shares"]+=1
        pid=e.get("photo_id")
        if pid:
            counts.setdefault(pid,{"likes":0,"downloads":0,"shares":0,"views":0})
            if t=="photo_view": counts[pid]["views"]+=1
            if t=="download": counts[pid]["downloads"]+=1
            if t=="share": counts[pid]["shares"]+=1
        aid=e.get("album_id")
        if aid:
            album_counts.setdefault(aid,{"views":0,"photo_views":0,"downloads":0,"shares":0,"likes":0})
            if t=="album_view": album_counts[aid]["views"]+=1
            if t=="photo_view": album_counts[aid]["photo_views"]+=1
            if t=="download": album_counts[aid]["downloads"]+=1
            if t=="share": album_counts[aid]["shares"]+=1
    for l in likes:
        pid=l.get("photo_id"); counts.setdefault(pid,{"likes":0,"downloads":0,"shares":0,"views":0}); counts[pid]["likes"]+=1
        aid=pmap.get(pid,{}).get("album_id")
        if aid:
            album_counts.setdefault(aid,{"views":0,"photo_views":0,"downloads":0,"shares":0,"likes":0}); album_counts[aid]["likes"]+=1
    def photo_row(pid,metric):
        p=pmap.get(pid,{})
        return {"photo_id":pid,"file_name":p.get("file_name") or pid,"image_url":f"/api/drive/image/{p.get('drive_file_id')}" if p.get("drive_file_id") else None,**counts.get(pid,{}),metric:counts.get(pid,{}).get(metric,0)}
    most_liked=[photo_row(pid,"likes") for pid in sorted(counts,key=lambda x:counts[x].get("likes",0),reverse=True)[:8]]
    most_downloaded=[photo_row(pid,"downloads") for pid in sorted(counts,key=lambda x:counts[x].get("downloads",0),reverse=True)[:8]]
    album_rows=[{"album_id":aid,"title":amap.get(aid,{}).get("title") or aid,**vals} for aid,vals in album_counts.items()]
    return {"period_days":days,"totals":totals,"most_liked":most_liked,"most_downloaded":most_downloaded,"albums":album_rows}

@app.get("/api/admin/projects")
async def admin_projects(admin=Depends(require_admin)):return db().table("projects").select("*").order("sort_order").order("created_at",desc=True).execute().data or []

@app.post("/api/admin/github/analyze")
async def github_analyze(payload:GitHubImportIn,admin=Depends(require_admin)):
    data=analyze_repo(payload.repo_url);audit(admin["email"],"github.analyze","repository",payload.repo_url,{"files":data["file_count"]});return data

@app.post("/api/admin/ai/generate")
async def ai_generate(payload:AIContentIn,admin=Depends(require_admin)):
    out=await gemini_generate_json(payload)
    audit(admin["email"],"ai.generate",payload.kind,None,{"keys":list(out.keys())})
    return out

@app.get("/api/admin/ai/status")
async def ai_status(admin=Depends(require_admin)):
    return {"provider":"Gemini","configured":bool(s.effective_gemini_api_key),"model":s.gemini_model}

@app.post("/api/admin/ai/test")
async def ai_test(admin=Depends(require_admin)):
    out=await gemini_generate_json(AIContentIn(kind="contact",context={"request":"Return a short readiness message for BOOM CMS."}))
    audit(admin["email"],"ai.test","gemini",None,{"model":s.gemini_model})
    return {"ok":True,"model":s.gemini_model,"sample":out}

@app.post("/api/admin/projects")
async def create_project(payload:ProjectIn,admin=Depends(require_admin)):
    data=payload.model_dump();data["slug"]=slugify(payload.slug or payload.title);data["updated_at"]=nowiso()
    try:r=db().table("projects").insert(data).execute()
    except Exception as e:raise HTTPException(409,"Project slug ซ้ำหรือข้อมูลไม่ถูกต้อง")
    p=r.data[0];audit(admin["email"],"project.create","project",p["id"]);return p

@app.put("/api/admin/projects/{project_id}")
async def update_project(project_id:str,payload:ProjectIn,admin=Depends(require_admin)):
    data=payload.model_dump();data["slug"]=slugify(payload.slug or payload.title);data["updated_at"]=nowiso()
    r=db().table("projects").update(data).eq("id",project_id).execute()
    if not r.data:raise HTTPException(404,"Project not found")
    audit(admin["email"],"project.update","project",project_id);return r.data[0]

@app.post("/api/admin/projects/{project_id}/sync-github")
async def sync_project(project_id:str,admin=Depends(require_admin)):
    old=one("projects","id",project_id);url=old.get("source_url")
    if not url or "github.com" not in url:raise HTTPException(400,"Project นี้ไม่มี GitHub URL")
    fresh=analyze_repo(url)
    patch={"tech_stack":fresh["tech_stack"],"github_owner":fresh["github_owner"],"github_repo":fresh["github_repo"],"github_branch":fresh["github_branch"],"github_last_synced_at":nowiso(),"updated_at":nowiso()}
    if not old.get("cover_url") and fresh.get("cover_url"):patch["cover_url"]=fresh["cover_url"]
    if not old.get("screenshots") and fresh.get("screenshots"):patch["screenshots"]=fresh["screenshots"]
    r=db().table("projects").update(patch).eq("id",project_id).execute();audit(admin["email"],"project.github_sync","project",project_id);return r.data[0]

@app.delete("/api/admin/projects/{project_id}")
async def delete_project(project_id:str,admin=Depends(require_admin)):
    db().table("projects").delete().eq("id",project_id).execute();audit(admin["email"],"project.delete","project",project_id);return {"message":"deleted"}

@app.post("/api/admin/drive/import")
async def import_drive(payload:DriveImport,admin=Depends(require_admin)):
    fid=folder_id_from(payload.folder_url);photos=await list_images(fid,admin["email"]);name=await folder_name(fid,admin["email"])
    for p in photos:p["image_url"]=f"/api/drive/image/{p['id']}?token={image_token(p['id'],admin['email'])}"
    return {"folder_id":fid,"folder_name":name,"count":len(photos),"photos":photos}

@app.get("/api/admin/albums")
async def admin_albums(admin=Depends(require_admin)):
    rows=db().table("albums").select("*").order("created_at",desc=True).execute().data or []
    for a in rows:a["cover_url"]=f"/api/drive/image/{a['cover_drive_file_id']}" if a.get("cover_drive_file_id") else None
    return rows

@app.get("/api/admin/albums/{album_id}")
async def admin_album(album_id:str,admin=Depends(require_admin)):
    a=one("albums","id",album_id);a["photos"]=db().table("album_photos").select("*").eq("album_id",album_id).order("sort_order").execute().data or []
    for p in a["photos"]:p["image_url"]=f"/api/drive/image/{p['drive_file_id']}?token={image_token(p['drive_file_id'],admin['email'])}"
    return a

@app.post("/api/admin/albums")
async def create_album(payload:AlbumIn,background_tasks:BackgroundTasks,admin=Depends(require_admin)):
    allp=await list_images(payload.drive_folder_id,admin["email"]);lookup={p["id"]:p for p in allp};selected=[lookup[x] for x in payload.selected_file_ids if x in lookup]
    if not selected:raise HTTPException(400,"ไม่ได้เลือกรูป")
    cover=payload.cover_drive_file_id if payload.cover_drive_file_id in {p['id'] for p in selected} else selected[0]["id"]
    data={"title":payload.title,"slug":unique_slug("albums",payload.slug or payload.title),"description":payload.description,"category":payload.category,"event_date":payload.event_date.isoformat() if payload.event_date else None,"cover_drive_file_id":cover,"drive_folder_id":payload.drive_folder_id,"drive_folder_url":payload.drive_folder_url,"is_published":payload.is_published,"allow_downloads":payload.allow_downloads,"allow_sharing":payload.allow_sharing,"show_likes":payload.show_likes,"download_quality":payload.download_quality,"seo_title":payload.seo_title,"seo_description":payload.seo_description,"updated_at":nowiso()}
    try:ar=db().table("albums").insert(data).execute()
    except Exception:raise HTTPException(409,"Album slug ซ้ำหรือข้อมูลไม่ถูกต้อง")
    a=ar.data[0];rows=[{"album_id":a["id"],"drive_file_id":p["id"],"file_name":p["name"],"mime_type":p["mime_type"],"width":p.get("width"),"height":p.get("height"),"alt_text":f"{payload.title} photograph {i+1}","sort_order":i} for i,p in enumerate(selected)];db().table("album_photos").insert(rows).execute();audit(admin["email"],"album.create","album",a["id"],{"photos":len(rows)})
    # Pre-render+cache the widths the public gallery will request, so the first
    # real visitor doesn't pay for a cold Drive fetch + resize on every photo.
    background_tasks.add_task(warm_cache,[p["id"] for p in selected],admin["email"])
    return {"album":a,"photo_count":len(rows)}

@app.put("/api/admin/albums/{album_id}")
async def update_album(album_id:str,payload:AlbumUpdate,admin=Depends(require_admin)):
    data=payload.model_dump();data["slug"]=unique_slug("albums",payload.slug or payload.title,album_id);data["event_date"]=payload.event_date.isoformat() if payload.event_date else None;data["updated_at"]=nowiso();r=db().table("albums").update(data).eq("id",album_id).execute();audit(admin["email"],"album.update","album",album_id);return r.data[0] if r.data else {}

@app.put("/api/admin/albums/{album_id}/order")
async def reorder_album(album_id:str,payload:PhotoOrderIn,admin=Depends(require_admin)):
    current=db().table("album_photos").select("id").eq("album_id",album_id).execute().data or []
    current_ids={str(row["id"]) for row in current}
    if len(payload.photo_ids)!=len(set(payload.photo_ids)) or set(payload.photo_ids)!=current_ids:
        raise HTTPException(400,"Photo order must contain every album photo exactly once")
    try:
        db().rpc("reorder_album_photos",{"p_album_id":album_id,"p_photo_ids":payload.photo_ids}).execute()
    except Exception:
        # Compatibility fallback until the bundled migration has been applied.
        for i,pid in enumerate(payload.photo_ids):db().table("album_photos").update({"sort_order":i}).eq("id",pid).eq("album_id",album_id).execute()
    audit(admin["email"],"album.reorder","album",album_id);return {"message":"ordered"}

@app.put("/api/admin/albums/{album_id}/photos")
async def update_photo_metadata(album_id:str,payload:PhotoMetadataIn,admin=Depends(require_admin)):
    current=db().table("album_photos").select("id").eq("album_id",album_id).execute().data or []
    allowed={str(row["id"]) for row in current};requested={item.id for item in payload.photos}
    if not requested.issubset(allowed):raise HTTPException(400,"Photo does not belong to this album")
    try:
        db().rpc("update_album_photo_metadata",{"p_album_id":album_id,"p_items":[item.model_dump() for item in payload.photos]}).execute()
    except Exception:
        for item in payload.photos:db().table("album_photos").update({"alt_text":item.alt_text.strip()}).eq("id",item.id).eq("album_id",album_id).execute()
    audit(admin["email"],"album.photos.update","album",album_id,{"photos":len(payload.photos)})
    return {"message":"photo metadata updated","updated":len(payload.photos)}

@app.delete("/api/admin/albums/{album_id}")
async def delete_album(album_id:str,admin=Depends(require_admin)):
    try:r=db().table("albums").delete().eq("id",album_id).execute()
    except Exception:raise HTTPException(409,"ลบอัลบั้มไม่ได้ กรุณารัน database migration ล่าสุดเพื่อตั้งค่า cascade")
    if not r.data:raise HTTPException(404,"Album not found")
    audit(admin["email"],"album.delete","album",album_id);return {"message":"deleted"}

@app.get("/api/admin/messages")
async def messages(admin=Depends(require_admin)):return db().table("contact_messages").select("*").eq("is_archived",False).order("created_at",desc=True).execute().data or []
@app.patch("/api/admin/messages/{mid}/read")
async def read_message(mid:str,admin=Depends(require_admin)):return (db().table("contact_messages").update({"is_read":True}).eq("id",mid).execute().data or [{}])[0]
@app.patch("/api/admin/messages/{mid}/archive")
async def archive_message(mid:str,admin=Depends(require_admin)):return (db().table("contact_messages").update({"is_archived":True}).eq("id",mid).execute().data or [{}])[0]
@app.delete("/api/admin/messages/{mid}")
async def delete_message(mid:str,admin=Depends(require_admin)):db().table("contact_messages").delete().eq("id",mid).execute();return {"message":"deleted"}

@app.put("/api/admin/settings")
async def update_settings(payload:SettingsIn,admin=Depends(require_admin)):
    data={"id":1,**payload.model_dump(),"updated_at":nowiso()};r=db().table("site_settings").upsert(data).execute();audit(admin["email"],"settings.update","settings","1");return r.data[0]

@app.get("/api/admin/integrations/google/status")
async def google_connection(admin=Depends(require_admin)):return await google_status(admin["email"])
@app.get("/api/admin/integrations/google/start")
async def google_start(admin=Depends(require_admin)):return {"authorization_url":google_auth_url(admin["email"])}
@app.delete("/api/admin/integrations/google")
async def google_disconnect(admin=Depends(require_admin)):disconnect_google(admin["email"]);return {"message":"disconnected"}

@app.get("/api/integrations/google/callback")
async def google_callback(code:str,state:str):
    data=decode_state(state);token=await google_exchange(code);save_google_token(data["email"],token)
    return RedirectResponse(s.frontend_public_url.rstrip("/")+"/admin.html?google=connected")
