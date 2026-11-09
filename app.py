import json
import re
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Polymarket NFL Lab", page_icon="🏈", layout="wide")
st.title("🏈 Polymarket NFL Lab")
st.caption("Read-only research dashboard • International Polymarket API data, NOT Polymarket US prices • Quotes are snapshots")


# ------------------- US exchange (the exchange used at polymarket.us) -------------------
st.divider()
st.subheader("🇺🇸 Polymarket US — NFL market quotes")
st.caption("US exchange only. No Apple ID, personal account access, API secret, or order placement required.")
from us_feed import fetch_us_nfl_snapshot
us_days = st.selectbox("US game window", options=[2,7], format_func=lambda d: "Today / next 48 hours" if d == 2 else "Next 7 days", index=0)
us_auto = st.toggle("Auto-check US quotes every 30 seconds while this page is open",value=False)
if st.button("Refresh US market feed now",key="us_force_refresh"):
    st.cache_data.clear()

@st.cache_data(ttl=25,show_spinner=False)
def get_us_feed(days):
    return fetch_us_nfl_snapshot(days=days,max_events=24,max_quotes=90)

@st.fragment(run_every="30s" if us_auto else None)
def show_us_feed():
    try:
        feed=get_us_feed(us_days)
    except Exception as exc:
        st.error("Polymarket US feed could not be retrieved: " + str(exc)[:250])
        st.caption("The international data shown below is a different exchange and should not be substituted for a US quote.")
        return
    fetched=feed.get("fetched_utc","")
    st.caption(f"US quote snapshot captured: {fetched} · {feed.get('games_count',0)} matchups · {feed.get('quote_count',0)} quote requests")
    for warning in feed.get("warnings",[]):
        st.warning(warning)
    games_us=feed.get("games") or []
    if not games_us:
        st.info("No upcoming NFL games with US quotes returned. This does not mean no US markets exist; the provider may use different event tags/times or limits.")
        return
    options=[f"{g['title']} — {g.get('event_start_time_utc','')[:16]} UTC" for g in games_us]
    chosen=st.selectbox("Choose US NFL game",options,key="us_game_select")
    game=games_us[options.index(chosen)]
    st.write(f"**{game['title']}**")
    st.caption("Exchange event start time (not independently verified kickoff): "+game["event_start_time_utc"])
    table=[]
    for market in game.get("markets",[]):
        table.append({
            "Market":market["title"],"Outcome":market["outcome"],
            "Best bid":market["bid"],"Best ask":market["ask"],
            "Spread":market["spread"],"Quote checked UTC":market["quote_checked_utc"],
            "Market slug":market["slug"],
        })
    if table:
        st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True)
        st.download_button("Download this US game's quotes",pd.DataFrame(table).to_csv(index=False),"polymarket_us_game_quotes.csv","text/csv",key="us_game_csv")
    st.caption("No trade recommendation. Quotes can move during live NFL games. Always reconfirm price, depth, availability, fees, and market rules on Polymarket US.")
show_us_feed()
st.divider()
st.subheader("🌍 International Polymarket — separate comparison feed")
st.caption("The historical international feed below is NOT the US exchange. Do not treat its prices as your US execution prices.")

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
st.subheader("Auto-updating matchup quotes")
auto_on=st.toggle("Refresh selected game's moneyline quotes every 30 seconds while this dashboard is open",value=False)
@st.fragment(run_every="30s" if auto_on else None)
def live_moneyline_fragment():
    if not games:
        st.info("No qualifying games to monitor in this time window.")
        return
    options={f"{dt.astimezone(local_tz).strftime('%b %d, %I:%M %p ET')} — {event.get('title','NFL game')}":(dt,event) for dt,event in games}
    label=st.selectbox("Game to monitor",list(options.keys()),key="live_moneyline_game")
    dt,event=options[label]
    qualifying=[market for market in event.get("markets",[]) if not market.get("closed") and market_category(market)=="Moneyline"]
    fresh_rows=[]
    for market in qualifying[:2]:
        tokens=arr(market.get("clobTokenIds"))
        for i,outcome in enumerate(arr(market.get("outcomes"))):
            if i>=len(tokens): continue
            try:
                book=order_book(tokens[i])
                bids=[float(x["price"]) for x in book.get("bids",[])]
                asks=[float(x["price"]) for x in book.get("asks",[])]
                fresh_rows.append({
                    "Outcome":outcome,
                    "Best bid":max(bids) if bids else None,
                    "Best ask":min(asks) if asks else None,
                    "Market":market.get("question"),
                })
            except (requests.RequestException,ValueError,KeyError):
                fresh_rows.append({"Outcome":outcome,"Best bid":None,"Best ask":None,"Market":market.get("question")})
    if fresh_rows:
        st.dataframe(pd.DataFrame(fresh_rows),hide_index=True,use_container_width=True)
    else:
        st.info("No moneyline order-book quotes available for this game.")
    st.caption("Checked at "+datetime.now(timezone.utc).astimezone(local_tz).strftime("%I:%M:%S %p ET")+" · Public international Polymarket quotes only. Session must stay open; no automatic ChatGPT message or real-money trading.")
