"""Conservative parsing of Polymarket US market metadata.

Only structured market data is used to decide which contract pays out.
No parsing of slugs/symbols for contract meaning.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json


def metadata(market):
    value = market.get("metadata") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    return value if isinstance(value, dict) else {}


def category(market):
    meta = metadata(market)
    kind = str(meta.get("market_sport_type") or "").lower()
    if kind == "football_team_full_game_winner":
        return "Moneyline"
    if kind == "football_team_full_game_spread":
        return "Spread"
    if kind == "football_team_full_game_total":
        return "Total"
    if kind:
        return "Props & other"
    fallback = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "").lower()
    if "moneyline" in fallback or fallback == "football_team_full_game_winner":
        return "Moneyline"
    if "spread" in fallback:
        return "Spread"
    if "total" in fallback:
        return "Total"
    return "Props & other"


def paying_side(market):
    """Only return outcome if explicitly identified by structured fields."""
    meta = metadata(market)
    kind = category(market)
    if kind == "Total" and str(meta.get("market_sport_type", "")).lower() == "football_team_full_game_total":
        strike = meta.get("outcome_strike")
        if strike is not None and str(strike).strip():
            return "Over " + str(strike), True, "metadata.market_sport_type/outcome_strike"
    names = []
    for side in market.get("marketSides") or []:
        if not isinstance(side, dict) or side.get("long") is not True:
            continue
        team = side.get("team") or {}
        name = team.get("name") if isinstance(team, dict) else None
        if name:
            names.append(str(name).strip())
        elif side.get("description"):
            names.append(str(side["description"]).strip())
    if len(set(names)) == 1 and names[0]:
        return names[0], True, "marketSides.long.team"
    long_name = meta.get("long_participant_name")
    if long_name and kind in ("Moneyline", "Spread"):
        return str(long_name), True, "metadata.long_participant_name"
    return "Side not verified", False, "missing structured outcome"


def line_description(market):
    meta = metadata(market)
    kind = category(market)
    strike = meta.get("outcome_strike")
    if strike is None:
        strike = market.get("line")
    label = str(strike) if strike is not None else "—"
    if kind == "Moneyline":
        return "Winner"
    if kind == "Spread":
        return "Handicap " + label + " (check sign)"
    if kind == "Total":
        return "Total " + label
    return label if label != "—" else str(market.get("titleShort") or market.get("question") or market.get("title") or "Other")


def value_price(value):
    if isinstance(value, dict):
        value = value.get("value")
    try:
        price = float(value)
        return price if 0 <= price <= 1 else None
    except (ValueError, TypeError):
        return None


def to_utc(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def normalize(market, bbo, quote_time=None):
    side, verified, source = paying_side(market)
    raw = bbo.get("marketData") or {}
    bid = value_price(raw.get("bestBid"))
    ask = value_price(raw.get("bestAsk"))
    flags = []
    if not verified:
        flags.append("Unverified contract side")
    if bid is None or ask is None:
        flags.append("Missing bid or ask")
    if bid is not None and ask is not None and bid > ask:
        flags.append("Crossed order book")
        bid, ask = None, None
    if bid is not None and ask is not None and ask-bid > .10:
        flags.append("Wide spread")
    return {
        "slug": str(market.get("slug") or ""),
        "market": str(market.get("titleShort") or market.get("question") or market.get("title") or "Market"),
        "type": category(market),
        "side": side,
        "side_verified": verified,
        "side_source": source,
        "line": line_description(market),
        "bid": bid,
        "ask": ask,
        "width": round(ask-bid, 5) if ask is not None and bid is not None else None,
        "quote_checked_utc": quote_time or datetime.now(timezone.utc).isoformat(),
        "flags": flags,
        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
        "fee_coefficient": market.get("feeCoefficient"),
        "market_sport_type": metadata(market).get("market_sport_type"),
    }
