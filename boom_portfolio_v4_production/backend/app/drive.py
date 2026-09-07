import re
import io
import os
import time
import asyncio
import hashlib
from pathlib import Path
from collections import OrderedDict
import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, ExifTags
from .config import get_settings
from .integrations import google_access_token

API="https://www.googleapis.com/drive/v3"
_MEDIA_CACHE=OrderedDict()
_MEDIA_CACHE_TTL=60*60*6
# Was 96 — with 5 srcset widths + an LQIP pass per photo, that overflows after
# ~15-20 photos, so growing the library made almost every request a cache miss
# (full Drive re-fetch + re-resize, for everyone). Raised well above a typical
# gallery's working set.
_MEDIA_CACHE_MAX=int(os.getenv("MEDIA_CACHE_MAX","600"))

# Second tier: when an item falls out of the in-memory cache (restart, or the
# cache above still overflows on a very large library), check disk before
# going back to Google Drive. Cheap insurance, no new dependency.
_DISK_CACHE_DIR=Path(os.getenv("IMAGE_DISK_CACHE_DIR","/tmp/drive_image_cache"))
_DISK_CACHE_DIR.mkdir(parents=True,exist_ok=True)

def _disk_cache_path(file_id:str,width:int,quality:int)->Path:
    key=hashlib.sha256(f"{file_id}:{width}:{quality}".encode()).hexdigest()
    return _DISK_CACHE_DIR/f"{key}.jpg"

async def _resilient_get(client, url, **kwargs):
    """Retry transient Drive/CDN failures before surfacing an unavailable image."""
    response=None
    for attempt in range(3):
        try:
            response=await client.get(url,**kwargs)
            if response.status_code not in (408,429,500,502,503,504):return response
        except (httpx.TimeoutException,httpx.TransportError):
            if attempt==2:raise
        if attempt<2:await asyncio.sleep(.35*(2**attempt))
    return response

def _cache_get(key):
    item=_MEDIA_CACHE.get(key)
    if not item:return None
    expires,value=item
    if expires<time.time():_MEDIA_CACHE.pop(key,None);return None
    _MEDIA_CACHE.move_to_end(key);return value

def _cache_set(key,value):
    _MEDIA_CACHE[key]=(time.time()+_MEDIA_CACHE_TTL,value);_MEDIA_CACHE.move_to_end(key)
    while len(_MEDIA_CACHE)>_MEDIA_CACHE_MAX:_MEDIA_CACHE.popitem(last=False)

def folder_id_from(value: str) -> str:
    value=value.strip()
    for pattern in (r"/folders/([A-Za-z0-9_-]+)",r"[?&]id=([A-Za-z0-9_-]+)"):
        m=re.search(pattern,value)
        if m:return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}",value): return value
    raise HTTPException(400,"ไม่พบ Google Drive Folder ID ในลิงก์นี้")

async def _auth(admin_email: str | None = None):
    if admin_email:
        token=await google_access_token(admin_email)
        if token:return {"headers":{"Authorization":f"Bearer {token}"},"params":{}}
    key=get_settings().google_drive_api_key
    if not key: raise HTTPException(503,"Google Drive API key หรือ Google OAuth ยังไม่ได้ตั้งค่า")
    return {"headers":{},"params":{"key":key}}

async def _list_folder_items(client, folder_id: str, auth: dict):
    params={
        **auth["params"],
        "q":f"'{folder_id}' in parents and trashed = false",
        "fields":"nextPageToken,files(id,name,mimeType,imageMediaMetadata,shortcutDetails,createdTime,modifiedTime)",
        "pageSize":1000,
        "orderBy":"name",
        "supportsAllDrives":"true",
        "includeItemsFromAllDrives":"true",
    }
    out=[]; token=None
    while True:
        if token: params["pageToken"]=token
        r=await client.get(f"{API}/files",params=params,headers=auth["headers"])
        if r.status_code in (401,403,404): raise HTTPException(400,"อ่าน Google Drive ไม่ได้ ตรวจสอบสิทธิ์โฟลเดอร์หรือเชื่อม Google Drive ใน Admin")
        if r.is_error: raise HTTPException(502,f"Google Drive API error: {r.text[:250]}")
        data=r.json()
        out.extend(data.get("files",[]))
        token=data.get("nextPageToken")
        if not token:break
    return out

async def list_images(folder_id: str, admin_email: str | None = None):
    auth=await _auth(admin_email)
    out=[]; queue=[(folder_id,"")]; seen={folder_id}; max_folders=80
    async with httpx.AsyncClient(timeout=30) as client:
        while queue and len(seen)<=max_folders:
            current_id,path=queue.pop(0)
            for f in await _list_folder_items(client,current_id,auth):
                mime=f.get("mimeType","")
                item_id=f["id"]
                if mime=="application/vnd.google-apps.shortcut":
                    shortcut=f.get("shortcutDetails") or {}
                    item_id=shortcut.get("targetId") or item_id
                    mime=shortcut.get("targetMimeType") or mime
                if mime=="application/vnd.google-apps.folder" and item_id not in seen:
                    seen.add(item_id); queue.append((item_id,f"{path}{f.get('name','Folder')}/")); continue
                if not mime.startswith("image/"):continue
                meta=f.get("imageMediaMetadata") or {}
                out.append({"id":item_id,"name":f"{path}{f.get('name','Untitled')}","mime_type":mime or "image/jpeg","width":meta.get("width"),"height":meta.get("height")})
    return out