live_moneyline_fragment()



st.divider()
st.subheader("Paper-trading probability comparison")
st.caption("Supply your own independently estimated probability; this app does not yet have a trained NFL forecasting model.")
price=st.number_input("Market purchase price",0.01,0.99,0.55,0.01)
prob=st.number_input("Independent estimated probability",0.01,0.99,0.55,0.01)
buffer=st.number_input("Cost and uncertainty buffer",0.0,0.25,0.03,0.005)
st.metric("Raw probability difference",f"{prob-price:+.1%}")
st.metric("Difference after assumed buffer",f"{prob-price-buffer:+.1%}")
st.warning("Positive differences are hypothetical, not evidence of a profitable strategy. Validate forecasts and costs before drawing conclusions.")

st.divider()
st.subheader("International Polymarket wallet viewer (not Polymarket US)")
st.caption("This viewer is for the international Polymarket platform and cannot connect your Polymarket US Apple ID account. Optional: enter a PUBLIC Polymarket proxy-wallet address. Never enter a seed phrase, private key, password, or API secret. This app does not place trades.")
wallet=st.text_input("Public wallet address (0x + 40 hexadecimal characters)",value="",placeholder="0x...",type="default")
if wallet:
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}",wallet.strip()):
        st.warning("Enter a valid public Ethereum-format wallet address (0x followed by 40 hex characters).")
    elif st.button("Load public portfolio snapshot"):
        try:
            base="https://data-api.polymarket.com"
            response=requests.get(base+"/v2/positions",params={"user":wallet.strip()},timeout=20)
            response.raise_for_status()
            payload=response.json()
            positions=payload.get("data",[]) if isinstance(payload,dict) else payload
            if isinstance(positions,list) and positions:
                st.dataframe(pd.DataFrame(positions),use_container_width=True,hide_index=True)
                st.caption("Public data only. Values and P/L are provider estimates and may lag.")
            else:
                st.info("No open positions returned for this wallet.")
        except (requests.RequestException,ValueError) as error:
            st.error(f"Portfolio data unavailable: {error}")
st.caption("This public dashboard does not save wallet addresses to a database, but URLs, server logs and browser sessions can still expose information. Avoid using an address you want to keep private.")

st.divider()
st.subheader("Model evaluation workbench")
st.caption("Research-only: enter a hypothetical probability and executable ask to see estimated edge and break-even probability. No NFL model is trained yet.")
with st.expander("Expected value calculator",expanded=True):
    ask=st.number_input("Contract ask price ($)",min_value=0.001,max_value=0.999,value=0.55,step=0.01,key="eval_ask")
    p=st.number_input("Independent probability estimate",min_value=0.001,max_value=0.999,value=0.55,step=0.01,key="eval_p")
    fee=st.number_input("Estimated all-in fees per contract ($)",min_value=0.0,max_value=0.50,value=0.0,step=0.005,key="eval_fee")
    uncertainty=st.number_input("Probability uncertainty margin (percentage points)",min_value=0.0,max_value=30.0,value=3.0,step=0.5,key="eval_margin")/100
    cost=ask+fee
    ev=p-cost
    st.metric("Hypothetical expected net value / contract",f"${ev:+.3f}")
    st.metric("Break-even probability",f"{cost:.1%}")
    st.metric("Conservative estimated difference",f"{p-uncertainty-cost:+.1%}")
    if cost>=1:
        st.warning("All-in contract cost is at least the maximum $1 payout.")
    st.caption("Formula: EV = estimated probability × $1 payout − ask − estimated fees. Excludes price changes, slippage, and model error. This is not a trade recommendation.")

