"""UNIVERS ZONE - Telegram mini-app marketplace (website + bot in ONE file)
Run:  python app.py      (the mini app needs an https address, e.g. Render / VPS + domain)
Admin: send a FILE to the bot with caption   title | price | category | description | image link(optional)
       /del 12  removes product 12      /stats  shows totals
"""
import os, json, time, hmac, hashlib, sqlite3, threading, urllib.parse
import requests
from flask import Flask, request, jsonify, Response

# ======================= CONFIG - change these values, nothing else needed =======================
BOT_TOKEN = "9257392670:AAErzQw50AhKIY0JAUmOSlEv9DAyrbkUN7U"                       # from @BotFather
ADMIN_IDS = [8304747658]                                # your numeric Telegram ID (more admins: [111, 222])
CHANNEL = "@stxuniverszone"                              # users MUST join this before any download (bot must be its admin)
WEBAPP_URL = "https://your-app.onrender.com"           # the https link of this site
SUPPORT = "@STXSUPPORT1"
PAY_INFO = "Send money to bKash / Nagad (Personal): 01605218319"
CATS = ["Source Code", "Mod APK", "Tools", "Others"]
PORT = 10000                                           # Render's default port; change it on other hosts
DATA_DIR = "."                                         # folder of the database file (use a persistent disk path)
# ==================================================================================================

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
db = sqlite3.connect(os.path.join(DATA_DIR, "univers.db"), check_same_thread=False)
db.row_factory = sqlite3.Row
lock = threading.RLock()
db.executescript("""
create table if not exists users(id integer primary key, name text, uname text, bal real default 0, ts integer);
create table if not exists products(id integer primary key autoincrement, title text, price real, cat text, descr text, img text, fid text, sold integer default 0, ts integer);
create table if not exists orders(id integer primary key autoincrement, uid integer, pid integer, price real, ts integer);
create table if not exists deposits(id integer primary key autoincrement, uid integer, amt real, method text, trx text, st text default 'pending', ts integer);""")


def q(sql, *a, one=False, w=False):
    with lock:
        cur = db.execute(sql, a)
        if w:
            db.commit()
            return cur.lastrowid
        r = cur.fetchall()
        return (r[0] if r else None) if one else r


def tg(method, **p):
    try:
        return requests.post(f"{API}/{method}", json=p, timeout=35).json()
    except Exception:
        return {"ok": False}


def joined(uid):
    r = tg("getChatMember", chat_id=CHANNEL, user_id=uid)
    return bool(r.get("ok")) and r["result"]["status"] in ("creator", "administrator", "member", "restricted")


def auth():
    """the mini app sends Telegram's signed initData; we verify the signature, so nobody can fake a user id"""
    d = dict(urllib.parse.parse_qsl(request.headers.get("X-Init", ""), keep_blank_values=True))
    h = d.pop("hash", "")
    key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    if not h or not hmac.compare_digest(hmac.new(key, chk.encode(), hashlib.sha256).hexdigest(), h):
        return None
    if time.time() - int(d.get("auth_date", 0)) > 86400:
        return None
    u = json.loads(d["user"])
    if not q("select 1 from users where id=?", u["id"], one=True):
        q("insert into users values(?,?,?,0,?)", u["id"], u.get("first_name", ""), u.get("username", ""), int(time.time()), w=True)
    return u["id"]


app = Flask(__name__)


@app.get("/")
def home():
    return Response(PAGE, mimetype="text/html")


@app.get("/api/data")
def data():
    uid = auth()
    if not uid:
        return jsonify(error="Open this site from the Telegram bot."), 401
    rows = lambda sql, *a: [dict(r) for r in q(sql, *a)]
    return jsonify(
        me=dict(q("select * from users where id=?", uid, one=True)), cats=CATS, channel=CHANNEL, pay=PAY_INFO, support=SUPPORT,
        products=rows("select id,title,price,cat,descr,img,sold from products order by id desc"),
        owned=[r["pid"] for r in q("select pid from orders where uid=?", uid)],
        orders=rows("select o.price,o.ts,p.id pid,p.title from orders o join products p on p.id=o.pid where o.uid=? order by o.id desc", uid),
        deposits=rows("select amt,method,st,ts from deposits where uid=? order by id desc limit 30", uid))


