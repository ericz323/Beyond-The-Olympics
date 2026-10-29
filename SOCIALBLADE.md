# Social Blade history downloader

`pull_socialblade.py` uses Python 3.10+ and the standard library: no package installation needed. It retrieves YouTube cumulative channel views and Instagram cumulative followers, then derives daily net growth. Documentation checked October 29, 2026: https://socialblade.com/developers/docs

## Credentials

Obtain your **client ID and token** from the Social Blade developer console. Set `SOCIALBLADE_CLIENT_ID` and `SOCIALBLADE_TOKEN` in the environment of the terminal running the script. Do not put credentials in the source, notebooks, account lists, or Git. The script sends them in HTTP headers and never writes them to its output files.

For example, in PowerShell, prompt without putting the actual token in command history:

```powershell
$env:SOCIALBLADE_CLIENT_ID = Read-Host 'Social Blade client ID'
$sbSecret = Read-Host 'Social Blade token' -AsSecureString
$env:SOCIALBLADE_TOKEN = [System.Net.NetworkCredential]::new('', $sbSecret).Password
```

## Run from this project folder

Preview the five breaking-channel requests without spending credits:

```powershell
python pull_socialblade.py --youtube UCz98WUC8uWURqbNaTobcRbw UC9oEzPGZiTE692KucAsTY1g UCgiz3Pm3FBhDyy3g_fP5OVA UCLRCoLpDImkbFw8D3qXFpUQ UCeTvX9orVPSUbAa-JiA6H9Q --start 2023-11-01 --end 2025-02-28 --dry-run
```

Remove `--dry-run` to fetch. IDs are preferred for YouTube; quoted handles such as `'@redbullbcone'` also work. For Instagram, supply creator/business usernames without URLs. Replace the example usernames below with your selected accounts:

```powershell
python pull_socialblade.py --youtube '@redbullbcone' --instagram INSTAGRAM_USERNAME --history archive --max-credits 6
```

`--history archive` is the default: up to three years, normally 3 credits per profile/platform. `default` requests up to 30 days for 1 credit; `extended` requests up to one year for 2 credits. Instagram additionally supports `vault` (up to ten years, 5 credits); run that separately from YouTube. Availability depends on the profile and may be downgraded by the API. Date filters apply locally and do not reduce API costs or expand retention.

The estimated batch cost is printed before requests. `--max-credits` defaults to 100 and blocks a batch whose estimated cost exceeds that limit. It is a conservative per-batch estimate, not an account balance check or a guarantee of server billing. It does not count cached requests. Social Blade documents a 30-day repeat-access window; the script does not assume this discount when estimating a refreshed request. Using both a channel ID and its handle counts as two requests in the plan; choose one identifier consistently.

## Caching and repeat analyses

- Default behavior reuses the local response for the same platform, query and history tier, indefinitely. The retrieval timestamp always remains the original one.
- `--offline` requires existing cached responses and makes no API calls. Use it to change date filters or rebuild CSVs without spending credits.
- `--refresh` requests updated data; it may cost credits. Existing run folders retain their old raw snapshots. The cache is replaced only after a response passes normalization.
- `--demo --max-credits 0` uses Social Blade's two documented free sample accounts with no credentials. It cannot be combined with your account lists. This tests the connection without using your purchased credits.

Example offline analysis:

```powershell
python pull_socialblade.py --youtube '@redbullbcone' --instagram INSTAGRAM_USERNAME --start 2024-01-01 --end 2024-12-31 --offline
```

Only repeat accounts/tier already fetched into the same output directory. Use `--output PATH` to choose another dataset/cache location. The default is `socialblade_data` beside the script. Requests have a 45-second timeout and are not retried automatically; after a network or API error, inspect the manifest and rerun as appropriate. Successful profiles are cached even when another profile fails.

## Outputs and metric definitions

Both history CSVs include `days_from_olympics` (signed days since the opening date; opening day is 0) and `in_olympics` (1 if the row's date is inside the inclusive Olympic window, otherwise 0). Defaults are July 26–August 11, 2024, the opening-to-closing window reported by [Breaking GB](https://breakinggb.org/events/events-calendar/paris-olympic-games-2024). Some sports began before the opening ceremony. These fields classify the row date, not a multi-day growth interval or a current rolling summary. Override with `--olympics-start YYYY-MM-DD --olympics-end YYYY-MM-DD`; the chosen dates are recorded in the manifest. Use `--offline` with the same accounts/history/output directory to regenerate CSVs from cache without credits. Older run folders are preserved.

Every invocation creates a new timestamped `socialblade_data/runs/...` folder, preserving previous results:

| File | Contents |
| --- | --- |
| `youtube_history.csv` | Dated `total_views`, `growth_views`, and `interval_growth_views`. |
| `instagram_history.csv` | Dated `total_followers`, `growth_followers`, and `interval_growth_followers`. |
| `current_snapshots.csv` | API's current total and reported 1/3/7/14/30/60/90/180/365-day growth, as retrieved. These are current rolling summaries, not historical rows. |
| `raw/*.json` | Original JSON response plus nonsecret query and retrieval metadata. |
| `manifest.json` | Coverage dates, missing dates/totals, selected row counts, cache use, API-reported remaining credits, and failures. |

Only requested platforms get history CSVs. All history rows include account identity, source URL, retrieval time, previous observation date, interval length and quality flags. `date` preserves the API's date label; no local timezone conversion is applied.

`growth_views` and `growth_followers` are the current total minus the preceding day's total, **only when both totals exist and dates are consecutive**. The first available observation, missing totals and multi-day gaps get a blank daily-growth field, not zero. `interval_growth_*` retains a difference across a gap and `interval_days` records its duration; do not interpret that difference as one day's growth. Calculations use the full returned history before date filtering, allowing the first selected row to use an earlier observation.

Negative net changes are retained and flagged. Follower declines can be real; view-total declines can reflect removals/adjustments. No missing days are invented, interpolated or prorated. Duplicate dates or malformed totals fail that profile instead of silently changing the data. Available range and missing calendar-date counts are recorded; a range covering the requested endpoints does not guarantee every interior day exists.

Channel views include all channel content. Instagram followers measure audience size, not video views/reach. Counts are third-party public snapshots, not owner-authorized analytics. The API's reported current rolling growth can differ from differences in the daily series because their update timing may differ; they are intentionally exported separately.

Exit status is 0 when every requested profile produced selected rows; it is nonzero for failed profiles or empty selected windows. Partial results and the manifest remain available. API failure JSON is saved when received; an HTTP/network failure may have no raw JSON file. No real paid histories are bundled with the script.

## Validation

Run `python -m unittest test_pull_socialblade.py`. Tests use fabricated data and cover sorting, negative changes, date gaps, missing totals, duplicates, filtering, dry-run behavior, spending limits and offline cache replay. Run `python pull_socialblade.py --demo --max-credits 0` for a live zero-credit connection check.
