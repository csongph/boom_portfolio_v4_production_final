from functools import lru_cache
from supabase import create_client
from .config import get_settings

try:
    from redis import Redis
except ImportError:
    Redis = None

_PERMISSION_FIELDS = ("allow_downloads", "allow_sharing", "show_likes")


def _force_permissions(value):
    """Force download, share and likes on for every album payload."""
    if isinstance(value, dict):
        row = dict(value)
        for field in _PERMISSION_FIELDS:
            row[field] = True
        return row
    if isinstance(value, list):
        return [_force_permissions(item) for item in value]
    return value


def _mutate_response_permissions(response):
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        for field in _PERMISSION_FIELDS:
            data[field] = True
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                for field in _PERMISSION_FIELDS:
                    row[field] = True
    return response


class _AlbumQueryProxy:
    """Transparent Supabase query proxy that keeps album permissions enabled."""

    def __init__(self, query):
        self._query = query

    def execute(self):
        return _mutate_response_permissions(self._query.execute())

    def __getattr__(self, name):
        attr = getattr(self._query, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            if name in {"insert", "update", "upsert"} and args:
                args = list(args)
                args[0] = _force_permissions(args[0])
            result = attr(*args, **kwargs)
            if hasattr(result, "execute"):
                self._query = result
                return self
            return result

        return call


class _DatabaseProxy:
    def __init__(self, client):
        self._client = client

    def table(self, name):
        query = self._client.table(name)
        return _AlbumQueryProxy(query) if name == "albums" else query

    def __getattr__(self, name):
        return getattr(self._client, name)


def _backfill_album_permissions(client):
    """Persist the rule for albums created before permissions became automatic."""
    try:
        rows = client.table("albums").select(
            "id,allow_downloads,allow_sharing,show_likes"
        ).execute().data or []
        patch = {field: True for field in _PERMISSION_FIELDS}
        for row in rows:
            if not all(row.get(field) is True for field in _PERMISSION_FIELDS):
                client.table("albums").update(patch).eq("id", row["id"]).execute()
    except Exception:
        # The proxy still enforces all three values for application reads/writes.
        pass


def _clear_public_album_cache(settings):
    """Drop stale Redis public cache so old false permission values cannot survive deploy."""
    if not Redis or not getattr(settings, "redis_url", None):
        return
    try:
        redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        keys = list(redis.scan_iter(match="boom:public:*", count=100))
        if keys:
            redis.delete(*keys)
    except Exception:
        pass


@lru_cache
def db():
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    _backfill_album_permissions(client)
    _clear_public_album_cache(settings)
    return _DatabaseProxy(client)
