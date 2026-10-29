"""Download Social Blade histories using Python 3.10+ (standard library only).

Credentials: SOCIALBLADE_CLIENT_ID and SOCIALBLADE_TOKEN environment variables.
Run with --help for usage. No network requests are made on import.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://matrix.sbapis.com/b"
COST = {"default": 1, "extended": 2, "archive": 3, "vault": 5}
COMMON = ["platform", "query", "account_id", "account_name", "date",
          "days_from_olympics", "in_olympics"]
TAIL = ["previous_date", "interval_days", "quality_flag", "retrieved_at_utc", "source_url"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def integer(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Expected a nonnegative integer cumulative count or null")
    return value


def normalize(envelope, start=None, end=None,
              olympics_start=date(2024, 7, 26), olympics_end=date(2024, 8, 11)):
    """Sort totals, reject duplicate dates, derive net daily/interval changes."""
    if olympics_start > olympics_end:
        raise ValueError("Olympics start must not be after Olympics end")
    payload = envelope["response"]
    if payload.get("status", {}).get("success") is not True:
        raise ValueError("API reported failure; see the saved raw response")
    data = payload.get("data", {})
    daily = data.get("daily")
    if not isinstance(daily, list) or not daily:
        raise ValueError("No daily history returned")
    platform = envelope["platform"]
    metric = "views" if platform == "youtube" else "followers"
    identity = data.get("id", {})
    observations = {}
    for item in daily:
        day = date.fromisoformat(item["date"][:10])
        if day in observations:
            raise ValueError(f"Duplicate observation date: {day}")
        observations[day] = integer(item.get(metric))
    rows = []
    previous_day = previous_total = None
    for day, total in sorted(observations.items()):
        interval = (day - previous_day).days if previous_day else None
        delta = total - previous_total if total is not None and previous_total is not None else None
        flags = []
        if previous_day is None:
            flags.append("first_observation")
        if total is None or (previous_day is not None and previous_total is None):
            flags.append("missing_total")
        if interval is not None and interval != 1:
            flags.append("date_gap")
        if delta is not None and delta < 0:
            flags.append("negative_net_change")
        row = {
            "platform": platform, "query": envelope["query"],
            "account_id": identity.get("id", ""),
            "account_name": identity.get("display_name", identity.get("username", "")),
            "date": day.isoformat(), f"total_{metric}": total,
            "days_from_olympics": (day - olympics_start).days,
            "in_olympics": int(olympics_start <= day <= olympics_end),
            f"growth_{metric}": delta if interval == 1 else None,
            f"interval_growth_{metric}": delta,
            "previous_date": previous_day.isoformat() if previous_day else "",
            "interval_days": interval, "quality_flag": "|".join(flags) or "ok",
            "retrieved_at_utc": envelope["retrieved_at_utc"],
            "source_url": envelope["source_url"],
        }
        # Compute differences BEFORE filtering, so the first selected date can
        # use a preceding observation outside the requested analysis window.
        if (start is None or day >= start) and (end is None or day <= end):
            rows.append(row)
        previous_day, previous_total = day, total
    coverage = {"available_start": min(observations).isoformat(),
                "available_end": max(observations).isoformat(),
                "available_rows": len(observations), "selected_rows": len(rows),
                "missing_calendar_dates": (max(observations) - min(observations)).days + 1 - len(observations),
                "missing_total_rows": sum(value is None for value in observations.values()),
                "requested_start_not_covered": start is not None and min(observations) > start,
                "requested_end_not_covered": end is not None and max(observations) < end}
    return rows, coverage


def fetch(platform, query, history, client_id, token):
    url = f"{BASE}/{platform}/statistics?" + urlencode({
        "query": query, "history": history, "allow-stale": "false"})
    headers = {"Accept": "application/json", "User-Agent": "SocialBladeResearch/1.0"}
    if client_id and token:
        headers.update({"clientid": client_id, "token": token})
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except HTTPError as error:
        # Do not print response headers or credentials. Avoid automatic paid retries.
        raise RuntimeError(f"HTTP {error.code}; no automatic retry. Check credentials, credits or rate limits.") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Network request failed; no automatic retry. Check connectivity and rerun.") from None
    return {"platform": platform, "query": query, "history_requested": history,
            "retrieved_at_utc": utc_now(), "source_url": url, "response": payload}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--youtube", nargs="+", default=[], help="Channel IDs (preferred) or @handles")
    parser.add_argument("--instagram", nargs="+", default=[], help="Creator/business usernames")
    parser.add_argument("--history", choices=COST, default="archive", help="Default: archive, up to 3 years at 3 credits/profile")
    parser.add_argument("--start", type=date.fromisoformat, help="Inclusive YYYY-MM-DD local output filter")
    parser.add_argument("--end", type=date.fromisoformat, help="Inclusive YYYY-MM-DD local output filter")
    parser.add_argument("--olympics-start", type=date.fromisoformat, default=date(2024, 7, 26),
                        help="Day zero and inclusive Olympic start (default 2024-07-26)")
    parser.add_argument("--olympics-end", type=date.fromisoformat, default=date(2024, 8, 11),
                        help="Inclusive Olympic end (default 2024-08-11)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "socialblade_data")
    parser.add_argument("--refresh", action="store_true", help="Fetch again instead of reusing cached responses")
    parser.add_argument("--offline", action="store_true", help="Only use existing cache; never contact API")
    parser.add_argument("--dry-run", action="store_true", help="Show request plan; no network calls or file writes")
    parser.add_argument("--max-credits", type=int, default=100, help="Maximum estimated credits for this batch (default 100)")
    parser.add_argument("--demo", action="store_true", help="Fetch Social Blade's two free sample accounts, without credentials")
    args = parser.parse_args(argv)
    if args.start and args.end and args.start > args.end:
        parser.error("--start must not be after --end")
    if args.olympics_start > args.olympics_end:
        parser.error("--olympics-start must not be after --olympics-end")
    if args.refresh and args.offline:
        parser.error("--refresh and --offline cannot be combined")
    if args.demo:
        if args.youtube or args.instagram:
            parser.error("--demo cannot be combined with account lists")
        args.youtube, args.instagram, args.history = ["socialblade"], ["socialblade"], "default"
    if args.youtube and args.history == "vault":
        parser.error("YouTube supports at most archive (3 years), not vault")
    if args.max_credits < 0:
        parser.error("--max-credits must be nonnegative")
    accounts = []
    for platform, queries in [("youtube", args.youtube), ("instagram", args.instagram)]:
        for query in queries:
            query = query.strip()
            if platform == "instagram":
                query = query.lstrip("@").lower()
            if not query or "/" in query or ":" in query:
                parser.error("Use IDs/handles/usernames, not URLs")
            if (platform, query) not in accounts:
                accounts.append((platform, query))
    if not accounts:
        parser.error("Provide --youtube, --instagram, or --demo")
    plan = []
    for platform, query in accounts:
        key = hashlib.sha256(f"{platform}:{query}:{args.history}".encode()).hexdigest()[:24]
        cache = args.output / "cache" / f"{key}.json"
        cached = cache.exists() and not args.refresh
        if args.offline and not cached:
            parser.error(f"No cached response for {platform} {query}")
        estimate = 0 if cached or args.demo else COST[args.history]
        plan.append((platform, query, key, cache, cached, estimate))
        print(f"{platform} {query}: {'cached' if cached else 'fetch'}, estimated credits {estimate}")
    estimate = sum(item[-1] for item in plan)
    print(f"Estimated maximum batch cost: {estimate} credits. Cached data stay unchanged unless --refresh is used.")
    if estimate > args.max_credits:
        parser.error("Estimated batch cost exceeds --max-credits; no requests made")
    if args.dry_run:
        return 0
    client_id = "" if args.demo else os.environ.get("SOCIALBLADE_CLIENT_ID", "")
    token = "" if args.demo else os.environ.get("SOCIALBLADE_TOKEN", "")
    if not args.demo and any(not item[4] for item in plan) and not (client_id and token):
        parser.error("Set SOCIALBLADE_CLIENT_ID and SOCIALBLADE_TOKEN; credentials are never placed in URLs")
    run = args.output / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run.mkdir(parents=True, exist_ok=False)
    histories = {"youtube": [], "instagram": []}
    snapshots, results = [], []
    for platform, query, key, cache, cached, cost in plan:
        result = {"platform": platform, "query": query, "cached": cached, "estimated_credits": cost}
        try:
            envelope = json.loads(cache.read_text(encoding="utf-8")) if cached else fetch(platform, query, args.history, client_id, token)
            write_json(run / "raw" / f"{key}.json", envelope)
            rows, coverage = normalize(envelope, args.start, args.end,
                                       args.olympics_start, args.olympics_end)
            if not cached:
                write_json(cache, envelope)
            histories[platform].extend(rows)
            data = envelope["response"]["data"]
            metric = "views" if platform == "youtube" else "followers"
            growth_key = "vidviews" if platform == "youtube" else "followers"
            stats = data.get("statistics", {})
            snapshot = {"platform": platform, "query": query, "metric": metric,
                        "retrieved_at_utc": envelope["retrieved_at_utc"],
                        "current_total": stats.get("total", {}).get(metric),
                        "source_url": envelope["source_url"]}
            for days in [1, 3, 7, 14, 30, 60, 90, 180, 365]:
                snapshot[f"reported_growth_{days}d"] = stats.get("growth", {}).get(growth_key, {}).get(str(days))
            snapshots.append(snapshot)
            result.update(coverage)
            result["status"] = "ok" if rows else "no_rows_in_selected_window"
            result["credits_remaining_reported"] = envelope["response"].get("info", {}).get("credits", {}).get("available")
            print(f"{platform} {query}: {len(rows)} rows; available {coverage['available_start']} through {coverage['available_end']}")
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
            result.update(status="error", error=str(error))
            print(f"{platform} {query}: {error}", file=sys.stderr)
        results.append(result)
    for platform in sorted({item[0] for item in accounts}):
        metric = "views" if platform == "youtube" else "followers"
        write_csv(run / f"{platform}_history.csv", histories[platform],
                  COMMON + [f"total_{metric}", f"growth_{metric}", f"interval_growth_{metric}"] + TAIL)
    if snapshots:
        write_csv(run / "current_snapshots.csv", snapshots, list(snapshots[0]))
    write_json(run / "manifest.json", {"created_at_utc": utc_now(), "history_requested": args.history,
               "olympics_start": args.olympics_start.isoformat(),
               "olympics_end": args.olympics_end.isoformat(),
               "start": str(args.start) if args.start else None, "end": str(args.end) if args.end else None,
               "estimated_max_credits": estimate, "accounts": results})
    print(f"Saved results to {run}")
    return 0 if all(item["status"] == "ok" for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
