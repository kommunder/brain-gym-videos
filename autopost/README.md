# Autopost (Instagram Reels + X)

1. Put the video in the repo (e.g. `gta-clips/`).
2. Add a JSON file to `autopost/queue/` (copy `autopost/examples/example.json`).
3. The workflow runs every 30 min and on every push to the queue. A post goes out when `publish_at` has passed.
   Results (link / error) are written back into the same JSON file.

Repository secrets (Settings → Secrets and variables → Actions):
- `IG_ACCESS_TOKEN` — Instagram API with Instagram Login, long-lived token
- `GH_PAT` — fine-grained token, this repo only, "Secrets: Read and write" (lets the token-refresh workflow renew `IG_ACCESS_TOKEN`)
- `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` — X app with Read and Write permission

A platform without secrets is skipped. Never commit tokens: this repo is public.
