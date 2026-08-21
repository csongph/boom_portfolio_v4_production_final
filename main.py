import os
import sqlite3
import secrets
import shutil
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent

IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

if IS_VERCEL:
    DB_PATH = Path("/tmp/portfolio.db")
    UPLOAD_DIR = Path("/tmp/uploads")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    SEED_DB = BASE_DIR / "portfolio.db"
    if SEED_DB.exists() and not DB_PATH.exists():
        try:
            shutil.copy(SEED_DB, DB_PATH)
        except Exception:
            pass
else:
    DB_PATH = BASE_DIR / "portfolio.db"
    UPLOAD_DIR = BASE_DIR / "static" / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme").strip()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "verve_static_session_secret_key_2026").strip()
SITE_TITLE = os.environ.get("SITE_TITLE", "Verve")
SITE_TAGLINE = os.environ.get("SITE_TAGLINE", "Your images. Your story.")
INSTAGRAM_HANDLE = os.environ.get("INSTAGRAM_HANDLE", "verve_stories")

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB

app = FastAPI(title=SITE_TITLE)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=False,
    max_age=86400 * 30
)

if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_db():
    """Returns a SQLite connection with Foreign Keys and Row Factory enabled."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass
    try:
        yield conn
    finally:
        conn.close()


def get_session_id(request: Request) -> str:
    if "user_sid" not in request.session:
        request.session["user_sid"] = secrets.token_hex(16)
    return request.session["user_sid"]


def is_admin_logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def init_db():
    """Initializes SQLite database with indexes."""
    conn = sqlite3.connect(DB_PATH)
    if not IS_VERCEL:
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except Exception:
            pass
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass
        
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            avatar TEXT NOT NULL DEFAULT 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80',
            bio TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            followers_count INTEGER NOT NULL DEFAULT 0,
            following_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, title TEXT, caption TEXT, tags TEXT, sort_order INTEGER, uploaded_at TEXT)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL DEFAULT 'alex_visuals',
            user_avatar TEXT NOT NULL DEFAULT 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80',
            title TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            layout_style TEXT NOT NULL DEFAULT 'clean',
            location TEXT NOT NULL DEFAULT '',
            likes_count INTEGER NOT NULL DEFAULT 0,
            saves_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE SET DEFAULT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS post_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            aspect_ratio TEXT NOT NULL DEFAULT '4:5',
            crop_mode TEXT NOT NULL DEFAULT 'center',
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            user_avatar TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(post_id, session_id),
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            collection_name TEXT NOT NULL DEFAULT 'Favorites',
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(post_id, session_id),
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            cover_image TEXT NOT NULL DEFAULT ''
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            actor_avatar TEXT NOT NULL,
            post_id INTEGER DEFAULT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_username ON posts(username);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_post_images_post_id ON post_images(post_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_likes_post_session ON likes(post_id, session_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_saves_post_session ON saves(post_id, session_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity(created_at DESC);")

    conn.commit()

    default_collections = [
        ("Favorites", "Handpicked moments worth remembering", ""),
        ("Inspiration", "Architectural shapes, light, and modern lines", ""),
        ("Travel", "Wanderlust reflections and cityscapes", ""),
        ("Photography", "Technical studies & light experiments", ""),
        ("Ideas", "Mood boards and creative concepts", "")
    ]
    for col_name, desc, cover in default_collections:
        cur.execute("INSERT OR IGNORE INTO collections (name, description, cover_image) VALUES (?, ?, ?)", (col_name, desc, cover))

    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT OR IGNORE INTO users (username, display_name, avatar, bio, website, followers_count, following_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "alex_visuals",
        "Alex Vance",
        "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80",
        "Visual creator & storyteller. Sharing quiet light and simple moments.",
        "alexvance.design",
        0,
        0,
        now
    ))

    conn.commit()
    conn.close()


init_db()


def require_login(request: Request):
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return True


def format_time_ago(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.utcnow()
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        elif seconds < 604800:
            return f"{int(seconds // 86400)}d ago"
        else:
            return dt.strftime("%b %d")
    except Exception:
        return "recently"


def fetch_full_posts(db: sqlite3.Connection, session_id: str, where_clause: str = "", params: tuple = ()) -> List[dict]:
    query = f"SELECT * FROM posts {where_clause} ORDER BY id DESC"
    post_rows = db.execute(query, params).fetchall()

    posts = []
    for row in post_rows:
        p = dict(row)
        pid = p["id"]

        img_rows = db.execute("SELECT * FROM post_images WHERE post_id = ? ORDER BY sort_order ASC", (pid,)).fetchall()
        p["images"] = [dict(img) for img in img_rows]

        comment_rows = db.execute("SELECT * FROM comments WHERE post_id = ? ORDER BY id ASC", (pid,)).fetchall()
        comments = []
        for c in comment_rows:
            cd = dict(c)
            cd["time_ago"] = format_time_ago(cd["created_at"])
            comments.append(cd)
        p["comments"] = comments

        liked = db.execute("SELECT 1 FROM likes WHERE post_id = ? AND session_id = ?", (pid, session_id)).fetchone()
        p["is_liked"] = bool(liked)

        saved = db.execute("SELECT 1 FROM saves WHERE post_id = ? AND session_id = ?", (pid, session_id)).fetchone()
        p["is_saved"] = bool(saved)

        p["time_ago"] = format_time_ago(p["created_at"])
        posts.append(p)

    return posts


# ---------- Navigation Views ----------

@app.get("/", response_class=HTMLResponse)
def home_feed(request: Request, db: sqlite3.Connection = Depends(get_db)):
    sid = get_session_id(request)
    posts = fetch_full_posts(db, sid)
    is_owner = is_admin_logged_in(request)

    user_row = db.execute("SELECT * FROM users WHERE username = 'alex_visuals'").fetchone()
    current_user = dict(user_row) if user_row else {
        "username": "alex_visuals",
        "display_name": "Alex Vance",
        "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80",
        "bio": "Visual creator & storyteller.",
        "followers_count": 0,
        "following_count": 0
    }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "posts": posts,
            "current_user": current_user,
            "is_owner": is_owner,
            "site_title": SITE_TITLE,
            "site_tagline": SITE_TAGLINE,
            "active_page": "home"
        },
    )


@app.get("/explore", response_class=HTMLResponse)
def explore_page(
    request: Request,
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db)
):
    sid = get_session_id(request)
    is_owner = is_admin_logged_in(request)
    
    where = ""
    params = []
    if category and category.lower() != "all":
        where = "WHERE tags LIKE ?"
        params.append(f"%{category.lower()}%")
    elif q:
        where = "WHERE title LIKE ? OR caption LIKE ? OR tags LIKE ? OR username LIKE ?"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])

    posts = fetch_full_posts(db, sid, where, tuple(params))
    categories = ["All", "Trending", "Minimal", "Architecture", "Editorial", "Landscape", "Portrait", "Lifestyle"]

    return templates.TemplateResponse(
        "explore.html",
        {
            "request": request,
            "posts": posts,
            "categories": categories,
            "selected_category": category or "All",
            "search_query": q or "",
            "is_owner": is_owner,
            "site_title": SITE_TITLE,
            "active_page": "explore"
        }
    )


@app.get("/collections", response_class=HTMLResponse)
def collections_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    sid = get_session_id(request)
    is_owner = is_admin_logged_in(request)

    saved_rows = db.execute("""
        SELECT p.* FROM posts p
        JOIN saves s ON p.id = s.post_id
        WHERE s.session_id = ?
        ORDER BY s.id DESC
    """, (sid,)).fetchall()

    saved_posts = []
    for r in saved_rows:
        pid = r["id"]
        img = db.execute("SELECT filename FROM post_images WHERE post_id = ? ORDER BY sort_order ASC LIMIT 1", (pid,)).fetchone()
        saved_posts.append({
            **dict(r),
            "cover_image": img["filename"] if img else ""
        })

    collections_rows = db.execute("SELECT * FROM collections ORDER BY id ASC").fetchall()
    collections = [dict(c) for c in collections_rows]

    return templates.TemplateResponse(
        "collections.html",
        {
            "request": request,
            "saved_posts": saved_posts,
            "collections": collections,
            "is_owner": is_owner,
            "site_title": SITE_TITLE,
            "active_page": "collections"
        }
    )


@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    is_owner = is_admin_logged_in(request)
    rows = db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 50").fetchall()
    activities = []
    for r in rows:
        act = dict(r)
        act["time_ago"] = format_time_ago(act["created_at"])
        activities.append(act)

    return templates.TemplateResponse(
        "activity.html",
        {
            "request": request,
            "activities": activities,
            "is_owner": is_owner,
            "site_title": SITE_TITLE,
            "active_page": "activity"
        }
    )


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    sid = get_session_id(request)
    is_owner = is_admin_logged_in(request)
    posts = fetch_full_posts(db, sid, "WHERE username = 'alex_visuals'")

    user_row = db.execute("SELECT * FROM users WHERE username = 'alex_visuals'").fetchone()
    user_info = dict(user_row) if user_row else {
        "username": "alex_visuals",
        "display_name": "Alex Vance",
        "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80",
        "bio": "Visual creator & storyteller.",
        "website": "alexvance.design",
        "followers_count": 0,
        "following_count": 0
    }
    user_info["posts_count"] = len(posts)
    user_info["is_self"] = True

    saved_rows = db.execute("""
        SELECT p.* FROM posts p
        JOIN saves s ON p.id = s.post_id
        WHERE s.session_id = ?
        ORDER BY s.id DESC
    """, (sid,)).fetchall()

    saved_posts = []
    for r in saved_rows:
        pid = r["id"]
        img = db.execute("SELECT filename FROM post_images WHERE post_id = ? ORDER BY sort_order ASC LIMIT 1", (pid,)).fetchone()
        saved_posts.append({
            **dict(r),
            "cover_image": img["filename"] if img else ""
        })

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user_info": user_info,
            "posts": posts,
            "saved_posts": saved_posts,
            "is_owner": is_owner,
            "site_title": SITE_TITLE,
            "active_page": "profile"
        }
    )


@app.get("/user/{username}", response_class=HTMLResponse)
def public_user_profile(username: str, request: Request, db: sqlite3.Connection = Depends(get_db)):
    sid = get_session_id(request)
    is_owner = is_admin_logged_in(request)
    posts = fetch_full_posts(db, sid, "WHERE username = ?", (username,))

    user_row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    user_info = dict(user_row) if user_row else {
        "username": username,
        "display_name": username.replace("_", " ").title(),
        "avatar": posts[0]["user_avatar"] if posts else "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80",
        "bio": "Visual creator sharing captured light and curated stories.",
        "website": "",
        "followers_count": 0,
        "following_count": 0
    }
    user_info["posts_count"] = len(posts)
    user_info["is_self"] = (username == "alex_visuals" and is_owner)

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user_info": user_info,
            "posts": posts,
            "saved_posts": [],
            "is_owner": is_owner,
            "site_title": SITE_TITLE,
            "active_page": "profile"
        }
    )


# ---------- Interactive Social APIs ----------

@app.post("/api/posts/{post_id}/like")
def toggle_like(post_id: int, request: Request, db: sqlite3.Connection = Depends(get_db)):
    sid = get_session_id(request)
    existing = db.execute("SELECT 1 FROM likes WHERE post_id = ? AND session_id = ?", (post_id, sid)).fetchone()

    if existing:
        db.execute("DELETE FROM likes WHERE post_id = ? AND session_id = ?", (post_id, sid))
        db.execute("UPDATE posts SET likes_count = MAX(0, likes_count - 1) WHERE id = ?", (post_id,))
        liked = False
    else:
        now = datetime.utcnow().isoformat()
        db.execute("INSERT INTO likes (post_id, session_id, created_at) VALUES (?, ?, ?)", (post_id, sid, now))
        db.execute("UPDATE posts SET likes_count = likes_count + 1 WHERE id = ?", (post_id,))
        liked = True

        post = db.execute("SELECT title FROM posts WHERE id = ?", (post_id,)).fetchone()
        title_str = post["title"] if post and post["title"] else "your post"
        db.execute("""
            INSERT INTO activity (type, actor_name, actor_avatar, post_id, message, created_at)
            VALUES ('like', 'visitor', 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=100&q=80', ?, ?, ?)
        """, (post_id, f"appreciated '{title_str}'", now))

    db.commit()
    new_count = db.execute("SELECT likes_count FROM posts WHERE id = ?", (post_id,)).fetchone()["likes_count"]
    return JSONResponse({"liked": liked, "likes_count": new_count})


@app.post("/api/posts/{post_id}/save")
def toggle_save(post_id: int, request: Request, collection: str = Form("Favorites"), db: sqlite3.Connection = Depends(get_db)):
    sid = get_session_id(request)
    existing = db.execute("SELECT 1 FROM saves WHERE post_id = ? AND session_id = ?", (post_id, sid)).fetchone()

    if existing:
        db.execute("DELETE FROM saves WHERE post_id = ? AND session_id = ?", (post_id, sid))
        db.execute("UPDATE posts SET saves_count = MAX(0, saves_count - 1) WHERE id = ?", (post_id,))
        saved = False
    else:
        now = datetime.utcnow().isoformat()
        db.execute("INSERT INTO saves (post_id, collection_name, session_id, created_at) VALUES (?, ?, ?, ?)", (post_id, collection, sid, now))
        db.execute("UPDATE posts SET saves_count = saves_count + 1 WHERE id = ?", (post_id,))
        saved = True

    db.commit()
    new_count = db.execute("SELECT saves_count FROM posts WHERE id = ?", (post_id,)).fetchone()["saves_count"]
    return JSONResponse({"saved": saved, "saves_count": new_count})


@app.post("/api/posts/{post_id}/comment")
def add_comment(post_id: int, request: Request, text: str = Form(...), db: sqlite3.Connection = Depends(get_db)):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Comment text cannot be empty")

    now = datetime.utcnow().isoformat()
    username = "visitor"
    avatar = "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?auto=format&fit=crop&w=100&q=80"

    db.execute("""
        INSERT INTO comments (post_id, username, user_avatar, text, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (post_id, username, avatar, text.strip(), now))

    comment_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    return JSONResponse({
        "success": True,
        "comment": {
            "id": comment_id,
            "username": username,
            "user_avatar": avatar,
            "text": text.strip(),
            "time_ago": "just now"
        }
    })


# ---------- Smart Auto Arrange Layout API ----------

@app.post("/api/smart-arrange")
async def analyze_smart_layout(files_count: int = Form(...)):
    if files_count <= 1:
        recommended = "clean"
        reason = "Clean minimalist frame ideal for single hero photos."
    elif files_count in (2, 3):
        recommended = "editorial"
        reason = "Editorial magazine layout creates a powerful side-by-side story."
    elif files_count == 4:
        recommended = "collage"
        reason = "Balanced 2x2 grid highlights all 4 angles perfectly."
    else:
        recommended = "masonry"
        reason = "Dynamic staggered masonry flow displays rich collections."

    styles = [
        {"id": "clean", "name": "Clean", "desc": "Minimal single/stacked focus"},
        {"id": "editorial", "name": "Editorial", "desc": "Magazine-style split hero layout"},
        {"id": "collage", "name": "Collage", "desc": "Balanced multi-image grid"},
        {"id": "masonry", "name": "Masonry", "desc": "Staggered gallery flow"},
        {"id": "story", "name": "Story", "desc": "Vertical sequential flow"}
    ]

    return JSONResponse({
        "recommended_layout": recommended,
        "reason": reason,
        "message_en": "We found a layout for your story.",
        "message_th": "เราเจอองค์ประกอบที่เหมาะกับรูปของคุณแล้ว",
        "available_styles": styles
    })


# ---------- Create Post Endpoint ----------

@app.post("/api/posts/create")
async def create_post(
    request: Request,
    title: str = Form(""),
    caption: str = Form(""),
    tags: str = Form(""),
    location: str = Form(""),
    layout_style: str = Form("clean"),
    aspect_ratio: str = Form("4:5"),
    files: List[UploadFile] = File(...),
    db: sqlite3.Connection = Depends(get_db)
):
    if not files or files[0].filename == "":
        raise HTTPException(status_code=400, detail="Please select at least one image.")

    saved_images = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            continue
        contents = await f.read()
        if len(contents) > MAX_UPLOAD_BYTES:
            continue

        safe_name = f"{secrets.token_hex(8)}{ext}"
        dest = UPLOAD_DIR / safe_name
        with open(dest, "wb") as out:
            out.write(contents)
        saved_images.append(f"/static/uploads/{safe_name}")

    if not saved_images:
        raise HTTPException(status_code=400, detail="No valid images were uploaded.")

    now = datetime.utcnow().isoformat()
    db.execute("""
        INSERT INTO posts (username, user_avatar, title, caption, tags, layout_style, location, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("alex_visuals", "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80", title.strip(), caption.strip(), tags.strip(), layout_style, location.strip(), now))

    post_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    for idx, img_path in enumerate(saved_images):
        db.execute("""
            INSERT INTO post_images (post_id, filename, sort_order, aspect_ratio)
            VALUES (?, ?, ?, ?)
        """, (post_id, img_path, idx, aspect_ratio))

    db.commit()
    return JSONResponse({"success": True, "post_id": post_id, "redirect_url": "/"})


# ---------- Legacy Admin Panel & Auth ----------

@app.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = None):
    if request.session.get("logged_in"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": error, "site_title": SITE_TITLE, "is_owner": False})


@app.post("/admin/login")
def login_submit(
    request: Request,
    password: str = Form(...),
    email: Optional[str] = Form(""),
    db: sqlite3.Connection = Depends(get_db)
):
    clean_pw = password.strip() if password else ""
    if secrets.compare_digest(clean_pw, ADMIN_PASSWORD):
        request.session["logged_in"] = True
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/admin/login?error=1", status_code=303)


@app.get("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, db: sqlite3.Connection = Depends(get_db), _: bool = Depends(require_login)):
    sid = get_session_id(request)
    posts = fetch_full_posts(db, sid)
    return templates.TemplateResponse("admin.html", {"request": request, "photos": posts, "site_title": SITE_TITLE, "is_owner": True, "error": None})


@app.post("/admin/delete/{post_id}")
def delete_post(post_id: int, db: sqlite3.Connection = Depends(get_db), _: bool = Depends(require_login)):
    imgs = db.execute("SELECT filename FROM post_images WHERE post_id = ?", (post_id,)).fetchall()
    for img in imgs:
        fname = img["filename"]
        if fname.startswith("/static/uploads/"):
            file_path = BASE_DIR / fname.lstrip("/")
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass

    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/upload")
async def admin_upload_frame(
    request: Request,
    title: str = Form(""),
    caption: str = Form(""),
    tags: str = Form(""),
    file: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
    _: bool = Depends(require_login),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        posts = fetch_full_posts(db, get_session_id(request))
        return templates.TemplateResponse("admin.html", {"request": request, "photos": posts, "site_title": SITE_TITLE, "is_owner": True, "error": f"Unsupported file type '{ext}'."})

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        posts = fetch_full_posts(db, get_session_id(request))
        return templates.TemplateResponse("admin.html", {"request": request, "photos": posts, "site_title": SITE_TITLE, "is_owner": True, "error": "File too large. Max 15 MB."})

    safe_name = f"{secrets.token_hex(8)}{ext}"
    dest = UPLOAD_DIR / safe_name
    with open(dest, "wb") as out:
        out.write(contents)

    now = datetime.utcnow().isoformat()
    db.execute("""
        INSERT INTO posts (username, user_avatar, title, caption, tags, layout_style, location, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("alex_visuals", "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80", title.strip(), caption.strip(), tags.strip(), "clean", "", now))

    post_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.execute("""
        INSERT INTO post_images (post_id, filename, sort_order, aspect_ratio)
        VALUES (?, ?, ?, ?)
    """, (post_id, f"/static/uploads/{safe_name}", 0, "4:5"))

    db.commit()
    return RedirectResponse("/admin", status_code=303)
