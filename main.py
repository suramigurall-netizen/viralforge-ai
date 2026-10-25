import os, re, uuid, shutil, subprocess, json, time
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import yt_dlp
from supabase import create_client, Client
import stripe

BASE=Path(__file__).resolve().parent
WORK=Path(os.getenv("WORK_DIR","/tmp/clipgenius_jobs")); WORK.mkdir(parents=True,exist_ok=True)
SUPABASE_URL=os.getenv("SUPABASE_URL",""); SUPABASE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
supabase: Optional[Client]=create_client(SUPABASE_URL,SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
stripe.api_key=os.getenv("STRIPE_SECRET_KEY","")
app=FastAPI(title="ViralForge Ultimate",version="3.0.0")
app.mount("/static", StaticFiles(directory=str(BASE/"app"/"static")), name="static")
class JobRequest(BaseModel):
    video_url:str
    authorized:bool=False
    clip_count:int=Field(3,ge=1,le=10)
    clip_length:int=Field(30,ge=5,le=180)
    aspect_ratio:str="9:16"
    crop_mode:str="fill"
    strategy:str="distributed"
    quality:str="standard"
    caption_style:str="pop"
    captions:bool=False
    start_at:int=Field(0,ge=0)
    end_at:Optional[int]=Field(None,ge=1)

def run_cmd(args,timeout=1800):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or "Media processing failed")[-2000:])
    return p.stdout
def youtube_url(url):
    try:
        p=urlparse(url.strip()); host=(p.netloc or "").lower().split(":")[0]
        return host in {"youtube.com","www.youtube.com","m.youtube.com","youtu.be"} and (p.path=="/watch" or p.path.startswith(("/shorts/","/live/")) or host=="youtu.be")
    except Exception:return False
def auth_user(token):
    if not supabase: raise HTTPException(503,"Account service not configured. Add Supabase environment variables.")
    if not token: raise HTTPException(401,"Sign in first.")
    try:
        result=supabase.auth.get_user(token)
        if not result or not result.user: raise ValueError("No user")
        return result.user
    except Exception: raise HTTPException(401,"Session expired. Please sign in again.")
def tier_for(user_id):
    if not supabase: return "free"
    try:
        r=supabase.table("profiles").select("plan").eq("id",user_id).maybe_single().execute()
        return (r.data or {}).get("plan","free")
    except Exception:return "free"
def ensure_plan(user,need=1):
    plan=tier_for(user.id); limits={"free":2,"creator":30,"pro":150}
    if plan not in limits: plan="free"
    if need>limits[plan]: raise HTTPException(402,f"{plan.title()} plan limit is {limits[plan]} clips per job. Upgrade your plan.")
    return plan
def dims(ratio):
    return {"9:16":(1080,1920),"1:1":(1080,1080),"4:5":(1080,1350),"16:9":(1920,1080)}.get(ratio,(1080,1920))
def filter_for(ratio,mode):
    w,h=dims(ratio)
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black" if mode=="fit" else f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
def probe(p):
    s=run_cmd(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(p)],60)
    return max(0,int(float(s.strip())))
