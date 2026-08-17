# BOOM Portfolio V4 — Production Final

Portfolio CMS สำหรับ **Developer + Photographer** ที่ยังคง Frontend เป็น Vanilla HTML/CSS/JavaScript ตามโจทย์เดิม

## Architecture

- Frontend: HTML + CSS + Vanilla JavaScript → **Vercel**
- Backend: Python + FastAPI → **Render (Docker)**
- Database/Auth: **Supabase PostgreSQL + Supabase Auth**
- Photography source: **Google Drive** (public API-key mode หรือ private OAuth read-only)
- Development source: **GitHub repository clone/analyzer**

## สิ่งที่ V4 มี

### Public website
- Home / Work / Project Detail / Photography / Album / About / Contact / 404
- Project tech stack, role, problem, solution, features, screenshots, Demo + GitHub links
- Photography masonry gallery + lazy loading + keyboard lightbox
- Contact form
- Dynamic Contact information จาก Admin
- Dynamic SEO title/description สำหรับ Project/Album
- Open Graph metadata
- `/sitemap.xml` และ `/robots.txt`
- Responsive mobile UI

### Admin CMS
- Supabase Auth email/password login
- จำกัดผู้ดูแลด้วย `ADMIN_EMAILS`
- Dashboard KPI
- Project CRUD + Draft / Published + Featured
- **GitHub: Paste URL → shallow clone → analyze → draft**
- GitHub Sync โดยอัปเดตข้อมูลเชิงเทคนิค แต่พยายามไม่ทับเนื้อหาที่แก้เอง
- ตรวจ `package.json`, `requirements.txt`, `pyproject.toml`, Docker, compose, ฯลฯ
- หา screenshots/images ภายใน repository และสร้าง raw GitHub URLs
- จำกัด repository size + clone timeout
- ไม่ execute code / ไม่ install dependencies จาก repository
- Google Drive import → เลือกรูป → Cover → Album
- Album photo drag-and-drop ordering
- Google OAuth Drive `readonly` สำหรับโฟลเดอร์ private
- Website Settings / Contact Settings
- Messages: read / archive / delete
- Audit log table

### Security / production
- Supabase JWT session validated by Backend
- Service-role key อยู่ Backend เท่านั้น
- Google OAuth token encrypted before Supabase storage
- Contact rate limit + honeypot anti-spam
- CORS allowlist
- Security headers
- Draft เป็นค่าเริ่มต้นสำหรับ Project/Album
- Backend image cache headers
- Docker image มี `git` แน่นอนสำหรับ GitHub importer
- Render health check `/api/health`

---

# 1) Supabase

สร้าง Supabase project แล้วเปิด **SQL Editor** และรัน:

```text
backend/database/schema.sql
```

ไฟล์เป็นแบบ upgrade-friendly และมี `ADD COLUMN IF NOT EXISTS` สำหรับอัปเกรดจาก V3

## สร้าง Admin User

Supabase Dashboard → Authentication → Users → Add user

สร้าง email/password สำหรับคุณ แล้วนำ email เดียวกันไปใส่ใน Render:

```env
ADMIN_EMAILS=your-email@example.com
```

ถ้ามีหลาย admin:

```env
ADMIN_EMAILS=one@example.com,two@example.com
```

> ไม่ต้องเปิด public signup บนเว็บไซต์ Portfolio

---

# 2) Backend local setup

```bash
cd backend
python -m venv venv
```

Windows:

```cmd
venv\Scripts\activate
```

Install:

```bash
pip install -r requirements.txt
```

Copy env:

```cmd
copy .env.example .env
```

หรือ macOS/Linux:

```bash
cp .env.example .env
```

Run:

```bash
uvicorn app.main:app --reload
```

API docs:

```text
http://127.0.0.1:8000/api/docs
```

---

# 3) Required environment variables on Render

```env
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
ADMIN_EMAILS=your-email@example.com
CORS_ORIGINS=https://YOUR-VERCEL-DOMAIN.vercel.app
FRONTEND_PUBLIC_URL=https://YOUR-VERCEL-DOMAIN.vercel.app
BACKEND_PUBLIC_URL=https://YOUR-RENDER-SERVICE.onrender.com
```

`SUPABASE_ANON_KEY` เป็น public client key แต่ `SUPABASE_SERVICE_ROLE_KEY` ห้ามใส่ใน frontend

---

# 4) Google Drive

มี 2 โหมด

## A. ง่ายที่สุด: Public/link-shared folder

Google Cloud → Enable Google Drive API → API Key

