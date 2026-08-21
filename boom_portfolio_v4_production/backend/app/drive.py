import re
import httpx
from fastapi import HTTPException
from .config import get_settings
from .integrations import google_access_token

API="https://www.googleapis.com/drive/v3"

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

async def image_bytes(file_id: str, admin_email: str | None = None):
    auth=await _auth(admin_email)
    params={**auth["params"],"alt":"media"}
    async with httpx.AsyncClient(timeout=60,follow_redirects=True) as client:
        r=await client.get(f"{API}/files/{file_id}",params=params,headers=auth["headers"])
    if r.is_error: raise HTTPException(r.status_code,"โหลดรูปจาก Google Drive ไม่สำเร็จ")
    return r.content,r.headers.get("content-type","image/jpeg")