st.divider()
st.subheader("NFL forecasting prototype — Elo baseline")
st.caption("Experimental probability calculator. Default team ratings are identical placeholders, not trained ratings or current-season statistics.")
import math
NFL_TEAMS=["Arizona Cardinals","Atlanta Falcons","Baltimore Ravens","Buffalo Bills","Carolina Panthers","Chicago Bears","Cincinnati Bengals","Cleveland Browns","Dallas Cowboys","Denver Broncos","Detroit Lions","Green Bay Packers","Houston Texans","Indianapolis Colts","Jacksonville Jaguars","Kansas City Chiefs","Las Vegas Raiders","Los Angeles Chargers","Los Angeles Rams","Miami Dolphins","Minnesota Vikings","New England Patriots","New Orleans Saints","New York Giants","New York Jets","Philadelphia Eagles","Pittsburgh Steelers","San Francisco 49ers","Seattle Seahawks","Tampa Bay Buccaneers","Tennessee Titans","Washington Commanders"]
with st.expander("Calculate an illustrative game probability",expanded=True):
    home=st.selectbox("Home team",NFL_TEAMS,index=NFL_TEAMS.index("Seattle Seahawks"))
    away=st.selectbox("Away team",[t for t in NFL_TEAMS if t!=home],index=0)
    home_rating=st.number_input("Home-team Elo rating (manual)",min_value=800,max_value=2200,value=1500,step=10)
    away_rating=st.number_input("Away-team Elo rating (manual)",min_value=800,max_value=2200,value=1500,step=10)
    home_advantage=st.slider("Home-field Elo adjustment",0,120,45,5)
    delta=home_rating+home_advantage-away_rating
    home_p=1/(1+10**(-delta/400))
    st.metric(f"Illustrative {home} win probability",f"{home_p:.1%}")
    st.metric(f"Illustrative {away} win probability",f"{1-home_p:.1%}")
    st.caption("Elo logistic formula: 1 / (1 + 10^(-(home rating + home advantage - away rating)/400)). Not calibrated to NFL outcomes. No injuries, roster changes, or current team statistics included.")
    st.warning("Do not interpret this probability as an independent validated edge. Historical game data and out-of-sample calibration are required.")

st.divider()
st.subheader("Historical NFL Elo — walk-forward evaluation")
st.caption("Uses completed NFL games from nflverse. Predictions are generated before each game's rating update, never from its result. 2026 is a held-out evaluation season.")
@st.cache_data(ttl=3600,show_spinner=False)
def load_nfl_history():
    url="https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
    frame=pd.read_csv(url,low_memory=False)
    required={"season","week","home_team","away_team","home_score","away_score","game_type"}
    missing=required-set(frame.columns)
    if missing: raise ValueError("Missing schedule columns: "+", ".join(sorted(missing)))
    frame=frame[frame["game_type"].isin(["REG","WC","DIV","CON","SB"])].copy()
    frame["season"]=pd.to_numeric(frame["season"],errors="coerce")
    frame["week"]=pd.to_numeric(frame["week"],errors="coerce")
    frame["home_score"]=pd.to_numeric(frame["home_score"],errors="coerce")
    frame["away_score"]=pd.to_numeric(frame["away_score"],errors="coerce")
    frame=frame[(frame["season"]>=2018)&(frame["season"]<=2026)].copy()
    # nflverse gametime is a 24-hour Eastern Time kickoff field; gameday alone is midnight, not kickoff.
    local_start=pd.to_datetime(frame["gameday"].astype(str)+" "+frame["gametime"].astype(str),format="%Y-%m-%d %H:%M",errors="coerce")
    frame["game_datetime"]=local_start.dt.tz_localize("America/New_York",ambiguous="NaT",nonexistent="shift_forward").dt.tz_convert("UTC")
    frame=frame.dropna(subset=["season","week","home_team","away_team","game_datetime"])
    return frame.sort_values(["game_datetime","week","home_team","away_team"]).reset_index(drop=True)

