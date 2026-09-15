# BOOM Portfolio V4

Personal portfolio and CMS for **Developer × Photographer**.

## Architecture

- Frontend: Vanilla HTML / CSS / JavaScript → Vercel
- Backend: FastAPI / Python → Render
- Database & Auth: Supabase PostgreSQL + Supabase Auth
- Photography source: Google Drive
- Development source: GitHub repository analyzer

## Public experience

The public site is intentionally simple: visitors discover coding work and photography, learn about BOOM, then contact directly through the configured contact/social channels.

Pages:
- Home
- Coding projects + project details
- Photography + album/lightbox
- About
- Contact
- Photography digital card (`/wallet`)
- Link hub (`/boom-links`)

The UI uses a shared dark editorial system with warm-gold accents across desktop and mobile.

## Admin CMS

Admin is protected by Supabase Auth and `ADMIN_EMAILS`.

Main tools:
- Dashboard and photography analytics
- Project CRUD, featured/published state and GitHub analysis
- Google Drive album import
- Album photo ordering and alt text
- Website / SEO settings
- Contact and social settings
- Google Drive OAuth integration
- Gemini-assisted portfolio copy (optional)

## Backend setup

```bash
cd backend
python -m venv venv
```

Windows:

```cmd
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.bootstrap:app --reload
```

macOS/Linux:

```bash
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.bootstrap:app --reload
```

API docs:

```text
http://127.0.0.1:8000/api/docs
```

## Core Render environment

Configure Supabase, the public frontend/backend URLs, CORS and the admin allow-list. Google Drive can use either an API key for supported public folders or OAuth for private folders. Optional integrations include Gemini, GitHub token metadata access, Instagram Graph API and Redis.

Keep service-role keys, OAuth credentials and other private values on the backend only. Never place them in the frontend repository or browser JavaScript.

## Google Drive OAuth

Google OAuth is used only for read-only Drive access.

Enable Google Drive API, create a Web OAuth client, then set the callback to:

```text
https://YOUR-RENDER-SERVICE.onrender.com/api/integrations/google/callback
```

After deploy, open Admin → Integrations → Connect Google Drive.

## Supabase

For a fresh install run:

```text
backend/database/schema.sql
```

For an existing installation that previously used retired features, run the applicable cleanup migration in `backend/database/migrations/` after reviewing it.

## Deploy

Frontend Vercel root directory:

```text
boom_portfolio_v4_production/frontend
```

Backend Render service uses:

```text
web: uvicorn app.bootstrap:app --host 0.0.0.0 --port $PORT
```

Health check:

```text
/api/health
```

`frontend/vercel.json` proxies `/api/*` and dynamic project/photography routes to Render.

## Notes

- Published photography routes expose only published albums/photos.
- Google OAuth tokens are encrypted before storage.
- Contact endpoints use rate limiting / anti-spam safeguards.
- Responsive images, lazy loading, caching and reduced-motion support are used to keep the public site lightweight.
