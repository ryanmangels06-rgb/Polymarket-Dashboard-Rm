"""Read-only NFL schedule-to-market matching utilities.

Only compares pre-kickoff moneyline outcomes against historical Elo forecasts.
No position sizing, order placement, or claims of a validated edge.
"""
from datetime import datetime, timezone, timedelta
import math
import re

TEAM_NAMES = {
    "ARI": ("cardinals", "arizona cardinals"),
    "ATL": ("falcons", "atlanta falcons"),
    "BAL": ("ravens", "baltimore ravens"),
    "BUF": ("bills", "buffalo bills"),
    "CAR": ("panthers", "carolina panthers"),
    "CHI": ("bears", "chicago bears"),
    "CIN": ("bengals", "cincinnati bengals"),
    "CLE": ("browns", "cleveland browns"),
    "DAL": ("cowboys", "dallas cowboys"),
    "DEN": ("broncos", "denver broncos"),
    "DET": ("lions", "detroit lions"),
    "GB": ("packers", "green bay packers"),
    "HOU": ("texans", "houston texans"),
    "IND": ("colts", "indianapolis colts"),
    "JAX": ("jaguars", "jacksonville jaguars"),
    "KC": ("chiefs", "kansas city chiefs"),
    "LV": ("raiders", "las vegas raiders"),
    "LAC": ("chargers", "los angeles chargers"),
    "LA": ("rams", "los angeles rams"),
    "MIA": ("dolphins", "miami dolphins"),
    "MIN": ("vikings", "minnesota vikings"),
    "NE": ("patriots", "new england patriots"),
    "NO": ("saints", "new orleans saints"),
    "NYG": ("giants", "new york giants"),
    "NYJ": ("jets", "new york jets"),
    "PHI": ("eagles", "philadelphia eagles"),
    "PIT": ("steelers", "pittsburgh steelers"),
    "SF": ("49ers", "san francisco 49ers", "niners"),
    "SEA": ("seahawks", "seattle seahawks"),
    "TB": ("buccaneers", "tampa bay buccaneers", "bucs"),
    "TEN": ("titans", "tennessee titans"),
    "WAS": ("commanders", "washington commanders"),
}
CODE_ALIASES = {"JAC": "JAX", "WSH": "WAS", "LAR": "LA", "OAK": "LV", "STL": "LA"}


def canonical(code):
    raw = str(code or "").strip().upper()
    return CODE_ALIASES.get(raw, raw)


def identify_team(text):
    """Return one NFL code only if the label is unambiguous."""
    raw = str(text or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    found = set()
    for code, names in TEAM_NAMES.items():
        if normalized == code.lower():
            found.add(code)
        if any(re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", normalized) for name in names):
            found.add(code)
    # Common abbreviation used for Jacksonville in external scoreboards.
    if normalized in ("jac", "jax"):
        found.add("JAX")
    if normalized in ("lar",):
        found.add("LA")
    if normalized == "wsh":
        found.add("WAS")
    return next(iter(found)) if len(found) == 1 else None


def title_teams(title):
    parts = re.split(r"\s+(?:vs\.?|versus|@)\s+", str(title or ""), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    first, second = identify_team(parts[0]), identify_team(parts[1])
    if not first or not second or first == second:
        return None
    return frozenset((first, second))


def utc(value):
    if isinstance(value, datetime):
        value_dt = value
    else:
        value_dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value_dt.tzinfo is None:
        return value_dt.replace(tzinfo=timezone.utc)
    return value_dt.astimezone(timezone.utc)


def game_moneyline(question):
    q = str(question or "").lower()
    if any(phrase in q for phrase in ("spread", "total", "over/under", "o/u", "1st half", "first half", "second half", "quarter")):
        return False
    return title_teams(question) is not None


def compare_forecasts(predictions, market_rows, as_of=None):
    """Match upcoming Elo schedule games and valid order-book asks.

    Returns (comparisons, diagnostics). Rows can only produce a comparison
    when the matchup, time, outcome and actual ask are each unambiguous.
    """
    now = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    forecast_index = {}
    for p in predictions:
        if p.get("Result") is not None:
            try:
                if not math.isnan(float(p["Result"])):
                    continue
            except (TypeError, ValueError):
                continue
        try:
            dt = utc(p["Date"])
            a, h = canonical(p["Away"]), canonical(p["Home"])
            prob = float(p["Home win probability"])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if dt <= now or not 0 < prob < 1 or a == h:
            continue
        forecast_index.setdefault(frozenset((a, h)), []).append((dt, p, prob))

    compared = []
    seen = set()
    diagnostics = {"input_rows": len(market_rows), "matched": 0, "missing_quote": 0, "unmatched": 0}
    for row in market_rows:
        if row.get("Type") != "Moneyline" or not game_moneyline(row.get("Market")):
            continue
        teams = title_teams(row.get("Game"))
        outcome = identify_team(row.get("Outcome"))
        if not teams or outcome not in teams:
            diagnostics["unmatched"] += 1
            continue
        try:
            market_time = utc(row["Kickoff ET"])
        except (ValueError, TypeError, KeyError):
            diagnostics["unmatched"] += 1
            continue
        if market_time <= now:
            continue  # Elo forecasts are pregame only: NEVER compare to in-play price.
        candidates = [
            (abs((dt - market_time).total_seconds()), p, prob)
            for dt, p, prob in forecast_index.get(teams, [])
            if abs((dt - market_time).total_seconds()) <= 12 * 3600
        ]
        if not candidates:
            diagnostics["unmatched"] += 1
            continue
        _, match, home_prob = min(candidates, key=lambda t: t[0])
        ask = row.get("Best ask")
        bid = row.get("Best bid")
        try:
            ask = float(ask)
            if not 0 < ask < 1:
                raise ValueError("Non-executable displayed ask")
        except (ValueError, TypeError):
            diagnostics["missing_quote"] += 1
            continue
        try:
            bid = float(bid) if bid is not None else None
        except (ValueError, TypeError):
            bid = None
        if bid is not None and bid > ask:
            diagnostics["unmatched"] += 1
            continue  # crossed book, data inconsistency
        key = (market_time, tuple(sorted(teams)), outcome)
        if key in seen:
            continue
        seen.add(key)
        h_code = canonical(match["Home"])
        fair = home_prob if outcome == h_code else 1 - home_prob
        compared.append({
            "Game": row.get("Game"),
            "Kickoff (ET)": row["Kickoff ET"],
            "Outcome": row.get("Outcome"),
            "Model P": round(fair, 4),
            "Best ask": round(ask, 4),
            "Best bid": round(bid, 4) if bid is not None else None,
            "P − ask": round(fair - ask, 4),
            "Quote spread": round(ask - bid, 4) if bid is not None else None,
            "Market": row.get("Market"),
        })
        diagnostics["matched"] += 1
    return sorted(compared, key=lambda x: x["P − ask"], reverse=True), diagnostics