def elo_walkforward(frame,k_factor=20,home_edge=45,regression=0.25):
    ratings={}
    previous_season=None
    predictions=[]
    pending=[]
    as_of=pd.Timestamp.now(tz="UTC")
    for _,g in frame.iterrows():
        dt=g["game_datetime"]
        # A final score from a simultaneous or just-finished game cannot influence another kickoff.
        due=[item for item in pending if item[0]<=dt]
        pending=[item for item in pending if item[0]>dt]
        for _,h_team,a_team,change in due:
            ratings[h_team]=ratings.get(h_team,1500.0)+change
            ratings[a_team]=ratings.get(a_team,1500.0)-change
        season=int(g["season"])
        if previous_season is not None and season!=previous_season:
            ratings={team:1500+(rating-1500)*(1-regression) for team,rating in ratings.items()}
        previous_season=season
        home=str(g["home_team"]);away=str(g["away_team"])
        h=ratings.get(home,1500.0);a=ratings.get(away,1500.0)
        neutral=str(g.get("location","")).lower()=="neutral"
        advantage=0 if neutral else home_edge
        probability=1/(1+10**(-(h+advantage-a)/400))
        hs=g["home_score"];ascore=g["away_score"]
        completed=pd.notna(hs) and pd.notna(ascore) and dt+pd.Timedelta(hours=5)<=as_of
        actual=None if not completed else (1.0 if hs>ascore else 0.0 if hs<ascore else 0.5)
        predictions.append({"Date":dt,"Season":season,"Week":int(g["week"]),"Home":home,"Away":away,"Home win probability":probability,"Result":actual,"Home score":hs if completed else None,"Away score":ascore if completed else None,"Neutral":neutral})
        if completed:
            pending.append((dt+pd.Timedelta(hours=5),home,away,k_factor*(actual-probability)))
    for due_dt,h_team,a_team,change in pending:
        if due_dt<=as_of:
            ratings[h_team]=ratings.get(h_team,1500.0)+change
            ratings[a_team]=ratings.get(a_team,1500.0)-change
    return pd.DataFrame(predictions),ratings