async def folder_name(folder_id: str, admin_email: str | None = None):
    auth=await _auth(admin_email)
    params={**auth["params"],"fields":"id,name","supportsAllDrives":"true"}
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.get(f"{API}/files/{folder_id}",params=params,headers=auth["headers"])
    if r.status_code in (401,403,404): return None
    if r.is_error: return None
    return (r.json() or {}).get("name")

async def image_bytes(file_id: str, admin_email: str | None = None, allow_public_fallback: bool = False):
    """Load a Drive image without letting an expired OAuth token take down public galleries.

    Private files still use the connected admin account. Published, link-shared files may
    fall back to the API key or Google's public image CDN while the admin reconnects OAuth.
    """
    attempts=[]
    oauth_error=None
    if admin_email:
        try:
            attempts.append(await _auth(admin_email))
        except HTTPException as exc:
            oauth_error=exc

    key=get_settings().google_drive_api_key
    if allow_public_fallback and key:
        key_auth={"headers":{},"params":{"key":key}}
        if key_auth not in attempts: attempts.append(key_auth)

    async with httpx.AsyncClient(timeout=60,follow_redirects=True) as client:
        for auth in attempts:
            r=await _resilient_get(client,
                f"{API}/files/{file_id}",
                params={**auth["params"],"alt":"media"},
                headers=auth["headers"],
            )
            if not r.is_error and r.headers.get("content-type","").lower().startswith("image/"):
                return r.content,r.headers.get("content-type","image/jpeg")

        if allow_public_fallback:
            # Works only for files shared as "Anyone with the link". Validate the media
            # type so an HTML permission page is never cached as an image.
            r=await _resilient_get(client,f"https://lh3.googleusercontent.com/d/{file_id}=w2400")
            if not r.is_error and r.headers.get("content-type","").lower().startswith("image/"):
                return r.content,r.headers.get("content-type","image/jpeg")

    if oauth_error: raise oauth_error
    raise HTTPException(502,"โหลดรูปจาก Google Drive ไม่สำเร็จ")

async def optimized_image_bytes(file_id: str, admin_email: str | None = None, width: int = 1600, quality: int = 84):
    width=max(64,min(int(width),2000));quality=max(45,min(int(quality),90));key=(file_id,width,quality)
    cached=_cache_get(key)
    if cached:return cached

    disk_path=_disk_cache_path(file_id,width,quality)
    if disk_path.exists():
        try:
            data=disk_path.read_bytes()
            with Image.open(io.BytesIO(data)) as img:w,h=img.size
            value=(data,"image/jpeg",w,h)
            _cache_set(key,value);return value
        except Exception:
            pass  # corrupted/partial cache file — fall through and regenerate

    content,_=await image_bytes(file_id,admin_email,allow_public_fallback=True)
    try:
        with Image.open(io.BytesIO(content)) as source:
            image=ImageOps.exif_transpose(source)
            if image.width>width:
                height=max(1,round(image.height*width/image.width));image=image.resize((width,height),Image.Resampling.LANCZOS)
            if max(image.size)>2000:
                scale=2000/max(image.size);image=image.resize((max(1,round(image.width*scale)),max(1,round(image.height*scale))),Image.Resampling.LANCZOS)
            if image.mode not in ("RGB","L"):
                background=Image.new("RGB",image.size,(12,8,7))
                if "A" in image.getbands():background.paste(image,mask=image.getchannel("A"))
                else:background.paste(image)
                image=background
            elif image.mode=="L":image=image.convert("RGB")
            output=io.BytesIO();image.save(output,"JPEG",quality=quality,optimize=True,progressive=True)
            value=(output.getvalue(),"image/jpeg",image.width,image.height)
    except Exception:
        raise HTTPException(422,"ไฟล์นี้ไม่ใช่รูปภาพที่รองรับ")

    try:disk_path.write_bytes(value[0])
    except Exception:pass  # disk cache is best-effort, never block the response on it

    _cache_set(key,value);return value

async def image_exif(file_id: str, admin_email: str | None = None):
    key=("exif",file_id);cached=_cache_get(key)
    if cached:return cached
    content,_=await image_bytes(file_id,admin_email,allow_public_fallback=True)
    try:
        with Image.open(io.BytesIO(content)) as image:
            raw=image.getexif();named={ExifTags.TAGS.get(k,k):v for k,v in raw.items()}
        def text(name):
            value=named.get(name)
            if value in (None,""):return None
            return str(value)
        result={"camera":" ".join(x for x in [text("Make"),text("Model")] if x).strip() or None,"lens":text("LensModel"),"f_stop":f"f/{float(named['FNumber']):g}" if named.get("FNumber") else None,"iso":f"ISO {named['ISOSpeedRatings']}" if named.get("ISOSpeedRatings") else None,"shutter":text("ExposureTime"),"focal_length":f"{float(named['FocalLength']):g} mm" if named.get("FocalLength") else None}
    except Exception:result={}
    result={k:v for k,v in result.items() if v};_cache_set(key,result);return result
