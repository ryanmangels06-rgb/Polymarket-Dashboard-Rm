"""NFL Lab — fast, read-only research interface. Saved data never blocks the UI."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NFL Lab | Market Intelligence", page_icon="🏈", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container {max-width:1280px;padding-top:1.5rem;padding-bottom:3rem}
h1 {letter-spacing:-.045em;font-weight:800}
h2,h3 {letter-spacing:-.025em}
div[data-testid="stMetric"] {border:1px solid rgba(125,125,125,.2);border-radius:14px;padding:15px}
div[data-testid="stMetricLabel"] {font-size:.85rem}
div[data-testid="stMetricValue"] {font-size:1.65rem}
.stTabs [data-baseweb="tab"] {font-size:1rem;font-weight:650}
div[data-testid="stAlert"] {border-radius:12px}
</style>
""", unsafe_allow_html=True)

ET = ZoneInfo("America/New_York")
def parse_time(raw):
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z","+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None

def cents(value):
    try:
        return f"{float(value)*100:.2f}¢" if value is not None else "—"
    except (TypeError, ValueError):
        return "—"

@st.cache_data(ttl=90,show_spinner=False)
def read_report():
    path=Path("data/latest_us_nfl.json")
    if not path.exists():
        return {}, "No local snapshot found"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return {}, str(exc)

st.title("🏈 NFL LAB")
st.caption("GAME INTELLIGENCE  /  US MARKET MONITOR  /  READ-ONLY RESEARCH")
report,error=read_report()
now=datetime.now(timezone.utc)
stamp=parse_time(report.get("fetched_utc"))
age=(now-stamp).total_seconds()/60 if stamp else None
games=report.get("games") or []
games=sorted(games,key=lambda g:g.get("event_start_time_utc") or "")
upcoming=[g for g in games if (parse_time(g.get("event_start_time_utc")) or now-timedelta(days=365))>=now-timedelta(hours=5)]
total_contracts=sum(len(g.get("markets") or []) for g in games)
verified=sum(m.get("side_verified") is True and m.get("ask") is not None for g in games for m in g.get("markets") or [])
usable=verified
c1,c2,c3,c4=st.columns(4)
c1.metric("Games in archive",len(games))
c2.metric("Contracts captured",total_contracts)
c3.metric("Comparable quotes",usable)
c4.metric("Snapshot age",f"{age:.0f} min" if age is not None else "Unknown")
if error:
    st.error("Saved market report unavailable: "+error)
elif age is None or age>5:
    st.warning("Archived prices are not live. Market prices and outcomes must be reverified on the exchange.")
if not usable:
    st.info("No verified contract-side + ask-price pairs in the saved US feed. Rankings are intentionally disabled rather than showing misleading recommendations.")

st.divider()
st.subheader("Independent NFL game data")
st.caption("ESPN game feed — separate from Polymarket quotes. This is football information, not a sportsbook line.")

@st.cache_data(ttl=120,show_spinner=False)
def load_espn_scoreboard(date_str):
    import requests
    url="https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    response=requests.get(url,params={"dates":date_str},timeout=(3,6))
    response.raise_for_status()
    return response.json()

game_date=st.date_input("NFL schedule date",value=now.astimezone(ET).date())
try:
    espn=load_espn_scoreboard(game_date.strftime("%Y%m%d"))
    espn_rows=[]
    for event in espn.get("events",[]):
        comp=(event.get("competitions") or [{}])[0]
        participants=comp.get("competitors") or []
        home=next((x for x in participants if x.get("homeAway")=="home"),{})
        away=next((x for x in participants if x.get("homeAway")=="away"),{})
        status=event.get("status") or {}
        detail=(status.get("type") or {}).get("shortDetail") or "—"
        espn_rows.append({
            "Game":f"{away.get('team',{}).get('displayName','Away')} @ {home.get('team',{}).get('displayName','Home')}",
            "Away record":(away.get("records") or [{}])[0].get("summary","—"),
            "Home record":(home.get("records") or [{}])[0].get("summary","—"),
            "Score":f"{away.get('score','—')} - {home.get('score','—')}" if (status.get("type") or {}).get("state")!="pre" else "Not started",
            "Status":detail,
        })
    if espn_rows:
        st.dataframe(pd.DataFrame(espn_rows),hide_index=True,width="stretch")
        st.caption(f"{len(espn_rows)} games returned by independent NFL scoreboard feed.")
    else:
        st.info("No games returned by ESPN for this date.")
