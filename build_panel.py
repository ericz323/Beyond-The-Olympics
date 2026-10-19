"""
Olympics Attention Project — Panel Builder
============================================================
Combines the Wikipedia pageviews pull with a manual Games calendar to
produce an analysis-ready panel: one row per (sport/athlete, day),
tagged with days-since-nearest-Games-start and days-until-next-Games.

This is what lets you actually measure "the cliff": plot attention as a
function of distance from the Games rather than raw calendar date.

Run this AFTER pull_pageviews.py has produced the five category CSVs.
"""

import pandas as pd

from pull_pageviews import PAGEVIEW_FILES

# ----------------------------------------------------------------------
# 1. GAMES CALENDAR — fill in real opening/closing dates
# Source: Wikipedia "20XX Summer/Winter Olympics" infoboxes, or IOC site.
# Only Games from Rio 2016 onward are usable since pageviews data starts
# July 2015.
# ----------------------------------------------------------------------

GAMES = [
    {"games": "Rio 2016",           "season": "summer", "start": "2016-08-05", "end": "2016-08-21"},
    {"games": "PyeongChang 2018",   "season": "winter",  "start": "2018-02-09", "end": "2018-02-25"},
    {"games": "Tokyo 2021",         "season": "summer",  "start": "2021-07-23", "end": "2021-08-08"},
    {"games": "Beijing 2022",       "season": "winter",  "start": "2022-02-04", "end": "2022-02-20"},
    {"games": "Paris 2024",         "season": "summer",  "start": "2024-07-26", "end": "2024-08-11"},
    {"games": "Milan-Cortina 2026", "season": "winter",  "start": "2026-02-06", "end": "2026-02-22"},
]
games_df = pd.DataFrame(GAMES)
games_df["start"] = pd.to_datetime(games_df["start"])
games_df["end"] = pd.to_datetime(games_df["end"])

# Which case-study keys belong to which season's Games (summer sports
# should only be compared to summer Games, etc.) — edit as your list evolves
SPORT_SEASON = {
    # infrastructure sports
    "basketball": "summer", "tennis": "summer", "soccer": "summer",
    # cliff sports (gymnastics also belongs to halo effect sports)
    "swimming": "summer", "snowboarding": "winter", "gymnastics": "summer",
    # halo effect sports
    "figure_skating": "winter", "pole_vault": "summer",
    # new sports
    "breaking": "summer", "climbing": "summer",
    # athletes
    "alysa_liu": "winter", "simone_biles": "summer",
    "suni_lee": "summer", "mondo_duplantis": "summer",
}

# Keys with no season mapping fall back to comparing against ALL Games, which
# mixes summer and winter and is almost never what you want — so say so loudly
# rather than letting it through silently.
UNMAPPED_SEASON = "__all__"


def attach_nearest_games(df):
    """Add days_from_games and nearest_games to df.

    For each row, finds the nearest Games *start* of the relevant season
    (negative = before that Games, positive = after) and labels which Games
    it was nearest to. Done per season with merge_asof rather than row by
    row, so this stays fast on a full multi-year panel.
    """
    seasons = df["key"].map(SPORT_SEASON).fillna(UNMAPPED_SEASON)

    unmapped = sorted(df.loc[seasons == UNMAPPED_SEASON, "key"].unique())
    if unmapped:
        print(f"WARNING: no SPORT_SEASON entry for {unmapped} — these rows are "
              f"matched against both summer and winter Games. Add them to "
              f"SPORT_SEASON before trusting their days_from_games.")

    pieces = []
    for season, group in df.groupby(seasons, sort=False):
        relevant = (games_df if season == UNMAPPED_SEASON
                    else games_df[games_df["season"] == season])
        right = (relevant[["start", "games"]]
                 .rename(columns={"start": "_games_start", "games": "nearest_games"})
                 .sort_values("_games_start"))

        # merge_asof needs both sides sorted on the join key; mergesort keeps
        # ties in their original order so the output is reproducible.
        merged = pd.merge_asof(
            group.sort_values("date", kind="mergesort"),
            right,
            left_on="date",
            right_on="_games_start",
            direction="nearest",   # ties resolve to the earlier Games
        )
        merged["days_from_games"] = (merged["date"] - merged["_games_start"]).dt.days
        pieces.append(merged.drop(columns="_games_start"))

    # restore the input row order, which the per-season split scrambled
    return (pd.concat(pieces)
              .sort_values("_row", kind="mergesort")
              .drop(columns="_row")
              .reset_index(drop=True))


def build_panel(pageviews_csv, output_csv):
    df = pd.read_csv(pageviews_csv, parse_dates=["date"])
    df["_row"] = range(len(df))

    # compute days-from-nearest-Games and label which Games it's nearest to
    if df.empty:
        df = df.drop(columns="_row")
        df["days_from_games"] = pd.Series(dtype="int64")
        df["nearest_games"] = pd.Series(dtype="object")
    else:
        df = attach_nearest_games(df)

    # flag whether the date falls within a Games window (for the "spike" period)
    df["in_games_window"] = df["days_from_games"].abs() <= 10

    # keep the documented column order regardless of how the merge laid it out
    df = df[["date", "article", "views", "label", "category", "key",
             "days_from_games", "nearest_games", "in_games_window"]]

    df.to_csv(output_csv, index=False)
    print(f"Saved {output_csv} with {len(df)} rows")
    print(f"Categories: {df['category'].unique()}")
    return df


if __name__ == "__main__":
    for category, pageviews_csv in PAGEVIEW_FILES.items():
        build_panel(pageviews_csv, f"panel_{category}.csv")
