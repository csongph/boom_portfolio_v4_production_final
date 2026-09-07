import time
import httpx
from .config import get_settings

_cache={"expires":0.0,"data":None}

async def instagram_media():
    settings=get_settings();ttl=max(3600,min(settings.instagram_cache_ttl_seconds,21600))
    if _cache["data"] is not None and _cache["expires"]>time.time():
        return {"configured":True,"cached":True,"media":_cache["data"]}
    if not settings.instagram_access_token:
        return {"configured":False,"cached":False,"media":[]}
    fields="id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,username"
    url=f"https://graph.instagram.com/{settings.instagram_api_version}/me/media"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response=await client.get(url,params={"fields":fields,"access_token":settings.instagram_access_token,"limit":50})
        response.raise_for_status();data=response.json().get("data") or []
        _cache.update(data=data,expires=time.time()+ttl)
        return {"configured":True,"cached":False,"media":data}
    except Exception:
        if _cache["data"] is not None:return {"configured":True,"cached":True,"stale":True,"media":_cache["data"]}
        return {"configured":True,"cached":False,"unavailable":True,"media":[]}