except Exception as exc:
    st.warning("Independent NFL scoreboard temporarily unavailable: "+str(exc)[:130])
st.caption("Source: ESPN unofficial public scoreboard endpoint; subject to provider availability and terms.")

# Independent player box scores: passing, rushing, receiving, and other published categories.
st.markdown("#### Player statistics by game")
if 'espn' in locals() and espn.get("events"):
    event_options=espn["events"]
    chosen=st.selectbox("Select NFL game for player statistics",range(len(event_options)),
        format_func=lambda i:event_options[i].get("name") or event_options[i].get("shortName") or str(event_options[i].get("id")),
        key="player_game")
    event_id=event_options[chosen].get("id")
    @st.cache_data(ttl=180,show_spinner=False)
    def load_espn_boxscore(event_id):
        import requests
        res=requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
                         params={"event":str(event_id)},timeout=(3,7))
        res.raise_for_status()
        return res.json()
    try:
        detail=load_espn_boxscore(event_id)
        players=(detail.get("boxscore") or {}).get("players") or []
        categories={}
        for team in players:
            team_name=(team.get("team") or {}).get("displayName") or "Team"
            for stat in team.get("statistics") or []:
                name=stat.get("name") or stat.get("label") or "Other"
                labels=stat.get("labels") or []
                for athlete in stat.get("athletes") or []:
                    who=(athlete.get("athlete") or {}).get("displayName") or "Unknown"
                    values=athlete.get("stats") or []
                    record={"Team":team_name,"Player":who}
                    record.update({str(k):v for k,v in zip(labels,values)})
                    categories.setdefault(name,[]).append(record)
        if categories:
            chosen_stat=st.selectbox("Player stat category",list(categories),key="box_stat")
            df_stats=pd.DataFrame(categories[chosen_stat])
            st.dataframe(df_stats,hide_index=True,width="stretch")
            st.download_button("Download player statistics",df_stats.to_csv(index=False),"nfl_player_stats.csv","text/csv")
            st.caption("Actual game box-score statistics, not projections. ESPN public summary feed.")
        else:
            st.info("No player box-score stats are available yet for this game (often true before kickoff).")
    except Exception as exc:
        st.warning("Player statistics unavailable: "+str(exc)[:130])
else:
    st.info("Select a date with an NFL game to see its player statistics.")

st.divider()
st.subheader("Sportsbook player-prop comparison")
st.caption("Independent sportsbook lines for TB @ CHI, Nov 8, 2026. Source snapshot: Edge Labs, Nov 8 at 12:44 PM ET. NOT live Polymarket quotes.")
# Source: https://edgelabs.bet/nfl/props?week=8
# Each record is a published primary threshold with its own over/under odds.
prop_rows=[
 ("Baker Mayfield","Passing yards","DraftKings",212.5,-112,-112),
 ("Baker Mayfield","Passing yards","FanDuel",209.5,-113,-113),
 ("Baker Mayfield","Passing yards","BetMGM",209.5,-115,-115),
 ("Caleb Williams","Passing yards","DraftKings",245.5,-113,-111),
 ("Caleb Williams","Passing yards","FanDuel",243.5,-113,-113),
 ("Caleb Williams","Passing yards","BetMGM",245.5,-115,-115),
 ("Baker Mayfield","Passing TDs","DraftKings",1.5,127,-163),
 ("Baker Mayfield","Passing TDs","FanDuel",1.5,134,-176),
 ("Baker Mayfield","Passing TDs","BetMGM",1.5,130,-175),
 ("Caleb Williams","Passing TDs","DraftKings",1.5,-157,123),
 ("Caleb Williams","Passing TDs","FanDuel",1.5,-152,116),
 ("Caleb Williams","Passing TDs","BetMGM",1.5,-150,110),
 ("Bucky Irving","Rushing yards","DraftKings",64.5,-114,-110),
 ("Bucky Irving","Rushing yards","FanDuel",63.5,-113,-113),
 ("Bucky Irving","Rushing yards","BetMGM",64.5,-115,-115),
 ("D'Andre Swift","Rushing yards","DraftKings",62.5,-114,-110),
 ("D'Andre Swift","Rushing yards","FanDuel",62.5,-113,-113),
 ("D'Andre Swift","Rushing yards","BetMGM",62.5,-115,-115),
 ("Kenny Gainwell","Rushing yards","DraftKings",22.5,-112,-112),
 ("Kenny Gainwell","Rushing yards","FanDuel",21.5,-113,-113),
 ("Kenny Gainwell","Rushing yards","BetMGM",22.5,-105,-125),
 ("Caleb Williams","Rushing yards","DraftKings",19.5,-107,-117),
 ("Caleb Williams","Rushing yards","FanDuel",19.5,-113,-113),
 ("Caleb Williams","Rushing yards","BetMGM",18.5,-120,-111),
 ("Roschon Johnson","Rushing yards","DraftKings",16.5,-114,-110),
 ("Roschon Johnson","Rushing yards","FanDuel",16.5,-113,-113),
 ("Roschon Johnson","Rushing yards","BetMGM",15.5,-120,-110),
 ("Baker Mayfield","Rushing yards","DraftKings",16.5,-111,-113),
 ("Baker Mayfield","Rushing yards","FanDuel",17.5,-113,-113),
 ("Baker Mayfield","Rushing yards","BetMGM",16.5,-110,-120),
]
def american_implied(odds):
    return (100/(odds+100)) if odds>0 else (-odds/(-odds+100))