try:
    history=load_nfl_history()
    st.caption("Data source: nflverse/nfldata games.csv. Kickoff timestamps use nflverse Eastern Time game times, converted to UTC. A five-hour score delay prevents using same-time games as foreknowledge. Not a live score feed.")
    training=history[history["season"]<=2025]
    if training.empty: raise ValueError("No historical training games available")
    candidates=[(k,adv) for k in (12,20,30) for adv in (30,45,60)]
    score_grid=[]
    for k,adv in candidates:
        trial,_=elo_walkforward(training,k,adv)
        validation=trial[(trial["Season"]>=2023)&(trial["Season"]<=2025)&trial["Result"].isin([0,1])]
        if len(validation)<100: continue
        brier=float(((validation["Home win probability"]-validation["Result"])**2).mean())
        score_grid.append((brier,k,adv))
    if not score_grid: raise ValueError("Insufficient completed validation games")
    best_brier,best_k,best_adv=min(score_grid)
    predicted,latest_ratings=elo_walkforward(history,best_k,best_adv)
    holdout=predicted[(predicted["Season"]==2026)&predicted["Result"].isin([0,1])]
    validation_games=predicted[(predicted["Season"]>=2023)&(predicted["Season"]<=2025)&predicted["Result"].isin([0,1])]
    st.write(f"**Selected parameters:** Elo K={best_k}, home adjustment={best_adv}, offseason regression=25%. Selected using 2023–2025 completed games; 2026 was not used for selection.")
    a,b,c_metric=st.columns(3)
    a.metric("2023–25 validation Brier",f"{best_brier:.3f}")
    b.metric("2026 holdout games",str(len(holdout)))
    if len(holdout):
        holdout_brier=float(((holdout["Home win probability"]-holdout["Result"])**2).mean())
        c_metric.metric("2026 holdout Brier",f"{holdout_brier:.3f}")
    else:
        c_metric.metric("2026 holdout Brier","Not available")
    st.caption("Brier score: lower is better (0 is perfect). A 0.25 Brier corresponds to constant 50% forecasts for decisive games. Historical validation is used to select parameters, so it is not an untouched test set.")
    if len(holdout):
        baseline=float(((holdout["Result"].mean()-holdout["Result"])**2).mean())
        st.caption(f"2026 constant-rate hindsight benchmark: {baseline:.3f} Brier. This benchmark uses holdout outcomes and is descriptive only, not a fair predictive baseline.")
    upcoming=predicted[(predicted["Season"]==2026)&predicted["Result"].isna()].copy()
    if len(upcoming):
        upcoming=upcoming[upcoming["Date"]>=pd.Timestamp.now(tz="UTC")-pd.Timedelta(hours=5)]
        st.write("**Upcoming scheduled games — Elo baseline**")
        upcoming["Home win %"]=(100*upcoming["Home win probability"]).round(1)
        st.dataframe(upcoming[["Date","Week","Away","Home","Home win %"]].head(25),hide_index=True,use_container_width=True,column_config={"Home win %":st.column_config.ProgressColumn("Home win %",min_value=0,max_value=100,format="%.1f%%")})
    else:
        st.info("No upcoming 2026 games found in the schedule feed.")
    # True prospective baseline uses only pre-2026 completed games, not outcomes from the holdout.
    base_home_wins=float(training.loc[training["home_score"].notna() & training["away_score"].notna(),"home_score"].gt(training.loc[training["home_score"].notna() & training["away_score"].notna(),"away_score"]).mean())
    if len(holdout):
        home_baseline=float(((base_home_wins-holdout["Result"])**2).mean())
        model_brier=float(((holdout["Home win probability"]-holdout["Result"])**2).mean())
        st.caption(f"Fairer constant home-win baseline from pre-2026 games: {home_baseline:.3f} Brier on 2026 holdout (assumed home probability {base_home_wins:.1%}). Model improvement: {home_baseline-model_brier:+.3f}.")
    st.write("**Calibration check (2026 holdout)**")
    if len(holdout)>=20:
        calibrated=holdout.copy()
        calibrated["Forecast band"]=pd.cut(calibrated["Home win probability"],bins=[0,0.2,0.4,0.6,0.8,1],include_lowest=True,labels=["0–20%","20–40%","40–60%","60–80%","80–100%"])
        calibration=calibrated.groupby("Forecast band",observed=True).agg(Games=("Result","size"),Average_forecast=("Home win probability","mean"),Observed_home_win_rate=("Result","mean")).reset_index()
        st.dataframe(calibration.rename(columns={"Average_forecast":"Average model P","Observed_home_win_rate":"Actual home win rate"}),hide_index=True,use_container_width=True)
    st.write("**Current team Elo ratings**")
    ratings_df=pd.DataFrame([{"Team":team,"Elo":round(value,1)} for team,value in latest_ratings.items()]).sort_values("Elo",ascending=False)
    st.dataframe(ratings_df,hide_index=True,use_container_width=True)

    # Portable, read-only summary for analysis in ChatGPT. Does not carry credentials.
    next_games=[]
    for _,game in upcoming.head(30).iterrows():
        next_games.append({
            "kickoff_utc":game["Date"].isoformat(),
            "home":str(game["Home"]),
            "away":str(game["Away"]),
            "home_probability":round(float(game["Home win probability"]),4),
        })
    model_report={
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "source":"nflverse games.csv, historical Elo model",
        "exchange":"None; NFL model probabilities only (not Polymarket US odds)",
        "model":"Elo pregame baseline",
        "parameter_selection":"2023-2025 validation; 2026 holdout",
        "elo_k":best_k,
        "home_advantage_elo":best_adv,
        "validation_brier":round(best_brier,5),
        "holdout_count":len(holdout),
        "holdout_brier":round(float(((holdout["Home win probability"]-holdout["Result"])**2).mean()),5) if len(holdout) else None,
        "upcoming":next_games,
        "team_ratings":{str(team):round(float(value),1) for team,value in latest_ratings.items()},
        "limitations":["Not live scores","No QB/injury data","Not calibrated to Polymarket US prices","Not proof of positive expected returns"]
    }
    st.download_button("Download model report for ChatGPT",json.dumps(model_report,indent=2),"nfl_model_report.json","application/json")
    st.caption("You can upload this report into this ChatGPT conversation for analysis. It does not continuously sync by itself.")
    with st.expander("Inspect held-out predictions and download"):
        if len(holdout):
            st.dataframe(holdout[["Date","Home","Away","Home win probability","Result"]],hide_index=True,use_container_width=True)
            st.download_button("Download 2026 evaluation CSV",holdout.to_csv(index=False),"nfl_elo_holdout_2026.csv","text/csv")
    st.warning("Model limitations: no injuries, starting-QB updates, weather, rest/travel, point-spread modeling, calibration adjustment, or US market integration. Ratings are game-result-only. Do not assume positive expected value from these forecasts.")
except (requests.RequestException,ValueError,KeyError,TypeError,ImportError) as exc:
    st.error(f"Historical Elo unavailable: {exc}")
