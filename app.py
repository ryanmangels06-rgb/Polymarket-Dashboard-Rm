import json
import re
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Polymarket NFL Lab", page_icon="🏈", layout="wide")
st.title("🏈 Polymarket NFL Lab")
st.caption("Read-only research dashboard • Prices are snapshots, not guaranteed executable quotes")

@st.cache_data(ttl=300)
def nfl_metadata():
    r=requests.get("https://gamma-api.polymarket.com/sports",timeout=20)
    r.raise_for_status()
    sports=r.json()
    nfl=next((s for s in sports if str(s.get("sport","")).lower()=="nfl"),None)
    if not nfl:
        raise ValueError("NFL metadata unavailable from Polymarket")
    return nfl

@st.cache_data(ttl=60)
def nfl_events():
    nfl=nfl_metadata()
    series_ids=[x.strip() for x in str(nfl.get("series") or "").split(",") if x.strip().isdigit()]
    tag_ids=[x.strip() for x in str(nfl.get("tags") or "").split(",") if x.strip().isdigit()]
    if not series_ids and not tag_ids:
        raise ValueError("NFL series and tag identifiers unavailable")
    found={}
    filters=[{"series_id":sid} for sid in series_ids]
    filters += [{"tag_id":tid} for tid in tag_ids]
    for filter_params in filters:
        for offset in range(0,1000,100):
            params={"active":"true","closed":"false","limit":100,"offset":offset}
            params.update(filter_params)
            r=requests.get("https://gamma-api.polymarket.com/events",params=params,timeout=20)
            r.raise_for_status()
            batch=r.json()
            for event in batch:
                if event.get("closed"): continue
                found[str(event.get("id") or event.get("slug"))]=event
            if len(batch)<100: break
    return list(found.values())

@st.cache_data(ttl=30)
def order_book(token):
    r=requests.get("https://clob.polymarket.com/book",params={"token_id":str(token)},timeout=12)
    r.raise_for_status()
    return r.json()

def arr(v):
    if isinstance(v,list): return v
    if isinstance(v,str):
        try:
            x=json.loads(v)
            return x if isinstance(x,list) else []
        except ValueError: pass
    return []

if st.button("Refresh market data"):
    st.cache_data.clear()

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def kickoff(event):
    candidates=[m.get("gameStartTime") or m.get("eventStartTime") for m in event.get("markets",[])]
    candidates += [event.get("gameStartTime"),event.get("startTime")]
    for raw in candidates:
        if not raw: continue
        try:
            value=str(raw).replace(" ","T").replace("Z","+00:00")
            if value.endswith("+00"): value+=":00"
            dt=datetime.fromisoformat(value)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except ValueError: continue
    return None

now=datetime.now(timezone.utc)
mode=st.radio("Show games",["Live / today","Next 7 days","Next 14 days"],horizontal=True,index=0)
days=1 if mode=="Live / today" else (7 if mode=="Next 7 days" else 14)
team_filter=st.text_input("Optional team filter",value="")
market_type=st.selectbox("Market type",["Moneyline, spread and total","All game markets","Moneyline only","Spreads only","Totals only"])
try:
    events=nfl_events()
except (requests.RequestException,ValueError) as e:
    st.error(f"Could not load NFL markets: {e}")
    st.stop()

# A game may stay open after kickoff; allow a 5-hour live window.
local_tz=ZoneInfo("America/New_York")
today_local=now.astimezone(local_tz).date()
games=[]
for event in events:
    dt=kickoff(event)
    if dt is None: continue
    if dt < now-timedelta(hours=5) or dt > now+timedelta(days=days): continue
    if mode=="Live / today" and dt.astimezone(local_tz).date()!=today_local: continue
    name=event.get("title") or ""
    if team_filter.strip() and team_filter.lower() not in name.lower(): continue
    # Avoid season futures and non-matchup event groups.
    if not re.search(r"\\b(vs\\.?|versus|@)\\b",name,re.I) and " vs. " not in name.lower(): continue
    games.append((dt,event))