raw_props=pd.DataFrame(prop_rows,columns=["Player","Stat","Book","Line","Over odds","Under odds"])
raw_props["Over implied"]=raw_props["Over odds"].apply(american_implied)
raw_props["Under implied"]=raw_props["Under odds"].apply(american_implied)
raw_props["No-vig over"]=raw_props["Over implied"]/(raw_props["Over implied"]+raw_props["Under implied"])
summary=[]
for (player,stat),group in raw_props.groupby(["Player","Stat"],sort=True):
    median=float(group["Line"].median())
    same=group[group["Line"]==median]
    summary.append({"Player":player,"Stat":stat,"Consensus line (median)":median,
                    "Lowest line":float(group["Line"].min()),"Highest line":float(group["Line"].max()),
                    "Books":len(group),
                    "No-vig over at median":round(float(same["No-vig over"].mean())*100,1) if len(same) else None})
summary_df=pd.DataFrame(summary)
st.caption("Median of three independently published sportsbook thresholds. The no-vig probability is calculated only from books quoting that exact median threshold; it is NOT a calibrated player projection.")
st.dataframe(summary_df,hide_index=True,width="stretch")
with st.expander("View every sportsbook line and its odds"):
    st.dataframe(raw_props[["Player","Stat","Book","Line","Over odds","Under odds","No-vig over"]],hide_index=True,width="stretch")
    st.download_button("Download sportsbook lines",raw_props.to_csv(index=False),"nfl_sportsbook_props_2026_11_08.csv","text/csv")
st.markdown("[View source and latest available lines](https://edgelabs.bet/nfl/props?week=8)")
st.info("This is a dated source snapshot for one matchup, not a live automated sportsbook API. Different thresholds must not be treated as interchangeable when comparing odds. No Polymarket player-prop contract is matched yet.")

st.divider()
st.subheader("Matchup center")
if not games:
    st.info("No saved games yet. Check the scheduled collector in GitHub Actions.")
