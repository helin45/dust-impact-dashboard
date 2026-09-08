"""
EUMETSAT Data Store lookups, used by the dashboard's "Satellite dust
imagery" panel (dust source attribution tab) to corroborate a flight's
HYSPLIT-modeled dust exposure against a real SEVIRI scene.

Requires a free EUMETSAT Data Store account (https://data.eumetsat.int/),
with an API key generated at https://api.eumetsat.int/api-key/ and its
consumer key/secret exported as EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET.

This only searches the archive and returns the matching scene's own
EUMETSAT Data Store metadata/browse link -- it does NOT download and render
a Dust RGB composite itself (that needs the raw HRSEVIRI channel data plus
Satpy, a much heavier pipeline than fits this lookup). Follow the returned
browse_url to see the actual image in EUMETSAT's own viewer.

Not imported at module load time by app.py -- see merra2_utils.py's module
docstring for why, and the same "not verified against a live account"
caveat applies here too: the token/search endpoints below follow EUMETSAT's
documented API shape, but haven't been exercised against a real key on the
build machine.
"""

import base64
import os
import time
from datetime import datetime, timedelta

import requests

TOKEN_URL = "https://api.eumetsat.int/token"
SEARCH_URL = "https://api.eumetsat.int/data/search-products/os"
# Full-disk High Rate SEVIRI Level 1.5 image data -- the raw channel data a
# Dust RGB composite is built from. EUMETSAT doesn't sell a pre-composited
# "Dust RGB" product directly, so this is the closest archive collection id
# to search against.
HRSEVIRI_COLLECTION = "EO:EUM:DAT:MSG:HRSEVIRI"
SEARCH_WINDOW = timedelta(minutes=90)


def credentials_configured():
    return bool(os.environ.get("EUMETSAT_CONSUMER_KEY")) and bool(os.environ.get("EUMETSAT_CONSUMER_SECRET"))


# EUMETSAT tokens are valid for 1 hour; cached process-wide (module-level,
# not per-request) so a dashboard session making several SEVIRI lookups
# doesn't mint a fresh token every time. Refreshed 5 minutes before actual
# expiry as a safety margin against clock drift/request latency, using
# the token response's own "expires_in" when present rather than
# hardcoding 3600s.
_TOKEN_CACHE = {"token": None, "expires_at": 0.0}
_EXPIRY_SAFETY_MARGIN_S = 300


def _access_token():
    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"]:
        return _TOKEN_CACHE["token"]

    key = os.environ["EUMETSAT_CONSUMER_KEY"]
    secret = os.environ["EUMETSAT_CONSUMER_SECRET"]
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    r = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    token = body["access_token"]
    expires_in = body.get("expires_in", 3600)
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = time.time() + max(0, expires_in - _EXPIRY_SAFETY_MARGIN_S)
    return token


def nearest_scene(lat, lon, when_utc, collection=HRSEVIRI_COLLECTION, window=SEARCH_WINDOW):
    """
    Searches the EUMETSAT Data Store for the HRSEVIRI full-disk scene whose
    sensing time is closest to `when_utc` (a tz-aware datetime), within
    +/- `window`. `lat`/`lon` are accepted for a future bbox-narrowed search
    but full-disk SEVIRI always covers the whole Arabian Peninsula region
    regardless of where in it the flight was, so they aren't currently used
    to filter.
    """
    token = _access_token()
    dtstart = (when_utc - window).strftime("%Y-%m-%dT%H:%M:%SZ")
    dtend = (when_utc + window).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(
        SEARCH_URL,
        params={"pi": collection, "dtstart": dtstart, "dtend": dtend, "si": 0, "c": 5, "format": "json"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    features = r.json().get("features", [])
    if not features:
        return None

    def sensing_dt(feat):
        return datetime.fromisoformat(feat["properties"]["date"].split("/")[0].replace("Z", "+00:00"))

    best = min(features, key=lambda f: abs((sensing_dt(f) - when_utc).total_seconds()))
    props = best["properties"]
    product_id = props.get("identifier")
    return {
        "product_id": product_id,
        "sensing_time_utc": props.get("date"),
        "browse_url": f"https://data.eumetsat.int/product/{product_id}" if product_id else None,
    }