@app.post("/api/buy")
def buy():
    uid = auth()
    if not uid:
        return jsonify(error="auth"), 401
    p = q("select * from products where id=?", (request.json or {}).get("pid"), one=True)
    if not p:
        return jsonify(error="Product not found"), 404
    if not joined(uid):                                     # force join: checked BEFORE any file or payment
        return jsonify(error="join"), 403
    if not q("select 1 from orders where uid=? and pid=?", uid, p["id"], one=True):
        with lock:
            c = db.execute("update users set bal=bal-? where id=? and bal>=?", (p["price"], uid, p["price"]))
            if c.rowcount == 0:
                return jsonify(error="Not enough balance. Please add money first."), 402
            db.execute("insert into orders(uid,pid,price,ts) values(?,?,?,?)", (uid, p["id"], p["price"], int(time.time())))
            db.execute("update products set sold=sold+1 where id=?", (p["id"],))
            db.commit()
    tg("sendDocument", chat_id=uid, document=p["fid"], caption=f"✅ {p['title']}")
    return jsonify(ok=True)


@app.post("/api/deposit")
def deposit():
    uid = auth()
    if not uid:
        return jsonify(error="auth"), 401
    j = request.json or {}
    try:
        amt = float(j.get("amt"))
    except (TypeError, ValueError):
        amt = 0
    trx, method = str(j.get("trx", "")).strip()[:40], str(j.get("method", "bKash"))[:10]
    if amt < 10 or len(trx) < 6:
        return jsonify(error="Enter an amount (minimum 10) and the TrxID."), 400
    if q("select 1 from deposits where trx=?", trx, one=True):
        return jsonify(error="This TrxID was already submitted."), 400
    did = q("insert into deposits(uid,amt,method,trx,ts) values(?,?,?,?,?)", uid, amt, method, trx, int(time.time()), w=True)
    for a in ADMIN_IDS:
        tg("sendMessage", chat_id=a, text=f"💰 Deposit #{did}\nUser: {uid}\nAmount: {amt:g}\n{method} TrxID: {trx}",
           reply_markup={"inline_keyboard": [[{"text": "✅ APPROVE", "callback_data": f"dp:{did}:1"}, {"text": "❌ REJECT", "callback_data": f"dp:{did}:0"}]]})
    return jsonify(ok=True)