```env
GOOGLE_DRIVE_API_KEY=...
```

แล้วแชร์ Folder เป็น Viewer ตามสิทธิ์ที่ API key อ่านได้

## B. แนะนำสำหรับงานลูกค้า: Private Google Drive OAuth

Google Cloud Console:

1. Enable Google Drive API
2. OAuth consent screen
3. Create OAuth Client → Web application
4. Authorized redirect URI:

```text
https://YOUR-RENDER-SERVICE.onrender.com/api/integrations/google/callback
```

Render env:

```env
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=https://YOUR-RENDER-SERVICE.onrender.com/api/integrations/google/callback
TOKEN_ENCRYPTION_KEY=...
OAUTH_STATE_SECRET=...
```

Generate Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate OAuth state secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

หลัง Deploy → Admin → Integrations → **Connect Google Drive**

Scope ที่ระบบขอคือ read-only เท่านั้น

---

# 5) GitHub Project Import

Admin → Dev Projects → วาง:

```text
https://github.com/OWNER/REPOSITORY
```

กด **Clone & Analyze**

ระบบใช้:

```text
git clone --depth 1 --single-branch
```

clone ลง temporary directory, วิเคราะห์แล้ว directory ถูกลบทิ้งอัตโนมัติ

ระบบ **ไม่รัน** source code, npm scripts, Python scripts หรือ dependency installer ใด ๆ

Public repository ใช้ได้โดยไม่ต้องตั้ง token

ถ้าต้องการ private repo / API rate limit สูงขึ้น สามารถตั้ง server-only:

```env
GITHUB_TOKEN=...
```

ไม่ควรใช้ token ที่มีสิทธิ์เกินความจำเป็น

---

# 6) Deploy Backend → Render

โปรเจกต์มี `render.yaml` + `backend/Dockerfile`

Render จะ build Docker ซึ่งติดตั้ง `git` ให้ GitHub importer โดยตรง

Health check:

```text
/api/health
```

หลัง deploy จด URL เช่น:

```text
https://boom-portfolio-api.onrender.com
```

---

# 7) Deploy Frontend → Vercel

Vercel Project Settings:

```text
Root Directory = frontend
```

ก่อน deploy แก้ใน:

```text
frontend/vercel.json
```

เปลี่ยนทุกค่า:

```text
https://YOUR-RENDER-SERVICE.onrender.com
```

เป็น Render URL จริง

Vercel จะ proxy `/api/*` ไป Render ดังนั้น JavaScript ใช้ `/api/...` ได้เหมือน local

---

# 8) First production setup

หลัง deploy:

1. เปิด `/admin`
2. Login ด้วย Supabase Auth admin
3. Website → ใส่ชื่อ, headline, bio, public site URL, OG image
4. Contact → ใส่ email/phone/social/booking/status
5. Integrations → Connect Google Drive ถ้าต้องการ private Drive
6. Dev Projects → import GitHub repo → ตรวจข้อมูล → Save เป็น Draft → Publish
7. Photography → import Drive → เลือกรูป → Create Draft → ตรวจ → Publish
8. ทดสอบ Contact form
9. เปิด `/sitemap.xml` และ `/robots.txt`

---

# Environment checklist

Required:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
ADMIN_EMAILS
CORS_ORIGINS
FRONTEND_PUBLIC_URL
BACKEND_PUBLIC_URL
```

Drive ต้องเลือกอย่างน้อยหนึ่งแนวทาง:

```text
GOOGLE_DRIVE_API_KEY
```

หรือ OAuth:

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI
TOKEN_ENCRYPTION_KEY
OAUTH_STATE_SECRET
```

Optional:

```text
GITHUB_TOKEN
CONTACT_RATE_LIMIT_PER_10_MIN=5
MAX_GITHUB_REPO_MB=150
```

---

# Notes

- `service_role` และ OAuth secrets ห้าม commit
- `.env` อยู่ใน `.gitignore`
- ถ้าเปลี่ยน `TOKEN_ENCRYPTION_KEY` หลังเชื่อม Google แล้ว token เดิมจะถอดรหัสไม่ได้ ต้อง reconnect
- Render free/sleeping instances อาจมี cold start; frontend ยังโหลดได้ แต่ API request แรกอาจช้ากว่า
- Google Drive เหมาะกับ source gallery สำหรับ Portfolio ขนาดส่วนตัว แต่ถ้า traffic รูปสูงมากในอนาคต ควรย้ายภาพ derivative/thumbnail ไป object storage + CDN โดยยังเก็บ master ใน Drive ได้