def generate(source,folder,opts):
    duration=probe(source)
    if duration<5: raise ValueError("Video is too short or unreadable.")
    start=min(opts["start_at"],duration-1); end=min(opts["end_at"] or duration,duration)
    if end<=start: raise ValueError("End boundary must be after start boundary.")
    avail=end-start; length=min(opts["clip_length"],avail); max_start=max(0,avail-length); n=opts["clip_count"]
    if opts["strategy"]=="opening": starts=[start]*n
    elif opts["strategy"]=="center": starts=[start+max_start//2]*n
    else: starts=[start+int(max_start*i/max(1,n-1)) for i in range(n)]
    w,h=dims(opts["aspect_ratio"]); crf={"high":"18","standard":"23","compact":"28"}.get(opts["quality"],"23")
    outputs=[]
    for i,pos in enumerate(starts,1):
        out=folder/f"clip_{i:02d}.mp4"
        run_cmd(["ffmpeg","-y","-ss",str(pos),"-i",str(source),"-t",str(length),"-vf",filter_for(opts["aspect_ratio"],opts["crop_mode"]),"-c:v","libx264","-preset","veryfast","-crf",crf,"-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-movflags","+faststart",str(out)])
        outputs.append({"name":out.name,"url":f"/api/jobs/{folder.name}/clips/{out.name}","start_seconds":pos,"duration_seconds":length,"size_bytes":out.stat().st_size})
    return duration,outputs
def persist_job(user,job_id,title,clips):
    if not supabase:return
    try:
        supabase.table("jobs").insert({"id":job_id,"user_id":user.id,"title":title,"clips":clips,"created_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}).execute()
    except Exception as e: print("job metadata save failed:",str(e))
@app.get("/",response_class=HTMLResponse)
def home():return FileResponse(BASE/"static"/"index.html")
@app.get("/health")
def health():return {"ok":True,"ffmpeg":bool(shutil.which("ffmpeg")),"auth_configured":bool(supabase),"billing_configured":bool(stripe.api_key)}
@app.get("/api/me")
def me(authorization:Optional[str]=Header(None)):
    token=authorization.replace("Bearer ","",1) if authorization else None; user=auth_user(token)
    return {"id":user.id,"email":user.email,"plan":tier_for(user.id)}
@app.get("/api/history")
def history(authorization:Optional[str]=Header(None)):
    user=auth_user(authorization.replace("Bearer ","",1) if authorization else None)
    if not supabase: raise HTTPException(503,"Storage/database is not configured.")
    r=supabase.table("jobs").select("id,title,clips,created_at").eq("user_id",user.id).order("created_at",desc=True).limit(50).execute()
    return {"jobs":r.data or []}
@app.post("/api/jobs")
def create_job(req:JobRequest,authorization:Optional[str]=Header(None)):
    user=auth_user(authorization.replace("Bearer ","",1) if authorization else None)
    if not req.authorized: raise HTTPException(400,"Confirm you have rights to process this video.")
    if not youtube_url(req.video_url): raise HTTPException(400,"Enter a supported YouTube video URL.")
    ensure_plan(user,req.clip_count)
    job=uuid.uuid4().hex[:12]; folder=WORK/job; folder.mkdir(parents=True,exist_ok=True)
    try:
        with yt_dlp.YoutubeDL({"quiet":True,"no_warnings":True,"noplaylist":True,"skip_download":True,"socket_timeout":30}) as ydl: info=ydl.extract_info(req.video_url,download=False)
        if info.get("is_live"): raise ValueError("Live streams are not supported.")
        with yt_dlp.YoutubeDL({"format":"bestvideo[height<=1080]+bestaudio/best[height<=1080]/best","outtmpl":str(folder/"source.%(ext)s"),"merge_output_format":"mp4","noplaylist":True,"quiet":True,"no_warnings":True,"socket_timeout":30,"retries":2}) as ydl: ydl.download([req.video_url])
        candidates=[p for p in folder.glob("source.*") if p.is_file() and p.suffix not in {".part",".ytdl"}]
        if not candidates: raise ValueError("Video unavailable, private, restricted, or not downloadable.")
        opts=req.model_dump(); duration,clips=generate(max(candidates,key=lambda p:p.stat().st_size),folder,opts)
        persist_job(user,job,info.get("title","YouTube video"),clips)
        return {"job_id":job,"title":info.get("title","YouTube video"),"duration_seconds":duration,"clips":clips,"note":"Timing-based clips generated. AI highlight ranking and animated word-level captions require the optional speech/AI worker described in README."}
    except Exception as e:
        shutil.rmtree(folder,ignore_errors=True)
        if isinstance(e,HTTPException): raise
        raise HTTPException(500,f"Processing failed: {str(e)[:800]}")
@app.post("/api/billing/checkout")
def checkout(request:Request,authorization:Optional[str]=Header(None),plan:str=Form(...)):
    user=auth_user(authorization.replace("Bearer ","",1) if authorization else None)
    price=os.getenv("STRIPE_CREATOR_PRICE_ID" if plan=="creator" else "STRIPE_PRO_PRICE_ID" if plan=="pro" else "")
    if not stripe.api_key or not price: raise HTTPException(503,"Stripe billing not configured. Add Stripe secret and Price IDs.")
    try:
        session=stripe.checkout.Session.create(mode="subscription",line_items=[{"price":price,"quantity":1}],success_url=os.getenv("APP_URL","https://example.com")+"/?billing=success",cancel_url=os.getenv("APP_URL","https://example.com")+"/?billing=cancelled",client_reference_id=user.id,customer_email=user.email,metadata={"user_id":user.id,"plan":plan})
        return {"url":session.url}
    except Exception as e: raise HTTPException(500,"Could not start checkout: "+str(e)[:300])
@app.post("/api/billing/webhook")
async def stripe_webhook(request:Request):
    if not supabase or not stripe.api_key or not os.getenv("STRIPE_WEBHOOK_SECRET"): raise HTTPException(503,"Billing webhook is not configured.")
    body=await request.body(); sig=request.headers.get("stripe-signature","")
    try:event=stripe.Webhook.construct_event(body,sig,os.getenv("STRIPE_WEBHOOK_SECRET"))
    except Exception:raise HTTPException(400,"Invalid Stripe webhook signature.")
    if event["type"] in {"checkout.session.completed","customer.subscription.updated"}:
        obj=event["data"]["object"]; user_id=obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id"); plan=(obj.get("metadata") or {}).get("plan","creator")
        if user_id: supabase.table("profiles").upsert({"id":user_id,"plan":plan,"stripe_customer_id":obj.get("customer"),"subscription_status":"active"}).execute()
    if event["type"] in {"customer.subscription.deleted","customer.subscription.paused"}:
        obj=event["data"]["object"]; uid=(obj.get("metadata") or {}).get("user_id")
        if uid:supabase.table("profiles").upsert({"id":uid,"plan":"free","subscription_status":"inactive"}).execute()
    return {"received":True}
@app.get("/api/jobs/{job_id}/clips/{filename}")
def get_clip(job_id:str,filename:str,authorization:Optional[str]=Header(None)):
    user=auth_user(authorization.replace("Bearer ","",1) if authorization else None)
    if not re.fullmatch(r"[a-f0-9]{12}",job_id) or not re.fullmatch(r"clip_[0-9]{2}\.mp4",filename):raise HTTPException(400,"Invalid path.")
    folder=WORK/job_id; path=folder/filename
    if not path.is_file():raise HTTPException(404,"Clip expired or not found.")
    # Jobs are stored temporarily on this instance. Durable media storage needs Supabase Storage integration.
    return FileResponse(path,media_type="video/mp4",filename=filename)
