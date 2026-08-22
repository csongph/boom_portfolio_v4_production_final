from datetime import date
from pydantic import BaseModel, EmailStr, Field, HttpUrl

class DriveImport(BaseModel): folder_url: str

class AlbumIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    slug: str | None = None
    description: str | None = None
    category: str = "Photography"
    event_date: date | None = None
    drive_folder_url: str
    drive_folder_id: str
    selected_file_ids: list[str] = Field(min_length=1)
    cover_drive_file_id: str | None = None
    is_published: bool = True
    seo_title: str | None = None
    seo_description: str | None = None

class AlbumUpdate(BaseModel):
    title: str
    slug: str | None = None
    description: str | None = None
    category: str = "Photography"
    event_date: date | None = None
    cover_drive_file_id: str | None = None
    is_published: bool = False
    seo_title: str | None = None
    seo_description: str | None = None

class PhotoOrderIn(BaseModel): photo_ids: list[str]

class ProjectIn(BaseModel):
    title: str
    slug: str | None = None
    summary: str | None = None
    description: str | None = None
    cover_url: str | None = None
    demo_url: str | None = None
    source_url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    role: str | None = None
    problem: str | None = None
    solution: str | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    github_branch: str | None = None
    github_last_synced_at: str | None = None
    sort_order: int = 0
    is_featured: bool = False
    is_published: bool = False
    seo_title: str | None = None
    seo_description: str | None = None

class GitHubImportIn(BaseModel): repo_url: str
class GitHubCreateIn(BaseModel): repo_url: str; publish: bool = False

class AIContentIn(BaseModel):
    kind: str = Field(pattern="^(project|album|site|contact)$")
    tone: str = "professional"
    language: str = "th"
    context: dict = Field(default_factory=dict)

class SettingsIn(BaseModel):
    display_name: str = "BOOM"
    headline: str = "Developer & Photographer"
    bio: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    github_url: str | None = None
    instagram_url: str | None = None
    facebook_url: str | None = None
    linkedin_url: str | None = None
    line_url: str | None = None
    youtube_url: str | None = None
    booking_url: str | None = None
    contact_heading: str = "Let's work together"
    contact_intro: str | None = None
    availability_text: str | None = None
    contact_button_text: str = "Send Message"
    show_contact_form: bool = True
    hero_image_url: str | None = None
    site_url: str | None = None
    default_og_image_url: str | None = None
    analytics_id: str | None = None

class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    subject: str | None = Field(default=None, max_length=180)
    message: str = Field(min_length=1, max_length=5000)
    website: str | None = Field(default=None, max_length=300)  # honeypot