# ------------------------------------------------------------------ bot (long polling thread)
def handle(u):
    cb = u.get("callback_query")
    if cb:
        if cb["from"]["id"] not in ADMIN_IDS:
            return
        _, did, ok = cb["data"].split(":")
        d = q("select * from deposits where id=? and st='pending'", int(did), one=True)
        if d:
            q("update deposits set st=? where id=?", "approved" if ok == "1" else "rejected", d["id"], w=True)
            if ok == "1":
                q("update users set bal=bal+? where id=?", d["amt"], d["uid"], w=True)
            tg("sendMessage", chat_id=d["uid"], text=f"✅ Deposit approved: ৳{d['amt']:g}" if ok == "1" else "❌ Your deposit was rejected.")
            tg("editMessageText", chat_id=cb["message"]["chat"]["id"], message_id=cb["message"]["message_id"],
               text=cb["message"]["text"] + ("\n\n✅ APPROVED" if ok == "1" else "\n\n❌ REJECTED"))
        return
    m = u.get("message") or {}
    uid, text = m.get("from", {}).get("id"), (m.get("text") or "")
    if m.get("document") and uid in ADMIN_IDS:
        f = [x.strip() for x in (m.get("caption") or "").split("|")] + [""] * 5
        try:
            price = float(f[1])
        except ValueError:
            return tg("sendMessage", chat_id=uid, text="Caption format:\ntitle | price | category | description | image link")
        cat = f[2] if f[2] in CATS else "Others"
        pid = q("insert into products(title,price,cat,descr,img,fid,ts) values(?,?,?,?,?,?,?)", f[0] or m["document"].get("file_name", "File"),
                price, cat, f[3], f[4], m["document"]["file_id"], int(time.time()), w=True)
        tg("sendMessage", chat_id=uid, text=f"✅ Product #{pid} added to {cat}")
    elif text.startswith("/del ") and uid in ADMIN_IDS:
        q("delete from products where id=?", text[5:].strip(), w=True)
        tg("sendMessage", chat_id=uid, text="🗑 Deleted")
    elif text == "/stats" and uid in ADMIN_IDS:
        n = lambda s: q(s, one=True)[0] or 0
        tg("sendMessage", chat_id=uid, text=f"Users: {n('select count(*) from users')}\nProducts: {n('select count(*) from products')}\n"
           f"Orders: {n('select count(*) from orders')}\nSales: ৳{n('select sum(price) from orders'):g}")
    elif text.startswith("/start"):
        tg("sendMessage", chat_id=uid, text="𝗨𝗡𝗜𝗩𝗘𝗥𝗦 𝗭𝗢𝗡𝗘\nBuy premium files, source codes and tools in one place.",
           reply_markup={"inline_keyboard": [[{"text": "OPEN UNIVERS ZONE", "web_app": {"url": WEBAPP_URL}}]]})


def bot():
    tg("deleteWebhook")
    off = 0
    while True:
        r = tg("getUpdates", offset=off, timeout=25, allowed_updates=["message", "callback_query"])
        for u in r.get("result", []):
            off = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                print("bot error:", e)
        if not r.get("ok"):
            time.sleep(3)


