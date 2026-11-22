"""Autopost queued clips to Instagram (Reels) and X.

Queue: autopost/queue/*.json, one file per post:
{
  "video": "gta-clips/gta6-kreekcraft-bank.mp4",   # path inside this repo
  "caption": "Instagram caption with #hashtags",
  "x_text": "Text for X (optional, falls back to caption, max 280 chars)",
  "publish_at": "2026-11-23T08:00:00+08:00",       # optional, publish as soon as possible if missing
  "platforms": ["instagram", "x"]
}
The script fills in "results" per platform. A platform with status "done" is never posted again.
A platform whose secrets are missing is skipped (status "no_credentials") and retried on later runs.

Secrets (environment):
  IG_ACCESS_TOKEN                           Instagram API with Instagram Login, long-lived token
  X_API_KEY, X_API_SECRET,
  X_ACCESS_TOKEN, X_ACCESS_SECRET           X app keys + access token of the posting account (OAuth 1.0a)
  GITHUB_REPOSITORY, GITHUB_REF_NAME        set by GitHub Actions, used for the raw video URL
"""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "autopost" / "queue"
IG_API = "https://graph.instagram.com/v23.0"
X_API = "https://api.x.com/2"
MAX_ATTEMPTS = 3


def now():
    return dt.datetime.now(dt.timezone.utc)


def raw_url(path):
    repo = os.environ.get("GITHUB_REPOSITORY", "kommunder/brain-gym-videos")
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"


# ---------------- Instagram ----------------

def ig_post(item):
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        return {"status": "no_credentials"}
    me = requests.get(f"{IG_API}/me", params={"fields": "user_id,username", "access_token": token}, timeout=60)
    me.raise_for_status()
    uid = me.json()["user_id"]
    r = requests.post(f"{IG_API}/{uid}/media", data={
        "media_type": "REELS",
        "video_url": raw_url(item["video"]),
        "caption": item.get("caption", ""),
        "share_to_feed": "true",
        "access_token": token,
    }, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"IG create container: {r.status_code} {r.text}")
    cid = r.json()["id"]
    for _ in range(60):  # up to ~10 min
        time.sleep(10)
        s = requests.get(f"{IG_API}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=60).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG processing failed: {s}")
    else:
        raise RuntimeError("IG processing timeout")
    p = requests.post(f"{IG_API}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=120)
    if p.status_code >= 400:
        raise RuntimeError(f"IG publish: {p.status_code} {p.text}")
    mid = p.json()["id"]
    link = requests.get(f"{IG_API}/{mid}", params={"fields": "permalink", "access_token": token}, timeout=60).json().get("permalink")
    return {"status": "done", "id": mid, "url": link}


# ---------------- X ----------------

def x_auth():
    keys = [os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    if not all(keys):
        return None
    return OAuth1(keys[0], keys[1], keys[2], keys[3])


def x_post(item):
    auth = x_auth()
    if not auth:
        return {"status": "no_credentials"}
    data = (ROOT / item["video"]).read_bytes()
    r = requests.post(f"{X_API}/media/upload/initialize", auth=auth, json={
        "media_type": "video/mp4", "total_bytes": len(data), "media_category": "tweet_video"}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"X init: {r.status_code} {r.text}")
    mid = r.json()["data"]["id"]
    chunk = 4 * 1024 * 1024
    for i, off in enumerate(range(0, len(data), chunk)):
        a = requests.post(f"{X_API}/media/upload/{mid}/append", auth=auth,
                          files={"media": ("chunk", data[off:off + chunk], "application/octet-stream")},
                          data={"segment_index": str(i)}, timeout=300)
        if a.status_code >= 400:
            raise RuntimeError(f"X append {i}: {a.status_code} {a.text}")
    f = requests.post(f"{X_API}/media/upload/{mid}/finalize", auth=auth, timeout=120)
    if f.status_code >= 400:
        raise RuntimeError(f"X finalize: {f.status_code} {f.text}")
    info = f.json().get("data", {}).get("processing_info")
    for _ in range(60):
        if not info or info.get("state") == "succeeded":
            break
        if info.get("state") == "failed":
            raise RuntimeError(f"X processing failed: {info}")
        time.sleep(info.get("check_after_secs", 5))
        s = requests.get(f"{X_API}/media/upload", auth=auth,
                         params={"command": "STATUS", "media_id": mid}, timeout=60).json()
        info = s.get("data", {}).get("processing_info")
    text = (item.get("x_text") or item.get("caption", ""))[:280]
    t = requests.post(f"{X_API}/tweets", auth=auth, json={"text": text, "media": {"media_ids": [mid]}}, timeout=60)
    if t.status_code >= 400:
        raise RuntimeError(f"X tweet: {t.status_code} {t.text}")
    tid = t.json()["data"]["id"]
    return {"status": "done", "id": tid, "url": f"https://x.com/i/status/{tid}"}


POSTERS = {"instagram": ig_post, "x": x_post}


def main():
    dry = "--dry-run" in sys.argv
    changed = False
    for qf in sorted(QUEUE.glob("*.json")):
        item = json.loads(qf.read_text(encoding="utf-8"))
        when = item.get("publish_at")
        if when and dt.datetime.fromisoformat(when) > now():
            print(f"{qf.name}: scheduled for {when}, skipping")
            continue
        if not (ROOT / item["video"]).exists():
            print(f"{qf.name}: video {item['video']} not found")
            continue
        results = item.setdefault("results", {})
        for platform in item.get("platforms", ["instagram", "x"]):
            res = results.get(platform, {})
            if res.get("status") == "done" or res.get("attempts", 0) >= MAX_ATTEMPTS:
                continue
            if dry:
                print(f"{qf.name}: would post to {platform}")
                continue
            try:
                out = POSTERS[platform](item)
            except Exception as e:  # keep going with other platforms/posts
                out = {"status": "error", "error": str(e)[:500], "attempts": res.get("attempts", 0) + 1}
            if out["status"] == "no_credentials" and res.get("status") == "no_credentials":
                continue
            out["time"] = now().isoformat(timespec="seconds")
            results[platform] = out
            changed = True
            print(f"{qf.name} -> {platform}: {out}")
        qf.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("changed" if changed else "nothing to do")


if __name__ == "__main__":
    main()
