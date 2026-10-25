import os, uuid, shutil, subprocess, json, time
from pathlib import Path
from typing import Optional
import requests
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl, Field

BASE = Path(__file__).resolve().parent.parent
STATIC = BASE / "app" / "static"
WORK = Path(os.getenv("WORK_DIR", "/tmp/viralforge"))
WORK.mkdir(parents=True, exist_ok=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "viralforge-clips")
MAX_CLIPS = int(os.getenv("MAX_CLIPS_PER_JOB", "5"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))

app = FastAPI(title="ViralForge AI", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

class ClipRequest(BaseModel):
    video_url: HttpUrl
    clip_count: int = Field(default=3, ge=1, le=5)
    clip_length: int = Field(default=30, ge=15, le=90)
    aspect_ratio: str = Field(default="9:16")
    captions: bool = False

def require_supabase():
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(503, "Account service is not configured. Add SUPABASE_URL and SUPABASE_ANON_KEY in Render Environment.")

def current_user(authorization: Optional[str]):
    require_supabase()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        r = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
            timeout=15
        )
        if r.status_code != 200:
            raise HTTPException(401, "Your session expired. Sign in again.")
        return r.json()
    except requests.RequestException:
        raise HTTPException(503, "Could not contact account service. Try again.")

def run_cmd(args, timeout=900):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Video processing timed out. Try a shorter video.")
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "Unknown processing error")[-1800:]
        raise HTTPException(422, "Video processing failed: " + msg)
    return p

def has_tool(name):
    return shutil.which(name) is not None

def upload_to_storage(local_path: Path, object_name: str):
    # Optional permanent cloud storage. Uses service role key only on the server.
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None
    try:
        data = local_path.read_bytes()
        r = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{object_name}",
            headers={
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Content-Type": "video/mp4",
                "x-upsert": "true"
            },
            data=data, timeout=180
        )
        if r.status_code not in (200, 201):
            print("Storage upload failed:", r.status_code, r.text[:500])
            return None
        return f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{object_name}"
    except Exception as e:
        print("Storage upload error:", str(e))
        return None

@app.get("/", response_class=HTMLResponse)
def home():
    index = STATIC / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>ViralForge AI setup error</h1><p>Missing app/static/index.html</p>", status_code=500)
    return FileResponse(index)

@app.get("/health")
def health():
    return {
        "ok": True,
        "ffmpeg": has_tool("ffmpeg"),
        "yt_dlp": has_tool("yt-dlp"),
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
        "storage_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
    }

@app.get("/api/me")
def me(authorization: Optional[str] = Header(default=None)):
    user = current_user(authorization)
    return {"id": user.get("id"), "email": user.get("email")}

@app.post("/api/jobs")
def create_job(payload: ClipRequest, authorization: Optional[str] = Header(default=None)):
    user = current_user(authorization)
    if not has_tool("ffmpeg"):
        raise HTTPException(503, "FFmpeg is missing from the server image.")
    if not has_tool("yt-dlp"):
        raise HTTPException(503, "yt-dlp is missing from the server image.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    source = job_dir / "source.mp4"
    try:
        # Download a source video; yt-dlp handles supported URLs and merges formats using ffmpeg.
        run_cmd([
            "yt-dlp", "--no-playlist", "--no-warnings",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
            "--merge-output-format", "mp4", "-o", str(source),
            str(payload.video_url)
        ], timeout=1200)
        if not source.exists():
            candidates = list(job_dir.glob("source.*"))
            if not candidates:
                candidates = [p for p in job_dir.iterdir() if p.is_file()]
            if not candidates:
                raise HTTPException(422, "Could not download the video. Check the link and permissions.")
            source = candidates[0]
        probe = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)])
        duration = float((probe.stdout or "0").strip() or 0)
        if duration < 1:
            raise HTTPException(422, "Could not read video duration.")
        # Starter uses evenly spaced sample points. True AI viral-moment ranking requires a separate AI model/service.
        count = min(payload.clip_count, MAX_CLIPS)
        length = min(payload.clip_length, 90)
        span = max(0, duration - length)
        if count == 1:
            starts = [max(0, span / 2)]
        else:
            starts = [span * i / (count - 1) for i in range(count)]
        clips = []
        for i, start in enumerate(starts, 1):
            out = job_dir / f"clip_{i}.mp4"
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
            if payload.aspect_ratio == "1:1":
                vf = "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
            elif payload.aspect_ratio == "16:9":
                vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
            run_cmd([
                "ffmpeg", "-y", "-ss", str(start), "-i", str(source),
                "-t", str(min(length, max(1, duration-start))),
                "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(out)
            ], timeout=900)
            cloud_url = upload_to_storage(out, f"{user.get('id')}/{job_id}/{out.name}")
            clips.append({
                "name": out.name,
                "download_url": f"/api/download/{job_id}/{out.name}",
                "storage_url": cloud_url,
                "start_seconds": round(start, 1),
                "duration_seconds": min(length, max(1, duration-start))
            })
        return {"job_id": job_id, "clips": clips, "note": "Clips were sampled across the video. AI viral-moment ranking and caption rendering are not enabled in this starter."}
    except HTTPException:
        raise
    except Exception as e:
        print("Job failed:", repr(e))
        raise HTTPException(500, "Clip generation failed. Check the video link and try again.")
    finally:
        # Keep output files for current-instance downloads. Render ephemeral disk may clear on restart.
        pass

@app.get("/api/download/{job_id}/{filename}")
def download_clip(job_id: str, filename: str):
    if "/" in job_id or "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid file path.")
    path = WORK / job_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "This clip is no longer on this server. Enable Supabase Storage for permanent storage.")
    return FileResponse(path, media_type="video/mp4", filename=filename)