games.sort(key=lambda pair:pair[0])
st.caption(f"Snapshot: {now.astimezone(local_tz).strftime('%b %d, %Y %I:%M %p ET')} · Game times in Eastern Time")
st.write(f"**{len(games)} games** in selected window.")
if not games:
    st.info("No matching NFL games with verified kickoff times in this window. Try Next 7 days. Old games and undated markets are excluded.")

def market_category(m):
    typ=str(m.get("sportsMarketType") or "").lower()
    question=str(m.get("question") or "").lower()
    if typ=="moneyline" or (not typ and " vs. " in question and "spread" not in question and "o/u" not in question): return "Moneyline"
    if typ in ("spreads","spread") or question.startswith("spread:"): return "Spread"
    if typ in ("totals","total") or question.startswith("total:") or "total points: o/u" in question: return "Total"
    return "Other"

def include_market(m):
    category=market_category(m)
    if market_type=="All game markets": return True
    if market_type=="Moneyline only": return category=="Moneyline"
    if market_type=="Spreads only": return category=="Spread"
    if market_type=="Totals only": return category=="Total"
    return category!="Other"

rows=[]
for dt,event in games:
    title=event.get("title","NFL game")
    live=dt<=now<dt+timedelta(hours=5)
    st.subheader(("🔴 Live window · " if live else "🏈 ")+title)
    st.caption("Kickoff: "+dt.astimezone(local_tz).strftime("%a %b %d, %I:%M %p ET") + (" · kickoff passed; live status unverified" if live else ""))
    markets=[m for m in event.get("markets",[]) if not m.get("closed") and include_market(m)]
    if not markets:
        st.caption("No matching active lines for this game.")
        continue
    game_rows=[]
    for market in markets[:30]:
        outcomes=arr(market.get("outcomes"))
        tokens=arr(market.get("clobTokenIds"))
        prices=arr(market.get("outcomePrices"))
        for i,outcome in enumerate(outcomes):
            bid=ask=None
            if i<len(tokens):
                try:
                    book=order_book(tokens[i])
                    bids=[float(x["price"]) for x in book.get("bids",[])]
                    asks=[float(x["price"]) for x in book.get("asks",[])]
                    bid=max(bids) if bids else None
                    ask=min(asks) if asks else None
                except (requests.RequestException,ValueError,KeyError): pass
            record={"Game":title,"Kickoff ET":dt.astimezone(local_tz).isoformat(),"Type":market_category(market),"Market":market.get("question"),"Outcome":outcome,"Best bid":bid,"Best ask":ask,"Indicative price":prices[i] if i<len(prices) else None}
            game_rows.append(record)
            rows.append(record)
    if game_rows:
        st.dataframe(pd.DataFrame(game_rows).drop(columns=["Game","Kickoff ET"]),use_container_width=True,hide_index=True)
if rows:
    st.download_button("Export displayed game lines",pd.DataFrame(rows).to_csv(index=False),"nfl_upcoming_markets.csv","text/csv")
st.caption("Prices are market snapshots. Live window is based on kickoff time, not a verified game score. Missing bids/asks indicate no displayed quote.")

st.divider()
st.subheader("Paper-trading probability comparison")
st.caption("Supply your own independently estimated probability; this app does not yet have a trained NFL forecasting model.")
price=st.number_input("Market purchase price",0.01,0.99,0.55,0.01)
prob=st.number_input("Independent estimated probability",0.01,0.99,0.55,0.01)
buffer=st.number_input("Cost and uncertainty buffer",0.0,0.25,0.03,0.005)
st.metric("Raw probability difference",f"{prob-price:+.1%}")
st.metric("Difference after assumed buffer",f"{prob-price-buffer:+.1%}")
st.warning("Positive differences are hypothetical, not evidence of a profitable strategy. Validate forecasts and costs before drawing conclusions.")
