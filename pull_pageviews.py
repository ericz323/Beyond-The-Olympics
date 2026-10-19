"""
Olympics Attention Project — Wikipedia Pageviews Pipeline
============================================================
Pulls daily English Wikipedia pageviews for a list of sport and athlete
articles, over a date range that spans multiple Olympic Games, and saves
five tidy long-format CSVs, one per category, for analysis.

Data source: Wikimedia Analytics REST API (free, no auth needed)
Coverage: daily data starts July 1, 2015, so usable Games are:
  Rio 2016, PyeongChang 2018, Tokyo 2021 (held 2021), Beijing 2022,
  Paris 2024, Milan-Cortina 2026

Rate limits (https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits):
  - Unidentified requests (no meaningful User-Agent): 10 req/min
  - User-Agent-compliant unauthenticated clients:    200 req/min
  So this script (a) sends a compliant User-Agent with contact info, and
  (b) throttles to REQUESTS_PER_MINUTE, well under the 200/min ceiling.
  429 and 503 responses are retried with exponential backoff, honoring
  the Retry-After header when the API sets one.

Setup: set a contact address so requests count as identified rather than
anonymous (10 req/min):

    $env:WIKI_CONTACT = "you@example.edu"     # PowerShell
    export WIKI_CONTACT="you@example.edu"     # macOS/Linux

Run:  python pull_pageviews.py
Output: pageviews_<category>.csv in the current working directory
"""

import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

# ----------------------------------------------------------------------
# 1. CONFIG — edit this section as your sport/case-study list evolves
# ----------------------------------------------------------------------

# Wikipedia article titles must match EXACTLY (case, underscores for spaces).
# Test uncertain titles at https://en.wikipedia.org/wiki/<Title> first.

ARTICLE_GROUPS = {
    "infrastructure_sports": {
        "basketball": "Basketball",
        "tennis": "Tennis",
        "soccer": "Association_football",
    },
    "cliff_sports": {
        "swimming": "Competitive_swimming",
        "snowboarding": "Snowboarding",
        "gymnastics": "Gymnastics",
    },
    "halo_effect_sports": {
        "figure_skating": "Figure_skating",
        "gymnastics": "Gymnastics",
        "pole_vault": "Pole_vault"
    },
    "new_sports": {
        "breaking": "Breakdancing",
        "climbing": "Competition_climbing",
    },
    "athletes": {
        "alysa_liu": "Alysa_Liu",
        "simone_biles": "Simone_Biles",
        "suni_lee": "Sunisa_Lee",
        "mondo_duplantis": "Armand_Duplantis",
    },
}

PAGEVIEW_FILES = {
    category: f"pageviews_{category}.csv" for category in ARTICLE_GROUPS
}
PAGEVIEW_COLUMNS = ["date", "article", "views", "label", "category", "key"]

# Preserve sport:: / athlete:: labels while recording the analysis category.
ALL_ARTICLES = {}
ARTICLE_CATEGORIES = {}
for category, articles in ARTICLE_GROUPS.items():
    prefix = "athlete" if category == "athletes" else "sport"
    for key, title in articles.items():
        label = f"{prefix}::{key}"
        ALL_ARTICLES[label] = title
        ARTICLE_CATEGORIES[label] = category

START_DATE = "20150701"  # earliest data available
END_DATE = "20261013"

BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
PROJECT = "en.wikipedia.org"
ACCESS = "all-access"   # could split into desktop/mobile-web/mobile-app if needed
AGENT = "user"          # excludes bots/spiders

# --- rate limiting -----------------------------------------------------
# The ceiling for a UA-compliant unauthenticated client is 200 req/min. We
# run at half that: this pipeline is only ~20 calls, so the headroom costs
# seconds and keeps us clear of shared-IP contention (campus network, or
# someone else running the script at the same time).
REQUESTS_PER_MINUTE = 100
MIN_INTERVAL = 60.0 / REQUESTS_PER_MINUTE  # seconds between request starts

MAX_RETRIES = 5          # per article, for 429/5xx and connection errors
BACKOFF_BASE = 2.0       # seconds; doubles each retry
MAX_BACKOFF = 60.0       # cap, so a bad run can't stall for many minutes
TIMEOUT = 30             # seconds per request

# Wikimedia asks for a User-Agent identifying the client with contact info.
# Without one, requests are treated as "unidentified" and capped at 10/min.
CONTACT = os.environ.get("WIKI_CONTACT", "").strip()
USER_AGENT = (
    f"cornell-data-journal-research/1.0 ({CONTACT})"
    if CONTACT
    else "cornell-data-journal-research/1.0 (contact not set)"
)
HEADERS = {"accept": "application/json", "User-Agent": USER_AGENT}


