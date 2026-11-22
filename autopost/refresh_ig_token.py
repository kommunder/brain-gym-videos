"""Refresh the long-lived Instagram token (valid 60 days) and print the new one.

The workflow stores the printed token back into the IG_ACCESS_TOKEN secret with `gh secret set`,
which needs a GH_PAT secret (fine-grained token with Secrets: read and write on this repo).
"""
import os
import sys

import requests

tok = os.environ.get("IG_ACCESS_TOKEN")
if not tok:
    sys.exit("IG_ACCESS_TOKEN not set")
r = requests.get("https://graph.instagram.com/refresh_access_token",
                 params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=60)
if r.status_code >= 400:
    sys.exit(f"refresh failed: {r.status_code} {r.text}")
new = r.json()["access_token"]
print(f"::add-mask::{new}")
with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write(f"token={new}\n")
print("refreshed, expires in", r.json().get("expires_in"), "s")
