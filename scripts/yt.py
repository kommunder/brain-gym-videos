#!/usr/bin/env python3
"""YouTube research tool: search, video stats, comments, channel overview.
Reads YOUTUBE_API_KEY from env. Usage: yt.py <command> <arg> [key=value ...]
Writes JSON to data/yt/result.json (and prints it)."""
import os, sys, json, re, urllib.request, urllib.parse, datetime

KEY = os.environ.get("YOUTUBE_API_KEY", "")
API = "https://www.googleapis.com/youtube/v3/"
units = 0

def call(path, **params):
    global units
    params["key"] = KEY
    url = API + path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(json.dumps({"error": f"HTTP {e.code}", "detail": body[:800]}, ensure_ascii=False))
    units += {"search": 100}.get(path, 1)
    return data

def vid_id(s):
    s = s.strip()
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else s

def dur_sec(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m: return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d*86400 + h*3600 + mi*60 + s

def fmt_video(it):
    sn, st, cd = it.get("snippet", {}), it.get("statistics", {}), it.get("contentDetails", {})
    return {
        "id": it["id"], "url": f"https://www.youtube.com/watch?v={it['id']}",
        "title": sn.get("title"), "channel": sn.get("channelTitle"), "channelId": sn.get("channelId"),
        "published": sn.get("publishedAt"), "durationSec": dur_sec(cd.get("duration")),
        "views": int(st.get("viewCount", 0)), "likes": int(st["likeCount"]) if "likeCount" in st else None,
        "comments": int(st["commentCount"]) if "commentCount" in st else None,
        "tags": sn.get("tags", [])[:15], "description": (sn.get("description") or "")[:500],
        "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
    }

def videos_by_ids(ids):
    out = []
    for i in range(0, len(ids), 50):
        r = call("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i+50]))
        out += [fmt_video(x) for x in r.get("items", [])]
    return out

def cmd_search(q, o):
    n = int(o.get("max", 25)); res, token = [], None
    while len(res) < n:
        r = call("search", part="snippet", q=q, type=o.get("type", "video"), order=o.get("order", "relevance"),
                 maxResults=min(50, n - len(res)), regionCode=o.get("region"), relevanceLanguage=o.get("lang"),
                 publishedAfter=o.get("after"), videoDuration=o.get("duration"), pageToken=token)
        res += [x["id"]["videoId"] for x in r.get("items", []) if "videoId" in x.get("id", {})]
        token = r.get("nextPageToken")
        if not token: break
    vids = videos_by_ids(res[:n])
    if o.get("sort") == "views": vids.sort(key=lambda v: -v["views"])
    return {"query": q, "count": len(vids), "videos": vids}

def cmd_video(arg, o):
    return {"videos": videos_by_ids([vid_id(x) for x in re.split(r"[,\s]+", arg) if x])}

def cmd_comments(arg, o):
    vid = vid_id(arg); n = int(o.get("max", 100)); out, token = [], None
    while len(out) < n:
        r = call("commentThreads", part="snippet", videoId=vid, maxResults=min(100, n - len(out)),
                 order=o.get("order", "relevance"), textFormat="plainText", pageToken=token)
        for it in r.get("items", []):
            c = it["snippet"]["topLevelComment"]["snippet"]
            out.append({"author": c.get("authorDisplayName"), "text": c.get("textDisplay"), "likes": c.get("likeCount"),
                        "published": c.get("publishedAt"), "replies": it["snippet"].get("totalReplyCount", 0)})
        token = r.get("nextPageToken")
        if not token: break
    return {"videoId": vid, "count": len(out), "comments": out}

def cmd_channel(arg, o):
    a = arg.strip()
    if a.startswith("http"):
        m = re.search(r"youtube\.com/(@[^/?]+|channel/(UC[\w-]{22}))", a)
        a = (m.group(2) or m.group(1)) if m else a
    kw = {"id": a} if a.startswith("UC") else {"forHandle": a if a.startswith("@") else "@" + a}
    r = call("channels", part="snippet,statistics,contentDetails", **kw)
    if not r.get("items"): raise SystemExit(json.dumps({"error": "channel not found", "arg": arg}))
    ch = r["items"][0]; st = ch["statistics"]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = call("playlistItems", part="contentDetails", playlistId=uploads, maxResults=min(50, int(o.get("max", 20))))
    vids = videos_by_ids([x["contentDetails"]["videoId"] for x in pl.get("items", [])])
    return {"channel": {"id": ch["id"], "title": ch["snippet"]["title"], "handle": ch["snippet"].get("customUrl"),
            "country": ch["snippet"].get("country"), "created": ch["snippet"]["publishedAt"],
            "subscribers": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
            "views": int(st.get("viewCount", 0)), "videos": int(st.get("videoCount", 0))}, "recent": vids}

def cmd_transcript(arg, o):  # best effort: YouTube often blocks datacenter IPs
    from youtube_transcript_api import YouTubeTranscriptApi
    vid = vid_id(arg)
    langs = [x for x in o.get("lang", "en,ru").split(",")]
    api = YouTubeTranscriptApi()
    t = api.fetch(vid, languages=langs)
    return {"videoId": vid, "language": t.language_code, "text": " ".join(s.text for s in t)}

CMDS = {"search": cmd_search, "video": cmd_video, "comments": cmd_comments, "channel": cmd_channel, "transcript": cmd_transcript}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in CMDS:
        sys.exit("usage: yt.py {%s} <arg> [key=value ...]" % "|".join(CMDS))
    cmd, arg = sys.argv[1], sys.argv[2]
    opts = dict(x.split("=", 1) for x in sys.argv[3:] if "=" in x)
    try:
        if not KEY and cmd != "transcript":
            raise SystemExit(json.dumps({"error": "YOUTUBE_API_KEY is not set in repository secrets"}))
        result = CMDS[cmd](arg, opts)
    except SystemExit as e:
        result = json.loads(str(e)) if str(e).startswith("{") else {"error": str(e)}
    except Exception as e:
        result = {"error": type(e).__name__, "detail": str(e)[:800]}
    result = {"request": {"command": cmd, "arg": arg, "options": opts, "at": datetime.datetime.utcnow().isoformat() + "Z", "quotaUnits": units}, **result}
    os.makedirs("data/yt", exist_ok=True)
    with open("data/yt/result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False)[:3000])
