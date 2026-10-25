# ViralForge Ultimate — setup notes

This is an advanced starter scaffold, not a turnkey AI SaaS. It includes a creator-style UI, basic clip processing, Supabase account API hooks, job metadata history, and Stripe Checkout/webhook scaffolding. You must configure third-party accounts/secrets before those services work.

## 1. Deploy
- Upload/replace project files in GitHub.
- Render service should use Docker runtime and deploy from this repository.
- Set `APP_URL` to the actual Render URL.
- Free Render instances are not reliable for long video processing and ephemeral disk is temporary.

## 2. Supabase accounts/database
1. Create a Supabase project.
2. In SQL Editor, run `supabase_schema.sql`.
3. In Render Environment, set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
4. In `app/static/index.html`, set `config.supabaseUrl` and `config.supabaseAnonKey` to your project URL and public anon/publishable key. Never put the service-role key in HTML.
5. Enable Email/Password auth in Supabase. Email confirmation settings are managed in Supabase Auth.
6. Restart/redeploy Render.

## 3. Stripe subscriptions
1. Create recurring Creator and Pro prices in Stripe test mode first.
2. Set `STRIPE_SECRET_KEY`, `STRIPE_CREATOR_PRICE_ID`, `STRIPE_PRO_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`, and `APP_URL` in Render.
3. Add webhook endpoint `https://YOUR-APP/api/billing/webhook` for checkout.session.completed, customer.subscription.updated, customer.subscription.deleted, customer.subscription.paused.
4. Test with Stripe test cards. Switch to live mode only after validating policies, cancellation, tax, refunds and customer support.
5. For production, improve webhook idempotency and subscription-to-user mapping before taking real payments.

## 4. What is NOT complete in this starter
- AI viral-moment ranking is not implemented. Current clipping uses timing positions (opening/center/distributed).
- Animated word-level captions are not implemented. UI exposes a future caption toggle, but backend does not generate subtitles yet.
- Permanent media storage is not implemented. Render `/tmp` clips disappear on restart and clip download endpoint currently needs signed Supabase Storage URLs.
- Billing webhook is a basic scaffold; subscription lifecycle handling must be production-hardened.
- UI auth client is intentionally disabled until the public Supabase URL/key are entered. Use only the public anon key in browser.
- Usage quotas are basic per-job clip-count checks, not a complete monthly quota ledger.
- Add rate limiting, job queue, background workers, malware/content checks, secure signed download URLs, cleanup jobs, logs/monitoring, and legal/privacy pages before public launch.

## Social Publishing Hub (new UI)
The dashboard now includes destination URL fields for YouTube, Instagram, Facebook and TikTok, a caption/hashtag field, and a Prepare post action. These links are saved in the browser on the current device. Prepare post opens the selected profile and attempts to copy the caption so the user can manually upload the downloaded MP4.

**Important:** this is not direct auto-publishing. Automatic publishing requires OAuth and official API integrations for each platform, plus permissions, app review where required, and secure server-side tokens. Do not collect social passwords. The displayed INR subscription prices are examples only; live billing needs matching recurring Stripe Price IDs. Creator and Pro checkout mappings must be configured in the backend; Studio currently needs an additional backend mapping and Stripe price.
