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
    tag_ids=[x.strip() for x in str(nfl.get("tags","")).split(",") if x.strip().isdigit()]
    if not tag_ids:
        raise ValueError("NFL tag IDs unavailable from Polymarket")
    found={}
    for tag_id in tag_ids[:8]:
        for offset in range(0,500,100):
            r=requests.get("https://gamma-api.polymarket.com/events",params={"tag_id":tag_id,"active":"true","closed":"false","limit":100,"offset":offset},timeout=20)
            r.raise_for_status()
            batch=r.json()
            for e in batch:
                labels=" ".join(str(t.get("slug",""))+" "+str(t.get("label","")) for t in e.get("tags",[]) if isinstance(t,dict))
                title=(e.get("title") or "")+" "+(e.get("slug") or "")
                if re.search(r"\\b(nfl|super.bowl)\\b",labels+" "+title,re.I):
                    found[str(e.get("id") or e.get("slug"))]=e
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

search=st.text_input("Filter NFL games by team or matchup",value="")
max_markets=st.slider("Maximum NFL markets",5,100,40)
try:
    events=nfl_events()
except (requests.RequestException,ValueError) as e:
    st.error(f"Could not load NFL markets: {e}")
    st.stop()

matches=[]
for event in events:
    for market in event.get("markets",[]):
        if market.get("closed"): continue
        text_to_search=(event.get("title") or "")+" "+(market.get("question") or "")
        if search.strip() and search.lower() not in text_to_search.lower(): continue
        matches.append((event,market))
st.write(f"{len(matches)} NFL markets across {len(events)} NFL events.")
if not matches:
    st.info("No active NFL markets returned by the sports tags right now. Try clearing the team filter; no unrelated markets will be substituted.")
rows=[]
for event,market in matches[:max_markets]:
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
        rows.append({"Event":event.get("title"),"Question":market.get("question"),"Outcome":outcome,"Best bid":bid,"Best ask":ask,"Indicative price":prices[i] if i<len(prices) else None})
if rows:
    df=pd.DataFrame(rows)
    st.dataframe(df,use_container_width=True,hide_index=True)
    st.download_button("Export snapshot CSV",df.to_csv(index=False),"market_snapshot.csv","text/csv")

st.divider()
st.subheader("Paper-trading probability comparison")
st.caption("Supply your own independently estimated probability; this app does not yet have a trained NFL forecasting model.")
price=st.number_input("Market purchase price",0.01,0.99,0.55,0.01)
prob=st.number_input("Independent estimated probability",0.01,0.99,0.55,0.01)
buffer=st.number_input("Cost and uncertainty buffer",0.0,0.25,0.03,0.005)
st.metric("Raw probability difference",f"{prob-price:+.1%}")
st.metric("Difference after assumed buffer",f"{prob-price-buffer:+.1%}")
st.warning("Positive differences are hypothetical, not evidence of a profitable strategy. Validate forecasts and costs before drawing conclusions.")
