"""CCDL operator terminal. No market feed or portfolio balances are implied."""
import hashlib
import html
import json
import re
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError
from dashboard_api import submit_command

REGION = "ap-southeast-2"  # Deployment region; branding is not infrastructure.
TABLE = "Exchange_OrderBook"
IST = ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="CCDL | Trading terminal", page_icon="◈", layout="wide")
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
:root {--ink:#18253b;--muted:#778398;--line:#e6ebf2;--blue:#3869ed;--green:#138568;--red:#d75661;}
.stApp {background:#f6f8fc;color:var(--ink);font-family:'DM Sans',sans-serif;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stMainBlockContainer"] {padding-top:2.4rem;max-width:1500px;padding-bottom:3rem;}
h1,h2,h3 {font-family:'Manrope',sans-serif!important;letter-spacing:-.035em; color:var(--ink)!important;}
h1 {font-size:2.15rem!important;} h3 {font-size:1.15rem!important;}
[data-testid="stSidebar"] {background:#fff;border-right:1px solid var(--line);}
[data-testid="stSidebar"] > div:first-child {padding-top:2rem;}
[data-testid="stSidebar"] [role="radiogroup"] label {padding:10px 14px;border-radius:8px;margin:3px 0;}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#edf2ff;color:#315ed5;}
[data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"] > div {border-radius:12px;}
[data-testid="stForm"] {background:white;border:1px solid var(--line);padding:22px;}
[data-testid="stMetric"] {background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;}
[data-testid="stMetricLabel"] {color:var(--muted);font-size:12px;}
[data-testid="stMetricValue"] {font-family:'Manrope',sans-serif;font-size:1.65rem;color:var(--ink);}
.stButton button, .stFormSubmitButton button {border-radius:7px;font-weight:600;}
button[kind="primary"],button[kind="primaryFormSubmit"] {background:var(--blue);border-color:var(--blue);color:#fff;}
[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input {color:var(--ink);}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {color:#66758c!important;}
.brand {display:flex;align-items:center;gap:10px;margin-bottom:8px;font:800 24px 'Manrope',sans-serif;letter-spacing:-1px;}
.logo {display:inline-grid;place-items:center;width:36px;height:36px;border-radius:10px;background:#3869ed;color:white;font-size:25px;}
.eyebrow {font-size:10px;font-weight:700;letter-spacing:2px;color:#8190a6;text-transform:uppercase;margin:26px 0 10px;}
.topbar {display:flex;justify-content:space-between;align-items:center;padding-bottom:16px;border-bottom:1px solid var(--line);margin-bottom:24px;gap:12px;}
.topbar .location {font-size:13px;color:#66758c;}.tag {display:inline-block;border:1px solid #dae3f3;background:#edf2ff;color:#4167b8;border-radius:5px;padding:5px 9px;font-size:10px;font-weight:700;letter-spacing:.7px;}
.hero {background:linear-gradient(110deg,#182b52,#254477);border-radius:14px;padding:28px 30px;color:#fff;margin:14px 0 24px;position:relative;overflow:hidden;}
.hero:after {content:'◈';position:absolute;right:38px;top:-24px;font-size:180px;color:#ffffff0b;}
.hero h2 {color:white!important;font-size:24px!important;margin:0 0 8px!important;}.hero p {color:#c1cee5;margin:0;max-width:660px;font-size:14px;}
.hero .overline {color:#95b6ff;font-size:10px;letter-spacing:2px;margin-bottom:12px;font-weight:700;}
.empty {background:#fff;border:1px dashed #d8e0ed;border-radius:12px;padding:42px 24px;text-align:center;margin:10px 0 18px;color:#7b8799;}
.empty b {display:block;font:700 17px 'Manrope',sans-serif;color:#43526a;margin:9px;}.empty .symbol {font-size:28px;color:#9fb1d1;}
.badge {display:inline-block;border-radius:5px;padding:5px 10px;background:#edf2ff;color:#3b64bc;font-size:11px;font-weight:700;letter-spacing:.4px;}
.badge.buy,.badge.filled {background:#e6f5ee;color:#138568;}.badge.sell,.badge.cancelled {background:#fcecee;color:#bf4b59;}
.card {background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px;margin:8px 0 18px;}
.detail-grid {display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:20px;}.detail-label {font-size:11px;color:#8190a6;margin-bottom:5px;}.detail-value {font-size:14px;color:#25334a;overflow-wrap:anywhere;}
.book-table {width:100%;border-collapse:collapse;font-size:13px;}.book-table th {color:#8a96a7;text-align:right;font-size:10px;letter-spacing:.7px;padding:12px 10px;border-bottom:1px solid var(--line);}.book-table td {padding:12px 10px;text-align:right;border-bottom:1px solid #f0f3f7;}.book-table th:first-child,.book-table td:first-child {text-align:left;}.bid {color:var(--green);}.ask {color:var(--red);}
.note {border-left:3px solid #cdd9f2;padding:10px 14px;color:#738097;font-size:12px;line-height:1.65;margin-top:16px;}
.footer {border-top:1px solid var(--line);padding-top:18px;margin-top:30px;color:#8b96a6;font-size:11px;}
@media(max-width:700px) {.detail-grid {grid-template-columns:1fr 1fr;}.hero {padding:22px;}.topbar {flex-wrap:wrap;}}
</style>''', unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def valid_identifier(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value))


def stamp():
    return datetime.now(IST).strftime("%d %b %Y · %H:%M:%S IST")


def empty(title, description):
    st.markdown(f'<div class="empty"><div class="symbol">◇</div><b>{esc(title)}</b>{esc(description)}</div>', unsafe_allow_html=True)


def badge(value):
    style = str(value).lower()
    if style not in ("buy", "sell", "filled", "cancelled"):
        style = "other"
    return f'<span class="badge {style}">{esc(str(value).replace("_", " "))}</span>'


def details(values):
    cells = ''.join(f'<div><div class="detail-label">{esc(k)}</div><div class="detail-value">{esc(v)}</div></div>' for k,v in values.items())
    st.markdown(f'<div class="detail-grid">{cells}</div>', unsafe_allow_html=True)


def read_item(key, table=TABLE, field="OrderID"):
    return boto3.resource("dynamodb", region_name=REGION).Table(table).get_item(Key={field:key}, ConsistentRead=True).get("Item")


def fetch(slot, key, table=TABLE, field="OrderID"):
    # Clear previous results on failure, so old data never looks like a new read.
    st.session_state.pop(slot, None)
    try:
        with st.spinner("Reading saved state…"):
            item = read_item(key, table, field)
        st.session_state[slot] = {"item":item, "time":stamp(), "key":key}
    except (BotoCoreError, ClientError) as error:
        code = error.response.get("Error", {}).get("Code", "AWS error") if isinstance(error, ClientError) else type(error).__name__
        st.error(f"Could not refresh: {code}. Check your WSL AWS profile and permissions.")


def snapshot(slot):
    result = st.session_state.get(slot)
    if result:
        st.caption(f"Snapshot · {result['time']} · Refresh to see subsequent changes")
    return result


def render_order(item):
    st.markdown(f"### {esc(item['OrderID'])}")
    st.markdown(badge(item.get("Side","")) + " &nbsp; " + badge(item.get("Status","Unknown")), unsafe_allow_html=True)
    original = int(item["QuantityLots"])
    remaining = item.get("RemainingLots")
    cancelled = int(item.get("CancelledLots",0))
    filled = int(item.get("FilledLots", original-int(remaining)-cancelled)) if remaining is not None else None
    cols = st.columns(4)
    for col,label,value in zip(cols,["Original lots","Filled lots","Remaining lots","Cancelled lots"],[original,filled,remaining,cancelled]):
        col.metric(label, "—" if value is None else f"{int(value):,}")
    details({"Symbol":item["Symbol"],"Limit price (ticks)":f"{int(item['PriceTicks']):,}","Acceptance sequence":item.get("Sequence","—"),"Received (UTC)":item.get("ReceivedAt","—"),"Updated (UTC)":item.get("UpdatedAt","—")})
    with st.expander("Technical details"):
        st.json(json.loads(json.dumps(item,default=str)))


def render_depth(orders, side):
    levels = {}
    for order in orders:
        if order["Side"] == side and int(order["RemainingLots"]) > 0:
            p = int(order["PriceTicks"])
            quantity,count = levels.get(p,(0,0))
            levels[p] = (quantity+int(order["RemainingLots"]),count+1)
    title = "Buy orders · Bids" if side == "BUY" else "Sell orders · Asks"
    st.markdown(f"### {title}")
    if not levels:
        empty("No bids" if side == "BUY" else "No asks", "No resting orders on this side.")
        return
    cls = "bid" if side == "BUY" else "ask"
    rows = ''.join(f'<tr><td class="{cls}">{p:,}</td><td>{levels[p][0]:,}</td><td>{levels[p][1]}</td></tr>' for p in sorted(levels,reverse=side=="BUY"))
    st.markdown('<div class="card"><table class="book-table"><tr><th>PRICE · TICKS</th><th>LOTS</th><th>ORDERS</th></tr>'+rows+'</table></div>',unsafe_allow_html=True)


for key,value in {"recent":[],"attempts":{},"book_symbol":"MATCH-DEMO"}.items():
    if key not in st.session_state:
        st.session_state[key] = value

with st.sidebar:
    st.markdown('<div class="brand"><span class="logo">◈</span> CCDL<span style="color:#3869ed">.</span></div>',unsafe_allow_html=True)
    st.caption("EXCHANGE TERMINAL")
    st.markdown('<div class="eyebrow">Workspace</div>',unsafe_allow_html=True)
    page = st.radio("Workspace",["Overview","Order book","Orders","Trades","Order entry"],label_visibility="collapsed",key="navigation")
    st.markdown('<div class="eyebrow">Project location</div><b>Manipal, India</b>',unsafe_allow_html=True)
    st.caption("Academic exchange · Operator workspace")
    st.divider()
    st.caption("DEVELOPMENT MODE")
    st.caption("25 active orders per symbol. Prices use ticks; quantities use lots.")
    with st.expander("Connection & scope"):
        st.caption(f"AWS deployment: {REGION}. Manipal is the project location, not the hosting region.")
        st.caption("No live market feed, portfolio balance, or settlement. Symbol-based matching can model stocks or commodities; contract rules are not implemented.")

st.markdown(f'<div class="topbar"><span class="location">WORKSPACE &nbsp;/&nbsp; <b>{esc(page)}</b></span><span class="location">Manipal, India &nbsp;&nbsp; <span class="tag">DEVELOPMENT</span></span></div>',unsafe_allow_html=True)

if page in ("Overview","Order book"):
    st.title("Your trading workspace" if page == "Overview" else "Order book")
    st.caption("A clear view of your exchange. Saved orders and executions, without simulated market prices.")
    if page == "Overview":
        st.markdown('<div class="hero"><div class="overline">CCDL EXCHANGE / MANIPAL, INDIA</div><h2>From order to execution.</h2><p>Inspect market depth, track your orders, and send commands from one focused workspace.</p></div>',unsafe_allow_html=True)
    with st.form("book_search"):
        c1,c2 = st.columns([4,1])
        symbol = c1.text_input("Symbol",value=st.session_state["book_symbol"],placeholder="e.g. MATCH-DEMO")
        refresh = c2.form_submit_button("Refresh book",type="primary",width="stretch")
    if refresh:
        if valid_identifier(symbol):
            st.session_state["book_symbol"] = symbol
            fetch("book_snapshot",f"BOOK#{symbol}")
        else:
            st.error("Use 1–128 letters, numbers, dots, underscores or hyphens.")
    saved = snapshot("book_snapshot")
    orders = None
    if saved and saved["item"]:
        try:
            orders = json.loads(saved["item"]["OrdersJSON"])
            if not isinstance(orders,list):
                raise ValueError("Invalid book")
        except (ValueError,KeyError,TypeError):
            st.error("Stored book format is invalid. No depth can be displayed.")
    if orders is not None:
        st.subheader(saved["item"].get("Symbol",saved["key"][5:]))
        buys=[o for o in orders if o["Side"]=="BUY"]
        sells=[o for o in orders if o["Side"]=="SELL"]
        bid=max((int(o["PriceTicks"]) for o in buys),default=None)
        ask=min((int(o["PriceTicks"]) for o in sells),default=None)
        for c,label,val in zip(st.columns(4),["Active orders","Best bid · ticks","Best ask · ticks","Spread · ticks"],[len(orders),bid,ask,ask-bid if bid is not None and ask is not None else None]):
            c.metric(label,"—" if val is None else f"{val:,}")
        l,r=st.columns(2)
        with l: render_depth(orders,"BUY")
        with r: render_depth(orders,"SELL")
        if orders:
            st.subheader("Resting orders")
            rows=[{"Order ID":o["OrderID"],"Side":o["Side"],"Price (ticks)":int(o["PriceTicks"]),"Remaining lots":int(o["RemainingLots"]),"Sequence":int(o["Sequence"])} for o in sorted(orders,key=lambda o:int(o["Sequence"]))]
            st.dataframe(rows,hide_index=True,width="stretch")
        st.caption(f"Book version {int(saved['item']['Version'])} · Price priority, then acceptance sequence")
    elif saved and saved["item"] is None:
        empty("No book for this symbol", "An accepted matching order creates the symbol’s book.")
    elif not saved:
        for col,label in zip(st.columns(4),["Active orders","Best bid · ticks","Best ask · ticks","Spread · ticks"]): col.metric(label,"—")
        empty("Choose a symbol to load its book", "Nothing is fetched until you click Refresh book. No live quotes are implied.")
    if page == "Overview":
        st.subheader("This session’s submissions")
        if st.session_state.recent:
            st.dataframe(st.session_state.recent,hide_index=True,width="stretch")
            st.caption("Submission receipts only, not execution status. Session history clears when the browser session ends.")
        else:
            st.caption("No submissions in this browser session. Open Order entry to submit a limit order.")

elif page == "Orders":
    st.title("Orders")
    st.caption("Look up an exact order ID. Reads are fresh when you click View order.")
    with st.form("order_search"):
        order_id=st.text_input("Order ID",value="match-buy-001")
        lookup=st.form_submit_button("View order",type="primary")
    if lookup:
        if valid_identifier(order_id): fetch("order_snapshot",order_id)
        else: st.error("Enter a valid order ID.")
    saved=snapshot("order_snapshot")
    if saved and saved["item"]: render_order(saved["item"])
    elif saved: empty("Order not found", "Check the ID. A queued command may still be processing.")
    else: empty("Every order has a story", "Look up an order to see its fills, remaining quantity and status.")

elif page == "Trades":
    st.title("Trade executions")
    st.caption("Find an execution by the incoming order that triggered the match. Fill numbers start at zero.")
    with st.form("trade_search"):
        a,b,c=st.columns([2,3,1])
        symbol=a.text_input("Symbol",value="MATCH-DEMO")
        incoming=b.text_input("Incoming order ID",value="match-buy-001")
        fill=c.number_input("Fill number",min_value=0,max_value=24,value=0,step=1)
        lookup=st.form_submit_button("View trade",type="primary")
    if lookup:
        if all(valid_identifier(v) for v in (symbol,incoming)):
            trade_id=hashlib.sha256(f"{symbol}|{incoming}|{int(fill)}".encode()).hexdigest()
            fetch("trade_snapshot",trade_id,"Exchange_TradeHistory","TradeID")
        else: st.error("Enter valid symbol and order identifiers.")
    saved=snapshot("trade_snapshot")
    if saved and saved["item"]:
        item=saved["item"]
        st.markdown(badge("FILLED"),unsafe_allow_html=True)
        a,b,c=st.columns(3)
        a.metric("Symbol",item["Symbol"]);b.metric("Executed lots",f"{int(item['QuantityLots']):,}");c.metric("Execution price · ticks",f"{int(item['PriceTicks']):,}")
        details({"Buy order":item["BuyOrderID"],"Sell order":item["SellOrderID"],"Executed (UTC)":item["ExecutedAt"],"Incoming order":item["IncomingOrderID"],"Fill number":int(item["FillIndex"])})
        with st.expander("Trade ID & technical details"): st.json(json.loads(json.dumps(item,default=str)))
    elif saved: empty("No execution found", "Check the symbol, incoming order ID and fill number.")
    else: empty("Executions, not estimates", "Only saved trade records appear here. No synthetic trade history.")

elif page == "Order entry":
    st.title("Order entry")
    st.caption("Send a limit order or cancel its unfilled quantity. Commands are submitted to your development exchange.")
    action=st.radio("Action",["New order","Cancel order"],horizontal=True,key="entry_action")
    kind="order" if action=="New order" else "cancel"
    idkey=f"{kind}_ticket_id"
    if idkey not in st.session_state: st.session_state[idkey]=f"web-{kind}-{uuid.uuid4().hex[:12]}"
    if st.button("Generate a new ID",key="new_id"):
        st.session_state[idkey]=f"web-{kind}-{uuid.uuid4().hex[:12]}"
    left,right=st.columns([3,2],gap="large")
    with right:
        st.markdown('<div class="card"><div class="eyebrow" style="margin-top:0">Before you submit</div><h3>One command. One identity.</h3><p>Reuse the same ID and values only for a retry. Generate a new ID for a new request.</p><div class="note">QUEUED confirms acceptance by the queue. Check Orders for the final result.<br><br>Prices are ticks, not rupees. Lot sizes and tick values are not configured per instrument.</div></div>',unsafe_allow_html=True)
    with left:
        with st.form("command_ticket"):
            command_id=st.text_input("Order ID" if kind=="order" else "Cancellation command ID",value=st.session_state[idkey])
            symbol=st.text_input("Symbol",value="DASH-DEMO")
            if kind=="order":
                side=st.radio("Side",["BUY","SELL"],horizontal=True)
                a,b=st.columns(2)
                price=a.number_input("Limit price · ticks",min_value=1,max_value=10**12,value=12500,step=1)
                quantity=b.number_input("Quantity · lots",min_value=1,max_value=10**12,value=10,step=1)
                payload={"OrderID":command_id,"Symbol":symbol,"Side":side,"PriceTicks":int(price),"QuantityLots":int(quantity)}
                path="/orders"
            else:
                target=st.text_input("Order ID to cancel",placeholder="Exact ID of the existing order")
                payload={"Type":"CANCEL","CommandID":command_id,"OrderID":target,"Symbol":symbol}
                path="/cancellations"
            confirmed=st.checkbox("I confirm this command should be sent to the development exchange.")
            submitted=st.form_submit_button("Queue limit order" if kind=="order" else "Queue cancellation",type="primary",width="stretch")
        if submitted:
            identities=[command_id,symbol]+([payload["OrderID"]] if kind=="cancel" else [])
            attempt_key=f"{kind}:{command_id}"
            canonical=json.dumps(payload,sort_keys=True)
            if not confirmed: st.error("Confirm the command before submitting.")
            elif not all(valid_identifier(v) for v in identities): st.error("IDs and symbols must be 1–128 letters, digits, dots, underscores or hyphens.")
            elif attempt_key in st.session_state.attempts and st.session_state.attempts[attempt_key]!=canonical:
                st.error("This ID was already attempted with different values in this session. Use a new ID for a new command.")
            else:
                st.session_state.attempts[attempt_key]=canonical
                st.session_state[idkey]=command_id
                try:
                    with st.spinner("Submitting signed command…"):
                        status,result=submit_command(path,payload)
                    if status==202:
                        st.success("Queued successfully. Check Orders for the processing result.")
                        receipt={"Time (IST)":stamp(),"Action":kind.upper(),"Order ID":payload["OrderID"],"Command ID":command_id,"Symbol":symbol,"Submission":"QUEUED"}
                        st.session_state.recent=([receipt]+st.session_state.recent)[:20]
                        st.session_state["last_receipt"]=receipt
                    else:
                        st.error(f"API returned HTTP {status}. Execution is not confirmed.")
                        with st.expander("Response details"): st.json(result)
                except (RuntimeError,BotoCoreError,ClientError) as error:
                    st.error(f"Submission not confirmed ({type(error).__name__}). Check order status before retrying the identical command.")
        if "last_receipt" in st.session_state:
            st.caption("Most recent accepted submission in this session")
            details(st.session_state.last_receipt)

st.markdown('<div class="footer">CCDL EXCHANGE &nbsp; / &nbsp; Manipal, India &nbsp; · &nbsp; Academic development terminal &nbsp; · &nbsp; No balances, settlement or live market feed</div>',unsafe_allow_html=True)