PAGE = r'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>UNIVERS ZONE</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#f3f6f5;--card:#fff;--ink:#0f1b2d;--mute:#64748b;--line:#e2e8f0;--acc:#16a34a;--on:#fff;--navy:#1e1b8f;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0b1210;--card:#131d1a;--ink:#e8f1ee;--mute:#8aa39b;--line:#22332e;--acc:#34d399;--on:#04231a;--navy:#a5b4fc}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding-bottom:84px}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--card);border-bottom:1px solid var(--line)}
.logo{width:36px;height:36px;border-radius:10px;background:var(--acc);color:var(--on);display:grid;place-items:center;font-weight:800;font-size:13px}
.brand{font-weight:800;font-size:17px;letter-spacing:.2px;color:var(--navy)}.brand b{color:var(--acc)}
.sp{flex:1}.chip{background:var(--acc);color:var(--on);border-radius:999px;padding:6px 12px;font-weight:700;border:0}
main{padding:14px;max-width:720px;margin:0 auto}
h2{font-size:18px;margin:4px 0 12px}
.cats{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;margin-bottom:10px}
.cats button{flex:none;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:8px 14px;font:inherit}
.cats .on{background:var(--acc);color:var(--on);border-color:var(--acc);font-weight:700}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.pad{padding:12px}.p{cursor:pointer}
.img{position:relative;aspect-ratio:16/10;background:linear-gradient(135deg,var(--acc),var(--navy));display:grid;place-items:center;color:#fff;font-weight:800;font-size:26px;background-size:cover;background-position:center}
.tag{position:absolute;top:8px;font-size:12px;font-weight:700;padding:3px 9px;border-radius:999px}
.pr{left:8px;background:#f59e0b;color:#1c1300}.sd{right:8px;background:rgba(0,0,0,.65);color:#fff}
.t{padding:10px 12px;font-weight:650;font-size:14px}
.btn{display:block;width:100%;border:0;border-radius:12px;padding:13px;background:var(--acc);color:var(--on);font:inherit;font-weight:700;cursor:pointer}
.btn.g{background:var(--card);color:var(--ink);border:1px solid var(--line)}
.bal{font-size:30px;font-weight:800;margin:4px 0 10px}
label{display:block;color:var(--mute);font-size:13px;margin:10px 0 4px}
input,select{width:100%;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px}
.row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-top:1px solid var(--line)}.row:first-child{border-top:0}
.mute{color:var(--mute);font-size:13px}.ok{color:var(--acc)}.no{color:#dc2626}
nav{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);border-top:1px solid var(--line);padding:6px 6px calc(6px + env(safe-area-inset-bottom))}
nav button{flex:1;border:0;background:none;color:var(--mute);font:inherit;font-size:12px;font-weight:700;padding:10px 0;letter-spacing:.3px}
nav .on{color:var(--acc)}
.sheet{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:flex-end;z-index:9}
.sheet>div{background:var(--card);width:100%;max-width:720px;margin:0 auto;border-radius:18px 18px 0 0;padding:18px;display:grid;gap:10px}
#toast{position:fixed;left:50%;bottom:96px;transform:translateX(-50%);background:#111;color:#fff;padding:10px 16px;border-radius:999px;z-index:20;display:none;max-width:90%}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
</style></head><body>
<header><div class="logo">UZ</div><div class="brand">UNIVERS <b>ZONE</b></div><div class="sp"></div><button class="chip" id="wb">৳0</button></header>
<main id="v"></main><div id="toast"></div>
<nav id="nav"></nav>
<script>
const T=window.Telegram.WebApp;T.ready();T.expand();
const $=s=>document.querySelector(s);
const h=(t,p={},...k)=>{const e=document.createElement(t);for(const[a,v]of Object.entries(p))a==='class'?e.className=v:a.startsWith('on')?e[a]=v:e.setAttribute(a,v);e.append(...k);return e};
const api=(p,b)=>fetch(p,{method:b?'POST':'GET',headers:{'X-Init':T.initData,'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined}).then(r=>r.json());
const money=n=>'৳'+(+n).toLocaleString('en',{maximumFractionDigits:2});
const date=t=>new Date(t*1000).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'});
let D=null,tab='home',cat='All';
function toast(m){const t=$('#toast');t.textContent=m;t.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display='none',2600)}
async function load(){const d=await api('/api/data');if(d.error){$('#v').replaceChildren(h('p',{class:'mute'},d.error));return}D=d;$('#wb').textContent=money(D.me.bal);draw()}
function sheet(...k){const s=h('div',{class:'sheet',onclick:e=>e.target===s&&s.remove()},h('div',{},...k));document.body.append(s);return s}
function card(p){
  const im=h('div',{class:'img'},h('span',{class:'tag pr'},money(p.price)),h('span',{class:'tag sd'},p.sold+' SOLD'));
  if(p.img)im.style.backgroundImage=`url("${p.img.replace(/"/g,'')}")`;else im.append(p.title.slice(0,2).toUpperCase());
  return h('div',{class:'card p',onclick:()=>open(p)},im,h('div',{class:'t'},p.title))}
function open(p){
  const own=D.owned.includes(p.id),s=sheet(h('h2',{},p.title),h('div',{class:'mute'},p.cat+' • '+p.sold+' sold'),h('div',{},p.descr||'No description.'),
    h('button',{class:'btn',onclick:()=>buy(p,s)},own?'DOWNLOAD AGAIN':'BUY FOR '+money(p.price)))}
async function buy(p,s){
  const r=await api('/api/buy',{pid:p.id});
  if(r.ok){s.remove();toast('File sent to your Telegram chat');load();return}
  if(r.error==='join'){s.remove();const j=sheet(h('h2',{},'Join our channel first'),h('div',{class:'mute'},'To download files you must join '+D.channel+'. After joining, come back and tap the button below.'),
    h('button',{class:'btn',onclick:()=>T.openTelegramLink('https://t.me/'+D.channel.replace('@',''))},'JOIN CHANNEL'),
    h('button',{class:'btn g',onclick:()=>{j.remove();open(p)}},'I HAVE JOINED'));return}
  toast(r.error||'Something went wrong');if(r.error&&r.error.startsWith('Not enough')){s.remove();tab='wallet';draw()}}
function draw(){
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===tab));
  const v=$('#v');v.replaceChildren();
  if(tab==='home'){
    const c=h('div',{class:'cats'},...['All',...D.cats].map(x=>h('button',{class:x===cat?'on':'',onclick:()=>{cat=x;draw()}},x)));
    const L=D.products.filter(p=>cat==='All'||p.cat===cat);
    v.append(h('h2',{},'Premium files'),c,L.length?h('div',{class:'grid'},...L.map(card)):h('p',{class:'mute'},'No files here yet.'))}
  if(tab==='wallet'){
    const am=h('input',{type:'number',inputmode:'numeric',placeholder:'Amount (min 10)'}),tx=h('input',{placeholder:'Transaction ID (TrxID)'}),me=h('select',{},...['bKash','Nagad','Rocket'].map(x=>h('option',{},x)));
    v.append(h('div',{class:'card pad'},h('div',{class:'mute'},'AVAILABLE BALANCE'),h('div',{class:'bal'},money(D.me.bal)),h('div',{class:'mute'},D.pay),
      h('label',{},'Method'),me,h('label',{},'Amount'),am,h('label',{},'TrxID'),tx,h('div',{style:'height:12px'}),
      h('button',{class:'btn',onclick:async()=>{const r=await api('/api/deposit',{amt:am.value,trx:tx.value,method:me.value});toast(r.ok?'Sent. Admin will approve soon.':r.error);if(r.ok)load()}},'ADD MONEY')),
      h('h2',{style:'margin-top:18px'},'Deposit history'),h('div',{class:'card pad'},...(D.deposits.length?D.deposits.map(d=>h('div',{class:'row'},h('div',{},money(d.amt)+' • '+d.method,h('div',{class:'mute'},date(d.ts))),h('b',{class:d.st==='approved'?'ok':d.st==='rejected'?'no':'mute'},d.st.toUpperCase()))):[h('span',{class:'mute'},'No deposits yet.')])))}
  if(tab==='orders'){
    v.append(h('h2',{},'My orders'),h('div',{class:'card pad'},...(D.orders.length?D.orders.map(o=>h('div',{class:'row'},h('div',{},o.title,h('div',{class:'mute'},money(o.price)+' • '+date(o.ts))),
      h('button',{class:'chip',onclick:()=>{const p=D.products.find(x=>x.id===o.pid);p?open(p):toast('File removed')}},'GET'))):[h('span',{class:'mute'},'You have not bought anything yet.')])))}
  if(tab==='me'){
    const m=D.me;v.append(h('div',{class:'card pad'},h('h2',{},m.name||'User'),h('div',{class:'row'},h('span',{class:'mute'},'Telegram ID'),h('b',{},String(m.id))),
      h('div',{class:'row'},h('span',{class:'mute'},'Username'),h('b',{},m.uname?'@'+m.uname:'-')),h('div',{class:'row'},h('span',{class:'mute'},'Balance'),h('b',{},money(m.bal)))),
      h('div',{style:'height:12px'}),h('button',{class:'btn g',onclick:()=>T.openTelegramLink('https://t.me/'+D.channel.replace('@',''))},'OUR CHANNEL'),
      h('div',{style:'height:8px'}),h('button',{class:'btn',onclick:()=>T.openTelegramLink('https://t.me/'+D.support.replace('@',''))},'SUPPORT'))}
}
$('#nav').append(...[['home','HOME'],['wallet','WALLET'],['orders','ORDERS'],['me','MY PROFILE']].map(([k,n])=>h('button',{'data-t':k,onclick:()=>{tab=k;draw()}},n)));
$('#wb').onclick=()=>{tab='wallet';draw()};
load();
</script></body></html>'''

if __name__ == "__main__":
    threading.Thread(target=bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
