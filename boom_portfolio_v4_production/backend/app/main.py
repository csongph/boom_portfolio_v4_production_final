import json, re, time
from collections import defaultdict, deque
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.responses import Response, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from .database import db
from .security import require_admin
from .drive import folder_id_from, list_images, image_bytes, folder_name
from .schemas import DriveImport, AlbumIn, AlbumUpdate, PhotoOrderIn, ProjectIn, GitHubImportIn, SettingsIn, ContactIn, AIContentIn
from .config import get_settings
from .github_import import analyze_repo
from .integrations import google_auth_url, decode_state, google_exchange, save_google_token, google_status, disconnect_google

app=FastAPI(title="BOOM Portfolio API",version="4.0.0",docs_url="/api/docs",openapi_url="/api/openapi.json")
s=get_settings()
app.add_middleware(CORSMiddleware,allow_origins=s.cors_origin_list,allow_credentials=False,allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],allow_headers=["Content-Type","Authorization"])

_rate=defaultdict(deque)
def nowiso(): return datetime.now(timezone.utc).isoformat()
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

async def gemini_generate_json(payload: AIContentIn) -> dict:
    gemini_key=s.effective_gemini_api_key
    if not gemini_key:
        raise HTTPException(503,"Gemini ยังไม่ได้ตั้งค่า GEMINI_API_KEY ใน Backend")
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
    body={
        "contents":[{"parts":[{"text":ai_prompt(payload)}]}],
        "generationConfig":{
            "temperature":0.7,
            "maxOutputTokens":900,
            "response_mime_type":"application/json"
        }
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r=await client.post(url,headers={"x-goog-api-key":gemini_key,"Content-Type":"application/json"},json=body)
    except httpx.RequestError:
        raise HTTPException(502,"เชื่อมต่อ Gemini ไม่สำเร็จ")
    if r.status_code>=400:
        raise HTTPException(502,"Gemini สร้างข้อความไม่สำเร็จ")
    data=r.json()
    text=((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    raw="".join(str(p.get("text","")) for p in text).strip()
    raw=re.sub(r"^```(?:json)?|```$","",raw,flags=re.I).strip()
    try:
        return json.loads(raw)
    except Exception:
        raise HTTPException(502,"Gemini ส่งคำตอบกลับมาในรูปแบบที่อ่านไม่ได้")

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
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"): response.headers["Cache-Control"]=response.headers.get("Cache-Control","no-store")
    return response

@app.get("/api/health")
def health(): return {"status":"ok","version":"4.0.0","time":nowiso()}

@app.get("/api/public/config")
def public_config(): return {"supabase_url":s.supabase_url,"supabase_anon_key":s.supabase_anon_key}

@app.get("/api/settings")
def settings():
    r=db().table("site_settings").select("*").eq("id",1).limit(1).execute(); return r.data[0] if r.data else {}

@app.get("/api/projects")
def projects(featured: bool|None=None,q: str|None=None):
    query=db().table("projects").select("*").eq("is_published",True).order("sort_order").order("created_at",desc=True)
    if featured is not None:query=query.eq("is_featured",featured)
    rows=query.execute().data or []
    if q:
        x=q.lower(); rows=[p for p in rows if x in (p.get("title") or "").lower() or any(x in str(t).lower() for t in p.get("tech_stack") or [])]
    return rows

@app.get("/api/projects/{slug}")
def project(slug:str): return one("projects","slug",slug,True)

@app.get("/api/albums")
def albums(category:str|None=None):
    q=db().table("albums").select("*").eq("is_published",True).order("created_at",desc=True)
    if category:q=q.eq("category",category)
    rows=q.execute().data or []
    for a in rows:
        a["cover_url"]=f"/api/drive/image/{a['cover_drive_file_id']}" if a.get("cover_drive_file_id") else None
        a["photo_count"]=len(db().table("album_photos").select("id").eq("album_id",a["id"]).eq("is_hidden",False).execute().data or [])
    return rows

@app.get("/api/albums/{slug}")
def album(slug:str):
    a=one("albums","slug",slug,True)
    photos=db().table("album_photos").select("*").eq("album_id",a["id"]).eq("is_hidden",False).order("sort_order").execute().data or []
    for p in photos:p["image_url"]=f"/api/drive/image/{p['drive_file_id']}"
    a["photos"]=photos;a["cover_url"]=f"/api/drive/image/{a['cover_drive_file_id']}" if a.get("cover_drive_file_id") else None;return a

@app.get("/api/drive/image/{file_id}")
async def drive_image(file_id:str,token:str|None=None):
    if token: decode_image_token(token,file_id)
    else: require_published_image(file_id)
    content,ctype=await image_bytes(file_id,primary_admin())
    return Response(content=content,media_type=ctype,headers={"Cache-Control":"public,max-age=86400,stale-while-revalidate=604800"})

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
    urls += [f"{base}/project?slug={p['slug']}" for p in projects()]
    urls += [f"{base}/album?slug={a['slug']}" for a in albums()]
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
    d=db();return {"projects":len(d.table("projects").select("id").execute().data or []),"published_projects":len(d.table("projects").select("id").eq("is_published",True).execute().data or []),"albums":len(d.table("albums").select("id").execute().data or []),"photos":len(d.table("album_photos").select("id").execute().data or []),"published_albums":len(d.table("albums").select("id").eq("is_published",True).execute().data or [])}

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
async def create_album(payload:AlbumIn,admin=Depends(require_admin)):
    allp=await list_images(payload.drive_folder_id,admin["email"]);lookup={p["id"]:p for p in allp};selected=[lookup[x] for x in payload.selected_file_ids if x in lookup]
    if not selected:raise HTTPException(400,"ไม่ได้เลือกรูป")
    cover=payload.cover_drive_file_id if payload.cover_drive_file_id in {p['id'] for p in selected} else selected[0]["id"]
    data={"title":payload.title,"slug":unique_slug("albums",payload.slug or payload.title),"description":payload.description,"category":payload.category,"event_date":payload.event_date.isoformat() if payload.event_date else None,"cover_drive_file_id":cover,"drive_folder_id":payload.drive_folder_id,"drive_folder_url":payload.drive_folder_url,"is_published":True,"seo_title":payload.seo_title,"seo_description":payload.seo_description,"updated_at":nowiso()}
    try:ar=db().table("albums").insert(data).execute()
    except Exception:raise HTTPException(409,"Album slug ซ้ำหรือข้อมูลไม่ถูกต้อง")
    a=ar.data[0];rows=[{"album_id":a["id"],"drive_file_id":p["id"],"file_name":p["name"],"mime_type":p["mime_type"],"width":p.get("width"),"height":p.get("height"),"alt_text":p["name"],"sort_order":i} for i,p in enumerate(selected)];db().table("album_photos").insert(rows).execute();audit(admin["email"],"album.create","album",a["id"],{"photos":len(rows)});return {"album":a,"photo_count":len(rows)}

@app.put("/api/admin/albums/{album_id}")
async def update_album(album_id:str,payload:AlbumUpdate,admin=Depends(require_admin)):
    data=payload.model_dump();data["slug"]=unique_slug("albums",payload.slug or payload.title,album_id);data["event_date"]=payload.event_date.isoformat() if payload.event_date else None;data["updated_at"]=nowiso();r=db().table("albums").update(data).eq("id",album_id).execute();audit(admin["email"],"album.update","album",album_id);return r.data[0] if r.data else {}

@app.put("/api/admin/albums/{album_id}/order")
async def reorder_album(album_id:str,payload:PhotoOrderIn,admin=Depends(require_admin)):
    for i,pid in enumerate(payload.photo_ids):db().table("album_photos").update({"sort_order":i}).eq("id",pid).eq("album_id",album_id).execute()
    audit(admin["email"],"album.reorder","album",album_id);return {"message":"ordered"}

@app.delete("/api/admin/albums/{album_id}")
async def delete_album(album_id:str,admin=Depends(require_admin)):
    db().table("albums").delete().eq("id",album_id).execute();audit(admin["email"],"album.delete","album",album_id);return {"message":"deleted"}

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
async def google_connection(admin=Depends(require_admin)):return google_status(admin["email"])
@app.get("/api/admin/integrations/google/start")
async def google_start(admin=Depends(require_admin)):return {"authorization_url":google_auth_url(admin["email"])}
@app.delete("/api/admin/integrations/google")
async def google_disconnect(admin=Depends(require_admin)):disconnect_google(admin["email"]);return {"message":"disconnected"}

@app.get("/api/integrations/google/callback")
async def google_callback(code:str,state:str):
    data=decode_state(state);token=await google_exchange(code);save_google_token(data["email"],token)
    return RedirectResponse(s.frontend_public_url.rstrip("/")+"/admin.html?google=connected")
