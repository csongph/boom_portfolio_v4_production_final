from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    admin_emails: str
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://127.0.0.1:5500"
    frontend_public_url: str = "http://localhost:3000"
    backend_public_url: str = "http://127.0.0.1:8000"
    google_drive_api_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    gemini_api_key: str = ""
    google_ai_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    token_encryption_key: str = ""
    oauth_state_secret: str = ""
    github_token: str = ""
    instagram_access_token: str = ""
    instagram_api_version: str = "v25.0"
    instagram_cache_ttl_seconds: int = 10800
    contact_rate_limit_per_10_min: int = 5
    max_github_repo_mb: int = 150
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]
    @property
    def admin_email_set(self):
        return {x.strip().lower() for x in self.admin_emails.split(",") if x.strip()}
    @property
    def primary_admin_email(self):
        items=[x.strip().lower() for x in self.admin_emails.split(",") if x.strip()]
        return items[0] if items else None
    @property
    def effective_gemini_api_key(self):
        return self.gemini_api_key or self.google_ai_api_key

@lru_cache
def get_settings(): return Settings()
