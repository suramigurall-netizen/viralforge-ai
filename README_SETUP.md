# ViralForge AI — Starter Setup

## Included
- FastAPI app with `/`, `/health`, `/api/me`, `/api/jobs`, and clip download routes
- Supabase email/password sign-up and sign-in UI
- YouTube URL processing with yt-dlp + FFmpeg
- Vertical 9:16, square 1:1, and landscape 16:9 clips
- Docker deployment setup for Render

## Important truth about scope
This is a deployable starter, not a fully finished commercial SaaS. It samples evenly spaced sections of the source video; it does **not** perform AI viral-moment ranking, speech transcription, animated captions, Stripe subscriptions, or social-platform publishing. Those require additional implementation and external provider setup. No software can honestly guarantee zero errors in every environment.

## Render deployment
1. Upload the contents of this folder to the root of your GitHub repository (the `Dockerfile` and `requirements.txt` must be at repository root).
2. In Render, use a **Web Service** connected to the repo and choose **Docker** as the runtime.
3. Deploy. The app listens on `PORT` (Render supplies it).
4. In Render → Environment, add:
   - `SUPABASE_URL` = the Project URL from Supabase
   - `SUPABASE_ANON_KEY` = the publishable key (or legacy anon key) from Supabase API Keys
5. Save and redeploy.
6. Open the app and use Service status → Check service. `ok`, `ffmpeg`, `yt_dlp`, and `supabase_configured` should be true.

## Supabase browser config
Open `app/static/index.html` on GitHub and set these two constants near the bottom:
```js
const SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co";
const SUPABASE_ANON_KEY = "YOUR_PUBLISHABLE_OR_LEGACY_ANON_KEY";
```
Use only the publishable/anon key in HTML. **Never put `service_role` or secret keys in the browser or GitHub.**

## Permanent storage (optional)
1. Supabase → Storage → create a bucket named `viralforge-clips`.
2. For public download URLs, set the bucket to public; for private videos, implement signed URLs before production.
3. Add `SUPABASE_SERVICE_ROLE_KEY` in Render Environment only. Keep it secret. The server attempts to upload processed clips to this bucket.
4. The service-role key is highly privileged. Never share it in screenshots or messages. If it has been exposed, rotate it.

## Known limitations / production checklist
- Render ephemeral filesystem means local download files can disappear after restart; enable Supabase Storage for persistence.
- Public bucket URLs are public to anyone with the link. Use private buckets and signed URLs for private user media.
- Add rate limiting, quotas, background job queue, cleanup, database records, signed storage URLs, monitoring, and stronger abuse controls before public launch.
- yt-dlp source access depends on the video, region, and platform restrictions. Process only content you own or are authorized to use.
- AI highlight selection, caption transcription/rendering, Stripe checkout/webhooks, and publishing integrations are not implemented in this version.