# ----------------------------------------------------------------------
# 2. THROTTLE + PULL FUNCTIONS
# ----------------------------------------------------------------------

class Throttle:
    """Spaces request starts at least min_interval apart."""

    def __init__(self, min_interval=MIN_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


THROTTLE = Throttle()


def _retry_delay(resp, attempt):
    """Seconds to wait before retrying, honoring Retry-After if present."""
    retry_after = resp.headers.get("Retry-After") if resp is not None else None
    if retry_after:
        try:
            return min(float(retry_after), MAX_BACKOFF)
        except ValueError:
            pass  # HTTP-date form; fall through to exponential backoff
    return min(BACKOFF_BASE * (2 ** attempt), MAX_BACKOFF)


def fetch_pageviews(article_title, session, start=START_DATE, end=END_DATE):
    """Fetch daily pageviews for one article. Returns a DataFrame.

    Retries on 429 (rate limited) and 5xx (backend overload) with
    exponential backoff; other 4xx are treated as permanent failures,
    since a 404 means the title didn't resolve and retrying won't help.
    """
    url = f"{BASE_URL}/{PROJECT}/{ACCESS}/{AGENT}/{article_title}/daily/{start}/{end}"

    last_attempt = MAX_RETRIES - 1
    for attempt in range(MAX_RETRIES):
        THROTTLE.wait()
        try:
            resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            if attempt == last_attempt:
                break
            delay = min(BACKOFF_BASE * (2 ** attempt), MAX_BACKOFF)
            print(f"  connection error ({e.__class__.__name__}), retrying in {delay:.0f}s")
            time.sleep(delay)
            continue

        if resp.status_code == 200:
            data = resp.json().get("items", [])
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["timestamp"], format="%Y%m%d%H")
            return df[["date", "article", "views"]]

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == last_attempt:
                break
            delay = _retry_delay(resp, attempt)
            reason = "rate limited" if resp.status_code == 429 else "server busy"
            print(f"  {reason} ({resp.status_code}), retrying in {delay:.0f}s "
                  f"[attempt {attempt + 1}/{MAX_RETRIES}]")
            time.sleep(delay)
            continue

        print(f"  WARNING: {article_title} returned {resp.status_code}: {resp.text[:200]}")
        return pd.DataFrame()

    print(f"  WARNING: {article_title} gave up after {MAX_RETRIES} attempts")
    return pd.DataFrame()


def pull_all(article_dict):
    """Loop through all articles under the shared rate limiter."""
    frames = []
    with requests.Session() as session:
        for i, (label, title) in enumerate(article_dict.items(), 1):
            print(f"[{i}/{len(article_dict)}] Fetching {label} ({title})...")
            df = fetch_pageviews(title, session)
            if df.empty:
                print("  -> no data returned, check article title")
                continue
            df["label"] = label
            df["category"] = ARTICLE_CATEGORIES[label]
            df["key"] = label.split("::")[1]
            frames.append(df)
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=PAGEVIEW_COLUMNS))


def save_pageviews(df):
    """Write all five category files; empty categories retain CSV headers."""
    for category, out_path in PAGEVIEW_FILES.items():
        group = df.loc[df["category"] == category, PAGEVIEW_COLUMNS]
        group.to_csv(out_path, index=False)
        print(f"Saved {len(group)} rows to {out_path}")
        if group.empty:
            print(f"  WARNING: no data pulled for {category}")


# ----------------------------------------------------------------------
# 3. RUN
# ----------------------------------------------------------------------

if __name__ == "__main__":
    if not CONTACT:
        print('NOTE: WIKI_CONTACT is not set, so requests go out without contact\n'
              '      info. Wikimedia caps unidentified clients at 10 req/min and\n'
              '      may block them outright. Set it before a real run:\n'
              '        $env:WIKI_CONTACT = "you@example.edu"   # PowerShell\n'
              '        export WIKI_CONTACT="you@example.edu"   # macOS/Linux\n')

    est = len(ALL_ARTICLES) * MIN_INTERVAL
    print(f"Pulling {len(ALL_ARTICLES)} articles at {REQUESTS_PER_MINUTE} req/min "
          f"(~{est:.0f}s minimum).\n")

    result = pull_all(ALL_ARTICLES)
    if result.empty:
        print("\nNo data pulled — nothing written, existing CSVs left alone.")
        sys.exit(1)

    save_pageviews(result)
    print(f"Articles pulled: {result['label'].nunique()} / {len(ALL_ARTICLES)}")
    print(f"Date range: {result['date'].min()} to {result['date'].max()}")
