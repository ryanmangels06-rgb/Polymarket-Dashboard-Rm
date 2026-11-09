"""Read-only Polymarket US NFL feed.

Uses the OFFICIAL US SDK without credentials. Never creates orders or reads
private accounts. API payloads and quote freshness must be checked at use time.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
import re
from market_schema import normalize

NFL_PATTERN = re.compile(r"(?:\bnfl\b|national football league)", re.IGNORECASE)
MATCHUP_PATTERN = re.compile(r"\b(?:vs\.?|versus)\b|(?:\s@\s)", re.IGNORECASE)


def when_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def amount(value: Any) -> float | None:
    try:
        raw = value.get("value") if isinstance(value, dict) else value
        result = float(raw)
        if result < 0 or result > 1:
            return None
        return result
    except (TypeError, ValueError):
        return None


def is_nfl_event(event: dict, tagged_query: bool = False) -> bool:
    title = str(event.get("title") or "")
    tags = event.get("tags") or []
    labels = " ".join(
        str(t.get("slug") or "") + " " + str(t.get("label") or "")
        for t in tags if isinstance(t, dict)
    )
    series = event.get("series") or {}
    series_name = (str(series.get("slug") or "") + " " + str(series.get("title") or "")
                   if isinstance(series, dict) else "")
    return bool(tagged_query or NFL_PATTERN.search(labels + " " + series_name + " " + title))


def is_matchup(event: dict) -> bool:
    return bool(MATCHUP_PATTERN.search(str(event.get("title") or "")))


def discover_events(client: Any, now: datetime, days: int, max_events: int = 80) -> tuple[list[dict], list[str]]:
    cutoff = now + timedelta(days=days)
    warnings = []
    found: dict[str, dict] = {}
    # Explicit US NFL tag filtering, instead of broad markets that match
    # misleading terms such as "inflation".
    try:
        for offset in range(0, 500, 100):
            page = client.events.list({
                "limit": 100, "offset": offset, "active": True,
                "closed": False, "tagSlug": "nfl",
            })
            batch = page.get("events") or []
            for event in batch:
                if is_nfl_event(event, tagged_query=True) and is_matchup(event):
                    found[str(event.get("id") or event.get("slug"))] = event
            if len(batch) < 100:
                break
    except Exception as exc:
        warnings.append("US NFL tag query failed: " + str(exc)[:170])

    # Search is a backup. It is not used for market prices, which come from BBO.
    if not found:
        try:
            result = client.search.query({"query": "NFL", "limit": 100, "status": "active"})
            for event in result.get("events") or []:
                if is_nfl_event(event) and is_matchup(event):
                    found[str(event.get("id") or event.get("slug"))] = event
        except Exception as exc:
            warnings.append("US NFL search fallback failed: " + str(exc)[:170])

    selected = []
    for event in found.values():
        if event.get("closed") or event.get("active") is False:
            continue
        kickoff = when_utc(event.get("startTime"))
        # The exchange's startTime may not always be kickoff. Keep it clearly labeled.
        if kickoff is None:
            continue
        if now - timedelta(hours=5) <= kickoff <= cutoff:
            selected.append(event)
    selected.sort(key=lambda item: when_utc(item["startTime"]))
    return selected[:max_events], warnings


def fetch_us_nfl_snapshot(days: int = 7, max_events: int = 30, max_quotes: int = 100,
                          client: Any = None, now: datetime | None = None) -> dict:
    if days < 1 or max_events < 1 or max_quotes < 1:
        raise ValueError("Require positive days/events/quotes limits")
    now = now or datetime.now(timezone.utc)
    own_client = client is None
    if own_client:
        from polymarket_us import PolymarketUS
        client = PolymarketUS()  # NO key_id, NO secret_key

    try:
        events, warnings = discover_events(client, now, days, max_events)
        games, quote_count = [], 0
        for event in events:
            markets = []
            for market in event.get("markets") or []:
                if market.get("closed") or market.get("active") is False:
                    continue
                slug = market.get("slug")
                if not slug:
                    continue
                # Public best-bid/offer endpoint (no personal account access).
                if quote_count >= max_quotes:
                    break
                quote_count += 1
                captured = datetime.now(timezone.utc).isoformat()
                try:
                    payload = client.markets.bbo(str(slug))
                    q = payload.get("marketData") or {}
                    bid, ask = amount(q.get("bestBid")), amount(q.get("bestAsk"))
                    warning = None
                    if bid is not None and ask is not None and bid > ask:
                        warning = "Crossed quote; excluded"
                        bid, ask = None, None
                except Exception as exc:
                    bid = ask = None
                    warning = "Quote unavailable: " + str(exc)[:120]
                normalized=normalize(market,{"marketData":{"bestBid":bid,"bestAsk":ask}},captured)
                markets.append({
                    "slug": normalized["slug"],
                    "title": normalized["market"],
                    "outcome": normalized["side"] if normalized["side_verified"] else "",
                    "side_verified": normalized["side_verified"],
                    "side_source": normalized["side_source"],
                    "type": normalized["type"],
                    "line": normalized["line"],
                    "bid": normalized["bid"],
                    "ask": normalized["ask"],
                    "spread": normalized["width"],
                    "liquidity": normalized["liquidity"],
                    "volume": normalized["volume"],
                    "quote_checked_utc": captured,
                    "quote_warning": warning,
                })
            if markets:
                games.append({
                    "event_id": event.get("id"),
                    "event_slug": str(event.get("slug") or ""),
                    "title": str(event.get("title") or ""),
                    "event_start_time_utc": when_utc(event.get("startTime")).isoformat(),
                    "markets": markets,
                })
            if quote_count >= max_quotes:
                break
        return {
            "source": "Polymarket US (polymarket.us) — official public SDK",
            "exchange": "polymarket_us",
            "fetched_utc": datetime.now(timezone.utc).isoformat(),
            "quote_count": quote_count,
            "games_count": len(games),
            "events_considered": len(events),
            "warnings": warnings,
            "games": games,
            "execution_warning": "Read-only data; refresh the exact market before trading. No order execution.",
        }
    finally:
        if own_client:
            close = getattr(client, "close", None)
            if callable(close):
                close()
