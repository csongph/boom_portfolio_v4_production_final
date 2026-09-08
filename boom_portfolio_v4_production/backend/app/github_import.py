import json, os, re, shutil, subprocess, tempfile
from pathlib import Path
from urllib.parse import urlparse
from fastapi import HTTPException
from .config import get_settings

MANIFEST_HINTS = {
    "requirements.txt": ["Python"], "pyproject.toml": ["Python"], "Pipfile": ["Python"],
    "package.json": ["JavaScript"], "tsconfig.json": ["TypeScript"],
    "Dockerfile": ["Docker"], "docker-compose.yml": ["Docker"], "docker-compose.yaml": ["Docker"],
    "go.mod": ["Go"], "Cargo.toml": ["Rust"], "pubspec.yaml": ["Flutter"],
}

def parse_repo_url(value: str):
    u = urlparse(value.strip())
    if u.scheme != "https" or u.netloc.lower() not in {"github.com", "www.github.com"}:
        raise HTTPException(400, "รองรับเฉพาะ https://github.com/OWNER/REPO")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2: raise HTTPException(400, "GitHub URL ไม่ถูกต้อง")
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise HTTPException(400, "GitHub URL ไม่ถูกต้อง")
    return owner, repo, f"https://github.com/{owner}/{repo}"

def _read(path: Path, limit=220_000):
    try: return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception: return ""

def _feature_lines(readme: str):
    lines=[]
    for line in readme.splitlines():
        x=re.sub(r"^[\s>*#\-+\d.)]+", "", line).strip()
        if 12 <= len(x) <= 180 and any(k in x.lower() for k in ["feature", "support", "ระบบ", "รองรับ", "สามารถ"]):
            lines.append(x)
    return lines[:8]

def _repo_images(root: Path, owner: str, repo: str, branch: str):
    out=[]
    for p in root.rglob("*"):
        if not p.is_file(): continue
        if p.suffix.lower() not in {".png",".jpg",".jpeg",".webp",".gif"}: continue
        rel=p.relative_to(root).as_posix()
        low=rel.lower()
        if any(k in low for k in ["screenshot","preview","demo","docs/","images/","assets/"]):
            out.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rel}")
        if len(out)>=12: break
    return out

def _clone_repo(normalized: str, dest: Path, token: str | None):
    """Clone public repositories without credentials, then retry private ones."""
    attempts=[None]
    if token: attempts.append(token)
    last_error=None
    for credential in attempts:
        shutil.rmtree(dest,ignore_errors=True)
        cmd=["git"]
        if credential:
            cmd += ["-c",f"http.extraHeader=Authorization: Bearer {credential}"]
        cmd += ["clone","--depth","1","--single-branch",normalized,str(dest)]
        env={**os.environ,"GIT_TERMINAL_PROMPT":"0"}
        try:
            subprocess.run(cmd,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,env=env)
            return
        except subprocess.TimeoutExpired:
            raise HTTPException(408,"GitHub clone timeout กรุณาลองใหม่")
        except subprocess.CalledProcessError as exc:
            last_error=exc
    detail="Clone repository ไม่สำเร็จ ตรวจสอบว่า URL ถูกต้องและ repository ยังเปิดใช้งานอยู่"
    if token:
        detail += " หากเป็น private repository ให้ตรวจสอบ GITHUB_TOKEN บน Render"
    raise HTTPException(400,detail) from last_error

def analyze_repo(repo_url: str):
    owner, repo, normalized = parse_repo_url(repo_url)
    if not shutil.which("git"): raise HTTPException(500, "git is not installed on backend")
    settings=get_settings()
    with tempfile.TemporaryDirectory(prefix="portfolio_repo_") as tmp:
        dest=Path(tmp)/"repo"
        _clone_repo(normalized,dest,settings.github_token)
        size=sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
        if size > settings.max_github_repo_mb*1024*1024:
            raise HTTPException(413, f"Repository ใหญ่เกิน {settings.max_github_repo_mb} MB")
        branch=subprocess.run(["git","-C",str(dest),"branch","--show-current"],capture_output=True,text=True).stdout.strip() or "main"
        readme=""
        for name in ["README.md","README.MD","readme.md","README.txt"]:
            if (dest/name).exists(): readme=_read(dest/name); break
        tech=[]
        for filename,hints in MANIFEST_HINTS.items():
            if (dest/filename).exists(): tech.extend(hints)
        pkg={}
        if (dest/"package.json").exists():
            try: pkg=json.loads(_read(dest/"package.json"))
            except Exception: pkg={}
            deps={**pkg.get("dependencies",{}),**pkg.get("devDependencies",{})}
            mapping={"react":"React","next":"Next.js","vue":"Vue","@angular/core":"Angular","vite":"Vite","tailwindcss":"Tailwind CSS","express":"Express"}
            for k,v in mapping.items():
                if k in deps: tech.append(v)
        req=_read(dest/"requirements.txt").lower()
        for k,v in {"fastapi":"FastAPI","django":"Django","flask":"Flask","supabase":"Supabase","psycopg":"PostgreSQL","redis":"Redis"}.items():
            if k in req: tech.append(v)
        compose=_read(dest/"docker-compose.yml")+_read(dest/"docker-compose.yaml")
        if "postgres" in compose.lower(): tech.append("PostgreSQL")
        if "redis" in compose.lower(): tech.append("Redis")
        title=(pkg.get("name") or repo).replace("-"," ").replace("_"," ").strip().title()
        first_para=""
        for para in re.split(r"\n\s*\n", readme):
            clean=re.sub(r"[#>*`\[\]()]", "", para).strip()
            if len(clean)>=30 and not clean.lower().startswith(("http","badge")):
                first_para=re.sub(r"\s+"," ",clean)[:600]; break
        shots=_repo_images(dest,owner,repo,branch)
        return {
            "title": title, "slug": repo.lower(), "summary": first_para[:220], "description": first_para,
            "source_url": normalized, "demo_url": pkg.get("homepage") or None,
            "tech_stack": list(dict.fromkeys(tech)), "features": _feature_lines(readme),
            "screenshots": shots, "cover_url": shots[0] if shots else None,
            "github_owner": owner, "github_repo": repo, "github_branch": branch,
            "readme_excerpt": readme[:5000], "file_count": sum(1 for p in dest.rglob("*") if p.is_file()),
            "repo_size_mb": round(size/1024/1024,2)
        }
