import json
import re
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Polymarket NFL Lab", page_icon="🏈", layout="wide")
st.title("🏈 Polymarket NFL Lab")
st.caption("Read-only research dashboard • Prices are snapshots, not guaranteed executable quotes")

@st.cache_data(ttl=60)
def events_page(offset):
    r=requests.get("https://gamma-api.polymarket.com/events",params={"active":"true","closed":"false","limit":100,"offset":offset},timeout=20)
    r.raise_for_status()
    return r.json()

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

search=st.text_input("Find an NFL market or team",value="NFL")
pages=st.slider("Pages of active events to scan",1,15,5)
max_markets=st.slider("Maximum markets",5,50,20)
events=[]
try:
    for page in range(pages):
        batch=events_page(page*100)
        events.extend(batch)
        if len(batch)<100: break
except requests.RequestException as e:
    st.error(f"Polymarket API unavailable: {e}")
    st.stop()

matches=[]
for event in events:
    event_text = (event.get("title") or "")+" "+(event.get("slug") or "")+" "+(event.get("description") or "")
    tags = " ".join(str(t.get("label", "")) for t in event.get("tags", []) if isinstance(t, dict))
    haystack = event_text+" "+tags
    if search.strip().upper() == "NFL":
        if not re.search(r"\b(nfl|national football league|super bowl|football)\b", haystack, re.I): continue
    elif search.lower() not in haystack.lower(): continue
    for market in event.get("markets",[]):
        if not market.get("closed"): matches.append((event,market))

st.write(f"{len(matches)} matching markets found in {len(events)} scanned events.")
if not matches: st.info("Try a team name, 'football', or increase pages scanned.")
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