else:
    show=st.radio("Game window",["Upcoming / recent","All archived games"],horizontal=True)
    options=upcoming if show=="Upcoming / recent" and upcoming else games
    def label(g):
        dt=parse_time(g.get("event_start_time_utc"))
        when=dt.astimezone(ET).strftime("%a %b %d · %I:%M %p ET") if dt else "Time unknown"
        return f"{g.get('title','Unknown matchup')}   |   {when}"
    idx=st.selectbox("Choose a matchup",range(len(options)),format_func=lambda i:label(options[i]))
    game=options[idx]
    dt=parse_time(game.get("event_start_time_utc"))
    st.markdown("## "+str(game.get("title") or "NFL matchup"))
    st.caption("Exchange event start: "+(dt.astimezone(ET).strftime("%A, %B %d at %I:%M %p ET") if dt else "Not provided")+" · This is not independently verified kickoff.")
    markets=game.get("markets") or []
    by_type={}
    for m in markets:
        typ=m.get("type") or "Unclassified"
        by_type.setdefault(typ,[]).append(m)
    valid=[m for m in markets if m.get("side_verified") is True and m.get("ask") is not None]
    a,b,c=st.columns(3)
    a.metric("Contracts found",len(markets))
    b.metric("Verified outcomes",sum(m.get("side_verified") is True for m in markets))
    c.metric("Comparable asks",len(valid))

    tabs=st.tabs(["Overview","Moneyline","Spreads","Totals","Player props","Data quality"])
    def table(items):
        if not items:
            st.info("No contracts of this type were captured in this snapshot. This does not prove the market is unavailable.")
            return
        records=[]
        for m in items:
            verified=m.get("side_verified") is True
            records.append({
                "Contract":m.get("title") or "Untitled",
                "Outcome":m.get("outcome") if verified else "⚠ Unverified",
                "Line":m.get("line") or "—",
                "Ask":cents(m.get("ask")),
                "Bid":cents(m.get("bid")),
                "Bid/ask width":cents(m.get("spread")),
                "Status":"Verified side" if verified else "Side unknown"
            })
        st.dataframe(pd.DataFrame(records),hide_index=True,width="stretch")
    with tabs[0]:
        st.markdown("#### Available market snapshot")
        table(markets)
        st.caption("A displayed bid/ask is not a recommendation. Prices without verified contract sides cannot be matched to model probabilities.")
    with tabs[1]:
        st.markdown("#### Game winner")
        table(by_type.get("Moneyline",[]))
    with tabs[2]:
        st.markdown("#### Point spreads")
        table(by_type.get("Spread",[]))
    with tabs[3]:
        st.markdown("#### Game totals")
        table(by_type.get("Total",[]))
    with tabs[4]:
        st.markdown("#### Player performance markets")
        props=[m for m in markets if m.get("type") in ("Props & other","Player props")]
        table(props)
        st.caption("Player-stat projections require an independent player data feed. No player projections are fabricated.")
    with tabs[5]:
        st.markdown("#### Why a market may not be comparable")
        issues=[]
        for m in markets:
            issues.append({"Contract":m.get("title") or m.get("slug"),"Side verified":bool(m.get("side_verified")),"Has ask":m.get("ask") is not None,"Has bid":m.get("bid") is not None,"Issue":m.get("quote_warning") or ("Missing outcome mapping" if not m.get("side_verified") else "—")})
        if issues:
            st.dataframe(pd.DataFrame(issues),hide_index=True,width="stretch")
        st.caption("US feed coverage may omit markets and contract metadata. International prices are not substituted for US quotes.")
    if markets:
        st.download_button("Export this matchup",pd.DataFrame(markets).to_csv(index=False),"nfl_matchup_snapshot.csv","text/csv")

st.divider()
st.subheader("Market watchlist")
st.caption("All archived games in one view. This is a data inventory, not a ranked bet list.")
watch=[]
for g in games:
    when=parse_time(g.get("event_start_time_utc"))
    for m in g.get("markets") or []:
        watch.append({"Matchup":g.get("title"),"Event time ET":when.astimezone(ET).strftime("%b %d %I:%M %p") if when else "—","Type":m.get("type") or "Unclassified","Outcome":m.get("outcome") if m.get("side_verified") else "Unverified","Ask":cents(m.get("ask")),"Bid":cents(m.get("bid")),"Usable":bool(m.get("side_verified") and m.get("ask") is not None)})
if watch:
    df=pd.DataFrame(watch)
    st.dataframe(df,hide_index=True,width="stretch")
    st.download_button("Export all archived markets",df.to_csv(index=False),"nfl_market_watchlist.csv","text/csv")
else:
    st.info("No market rows available.")
with st.expander("Data sources and next model integrations"):
    st.write("Current: scheduled Polymarket US quote archive. Planned: independent nflverse team/player statistics, injury and weather context, calibrated player projections, and model-vs-market comparisons. Those projections are not yet connected.")
    st.write("Contract outcome verification, freshness, fees and liquidity must pass checks before an edge estimate can be displayed.")
st.caption("Read-only analytics · No account connection or order placement · Source: Polymarket US public snapshot")
st.link_button("View collector runs","https://github.com/ryanmangels06-rgb/Polymarket-Dashboard-Rm/actions")
