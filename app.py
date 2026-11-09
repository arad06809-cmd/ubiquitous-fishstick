#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEXO - پیام‌رسان فارسی (تک‌فایل) | Flask + SQLite (+ Flask-SocketIO اختیاری) + PWA
اجرا:  pip install flask flask-socketio simple-websocket   سپس   python nexo.py
(اگر flask-socketio نصب نباشد، برنامه با همگام‌سازی سریع HTTP کار می‌کند.)
متغیرهای محیطی: NEXO_OPENROUTER_KEY  NEXO_ADMIN (نام‌کاربری مدیر اصلی)  PORT
"""
import os, re, io, json, time, hmac, uuid, base64, struct, sqlite3, secrets, threading, traceback, urllib.request, functools
from flask import Flask, request, jsonify, session, g, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
try:
    from flask_socketio import SocketIO, join_room
    SIO = True
except Exception:
    SIO = False

BASE = os.path.dirname(os.path.abspath(__file__))
# On hosts with a persistent disk, set NEXO_DATA_DIR to its mount path.
DATA_DIR = os.environ.get('NEXO_DATA_DIR') or BASE
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, 'nexo.db'); UP = os.path.join(DATA_DIR, 'uploads'); BK = os.path.join(DATA_DIR, 'backups')
os.makedirs(UP, exist_ok=True); os.makedirs(BK, exist_ok=True)
KF = os.path.join(DATA_DIR, '.nexo_secret')
if not os.path.exists(KF):
    open(KF, 'w').write(secrets.token_hex(32))
app = Flask(__name__)
app.secret_key = open(KF).read().strip()
app.config.update(MAX_CONTENT_LENGTH=64 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('NEXO_SECURE_COOKIES', 'false').lower() in ('1', 'true', 'yes'),
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30, JSON_AS_ASCII=False)
now = lambda: int(time.time())

SCHEMA = '''
create table if not exists users(id integer primary key autoincrement, username text unique collate nocase, phone text unique, nick text, pw text, bio text default '', birth text, avatar text, premium_until int default 0, stars int default 0, role text default 'user', banned int default 0, created int, last_seen int default 0, theme text, prefs text default '{}', privacy text default '{}', totp text, recov text, daily int default 0, invited_by int, ai_prefs text default '{}');
create table if not exists chats(id integer primary key autoincrement, type text, title text, owner int, username text unique collate nocase, public int default 0, avatar text, about text default '', created int);
create table if not exists members(chat_id int, user_id int, role text default 'member', pinned int default 0, muted int default 0, archived int default 0, folder int, last_read int default 0, delivered int default 0, cleared int default 0, hidden int default 0, primary key(chat_id,user_id));
create table if not exists messages(id integer primary key autoincrement, chat_id int, sender int, text text, kind text default 'text', file text, reply_to int, fwd text, edited int default 0, deleted int default 0, pinned int default 0, created int, expire_at int, upd int);
create index if not exists mi on messages(chat_id,id);
create table if not exists reactions(msg_id int, user_id int, emoji text, primary key(msg_id,user_id));
create table if not exists contacts(owner int, contact int, primary key(owner,contact));
create table if not exists blocks(owner int, blocked int, primary key(owner,blocked));
create table if not exists sessions(id text primary key, user int, device text, ip text, created int, last int, revoked int default 0);
create table if not exists files(name text primary key, orig text, size int, owner int, kind text, created int);
create table if not exists folders(id integer primary key autoincrement, user int, name text);
create table if not exists stars_tx(id integer primary key autoincrement, user int, delta int, reason text, admin int, ref text, created int);
create table if not exists gifts(id integer primary key autoincrement, name text, emoji text, price int, premium_only int default 0, rare int default 0, active int default 1);
create table if not exists user_gifts(id integer primary key autoincrement, to_user int, from_user int, gift int, note text, created int);
create table if not exists ads(id integer primary key autoincrement, user int, title text, body text, link text, status text default 'pending', views int default 0, clicks int default 0, created int);
create table if not exists reports(id integer primary key autoincrement, reporter int, kind text, target int, reason text, status text default 'open', created int);
create table if not exists audit(id integer primary key autoincrement, actor int, kind text, action text, detail text, created int);
create table if not exists errors(id integer primary key autoincrement, path text, err text, created int);
create table if not exists ai_msgs(id integer primary key autoincrement, user int, role text, content text, created int);
create table if not exists ai_mem(id integer primary key autoincrement, user int, text text);
create table if not exists ai_usage(id integer primary key autoincrement, user int, model text, tokens int, created int);
create table if not exists posts(id integer primary key autoincrement, user int, text text, file text, archived int default 0, created int);
create table if not exists settings(k text primary key, v text);
create table if not exists ctr(n int);
'''
RULES = """۱. به حریم خصوصی دیگران احترام بگذارید.
۲. ارسال محتوای غیرقانونی، خشونت‌آمیز، آزاردهنده یا کلاهبرداری ممنوع است.
۳. هرزنگاری و ارسال انبوه پیام ممنوع است.
۴. مسئولیت امنیت رمز عبور و کد بازیابی با شماست.
۵. Stars فقط اعتبار داخلی NEXO است و ارزش پولی واقعی ندارد.
۶. مدیران می‌توانند حساب‌های متخلف را مسدود کنند."""
GIFTS = [('قلب', '❤️', 5, 0, 0), ('گل', '🌹', 10, 0, 0), ('کیک', '🎂', 25, 0, 0), ('ستاره', '⭐', 50, 0, 0),
         ('الماس', '💎', 500, 0, 1), ('تاج', '👑', 300, 1, 0), ('موشک', '🚀', 150, 1, 0),
         ('خرس', '🧸', 15, 0, 0), ('جعبه هدیه', '🎁', 25, 0, 0), ('جام', '🏆', 100, 0, 0), ('گلدان', '🌷', 20, 0, 0),
         ('قلب روبانی', '💝', 40, 0, 0), ('ماه', '🌙', 250, 1, 1), ('اژدها', '🐉', 400, 1, 1)]
STAR_PACKS = [50, 100, 250, 500, 1000, 2500]
STICKERS = ['😀', '😂', '😍', '🥳', '😎', '😭', '😡', '👍', '🙏', '❤️', '🔥', '🎉']
PSTICKERS = ['🦄', '🐉', '🪐', '🌈', '👑', '💎']
REACT = ['👍', '❤️', '😂', '😮', '😢', '🙏']
PREACT = ['🔥', '💎', '🚀', '👑']
PLANS = {'m1': [30, 150], 'm3': [90, 400], 'y1': [365, 1400]}
LV = {'user': 0, 'mod': 1, 'admin': 2, 'owner': 3}
UN = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,23}$')
IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp'}; VID = {'mp4', 'webm', 'mov'}; AUD = {'mp3', 'ogg', 'wav', 'm4a', 'webm', 'mp4', 'opus'}
DOC = {'pdf', 'txt', 'zip', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'json', 'rar', '7z', 'md'}


# ---------------- database ----------------
def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB, timeout=30); g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close(e):
    d = g.pop('db', None)
    if d: d.close()


def q(sql, a=(), one=False):
    r = db().execute(sql, a).fetchall()
    return (r[0] if r else None) if one else r


def x(sql, a=()):
    c = db().execute(sql, a); db().commit(); return c.lastrowid


def seq():
    db().execute('update ctr set n=n+1'); return db().execute('select n from ctr').fetchone()[0]


def S(k, d=None):
    r = q('select v from settings where k=?', (k,), True)
    return json.loads(r['v']) if r else d


def setS(k, v): x('insert or replace into settings values(?,?)', (k, json.dumps(v, ensure_ascii=False)))


def init_db():
    c = sqlite3.connect(DB); c.executescript(SCHEMA)
    if not c.execute('select count(*) from ctr').fetchone()[0]: c.execute('insert into ctr values(0)')
    have = {r[0] for r in c.execute('select name from gifts')}
    c.executemany('insert into gifts(name,emoji,price,premium_only,rare) values(?,?,?,?,?)', [g for g in GIFTS if g[0] not in have])
    c.commit(); c.close()


init_db()


# ---------------- helpers ----------------
class E(Exception):
    def __init__(s, m, c=400): s.m = m; s.c = c


def j(o=None, code=200):
    r = jsonify(o if o is not None else {'ok': True}); r.status_code = code; return r


@app.errorhandler(E)
def _e(e): return j({'error': e.m}, e.c)


@app.errorhandler(Exception)
def _ex(e):
    if isinstance(e, HTTPException):
        if e.code == 413: return j({'error': 'حجم فایل بیش از حد مجاز است'}, 413)
        return e
    try: x('insert into errors(path,err,created) values(?,?,?)', (request.path, traceback.format_exc()[-1800:], now()))
    except Exception: pass
    return j({'error': 'خطای داخلی سرور'}, 500)


RL = {}
def rl(key, n, per):
    t = time.time(); l = [a for a in RL.get(key, []) if t - a < per]
    ok = len(l) < n
    if ok: l.append(t)
    RL[key] = l; return ok


def audit(kind, action, detail='', actor=None):
    u = actor if actor is not None else (g.u['id'] if getattr(g, 'u', None) else None)
    x('insert into audit(actor,kind,action,detail,created) values(?,?,?,?,?)', (u, kind, action, str(detail)[:500], now()))


def jl(s, d=None):
    try: return json.loads(s) if s else (d if d is not None else {})
    except Exception: return d if d is not None else {}


def prem(u): return (u['premium_until'] or 0) > now()


def me():
    if hasattr(g, 'u'): return g.u
    g.u = None; sid = session.get('sid')
    if sid:
        s = q('select * from sessions where id=? and revoked=0', (sid,), True)
        u = q('select * from users where id=?', (s['user'],), True) if s else None
        if u and not u['banned']:
            g.u = u
            if now() - (u['last_seen'] or 0) > 20:
                db().execute('update users set last_seen=? where id=?', (now(), u['id']))
                db().execute('update sessions set last=? where id=?', (now(), sid)); db().commit()
    return g.u


def auth():
    u = me()
    if not u: raise E('ابتدا وارد شوید', 401)
    return u


def adm(level):
    u = auth()
    if LV.get(u['role'], 0) < LV[level]: raise E('دسترسی ندارید', 403)
    return u


@app.before_request
def _guard():
    p = request.path
    if p.startswith('/api/'):
        if not rl('ip' + (request.remote_addr or ''), 400, 60): return j({'error': 'تعداد درخواست‌ها زیاد است'}, 429)
        if request.method != 'GET':
            tok = session.get('csrf', '')
            if not tok or not hmac.compare_digest(request.headers.get('X-CSRF', ''), tok): return j({'error': 'نشست نامعتبر؛ صفحه را تازه‌سازی کنید'}, 403)


@app.after_request
def _hdr(r):
    r.headers['X-Content-Type-Options'] = 'nosniff'; r.headers['X-Frame-Options'] = 'DENY'; r.headers['Referrer-Policy'] = 'same-origin'
    if r.mimetype == 'text/html':
        r.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' ws: wss:; frame-ancestors 'none'"
        r.headers['Cache-Control'] = 'no-store'
    return r


def phone(p):
    if not p: return None
    p = str(p).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    p = re.sub(r'[\s\-()]', '', p)
    if p.startswith('09') and len(p) == 11: p = '+98' + p[1:]
    elif p.startswith('9') and len(p) == 10: p = '+98' + p
    elif p.startswith('0098'): p = '+' + p[2:]
    elif p.startswith('98') and len(p) == 12: p = '+' + p
    if not re.match(r'^\+\d{10,15}$', p): raise E('شماره تلفن نامعتبر است')
    return p


def online(u): return (u['last_seen'] or 0) > now() - 60


def uj(u, viewer=None, full=False):
    pv = jl(u['privacy']); self_ = viewer is not None and viewer['id'] == u['id']
    hide = pv.get('lastseen') == 'nobody' and not self_
    d = {'id': u['id'], 'username': u['username'], 'nick': u['nick'], 'avatar': u['avatar'], 'premium': prem(u), 'bio': u['bio'],
         'online': False if hide else online(u), 'last_seen': None if hide else u['last_seen'], 'role': u['role']}
    if full or self_:
        d['phone'] = u['phone'] if (self_ or pv.get('phone') != 'hide') else None
        d['birth'] = u['birth']
    if self_:
        d.update(stars=u['stars'], premium_until=u['premium_until'], theme=jl(u['theme'], None), prefs=jl(u['prefs']), privacy=pv,
                 has2fa=bool(u['totp']), email=None)
    return d


def login_user(uid):
    sid = uuid.uuid4().hex; ua = (request.headers.get('User-Agent') or '')[:160]
    x('insert into sessions(id,user,device,ip,created,last) values(?,?,?,?,?,?)', (sid, uid, ua, request.remote_addr, now(), now()))
    session['sid'] = sid; session.permanent = True
    if 'csrf' not in session: session['csrf'] = secrets.token_hex(16)


def totp(secret, t=None):
    k = base64.b32decode(secret); c = struct.pack('>Q', int((t or time.time()) // 30))
    h = hmac.new(k, c, 'sha1').digest(); o = h[-1] & 15
    return '%06d' % ((struct.unpack('>I', h[o:o + 4])[0] & 0x7fffffff) % 1000000)


def totp_ok(secret, code):
    return any(hmac.compare_digest(totp(secret, time.time() + d * 30), str(code).strip()) for d in (-1, 0, 1))


def stars_move(uid, delta, reason, admin=None, ref=None):
    c = db().execute('update users set stars=stars+? where id=? and stars+?>=0', (delta, uid, delta))
    if c.rowcount != 1: db().rollback(); raise E('موجودی Stars کافی نیست')
    db().execute('insert into stars_tx(user,delta,reason,admin,ref,created) values(?,?,?,?,?,?)', (uid, delta, reason, admin, ref, now()))


def add_premium(uid, days):
    u = q('select premium_until from users where id=?', (uid,), True)
    db().execute('update users set premium_until=? where id=?', (max(now(), u['premium_until'] or 0) + days * 86400, uid))


# ---------------- sockets ----------------
if SIO:
    sio = SocketIO(app, async_mode='threading')

    @sio.on('connect')
    def _sc():
        sid = session.get('sid'); c = sqlite3.connect(DB)
        r = c.execute('select s.user from sessions s join users u on u.id=s.user where s.id=? and s.revoked=0 and u.banned=0', (sid,)).fetchone(); c.close()
        if not r: return False
        join_room('u%d' % r[0])

    def ping(uids):
        for i in set(uids): sio.emit('sync', {}, to='u%d' % i)
else:
    def ping(uids): pass


# ---------------- pages / pwa ----------------
@app.get('/')
def index():
    session.setdefault('csrf', secrets.token_hex(16)); session.permanent = True
    return Response(CLIENT.replace('__CSRF__', session['csrf']), mimetype='text/html')


@app.get('/admin')
def admin_page():
    session.setdefault('csrf', secrets.token_hex(16))
    return Response(ADMIN.replace('__CSRF__', session['csrf']), mimetype='text/html')


@app.get('/manifest.json')
def manifest():
    return j({'name': 'NEXO', 'short_name': 'NEXO', 'start_url': '/', 'display': 'standalone', 'dir': 'rtl', 'lang': 'fa',
              'background_color': '#17212b', 'theme_color': '#17212b',
              'icons': [{'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
                        {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'}]})


@app.get('/sw.js')
def sw():
    js = """const C='nexo-files';self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(clients.claim()));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method!=='GET'||u.origin!==location.origin||!u.pathname.startsWith('/f/'))return;
e.respondWith(caches.open(C).then(async c=>{const m=await c.match(e.request);if(m)return m;const r=await fetch(e.request);if(r.status===200)c.put(e.request,r.clone());return r}))});"""
    return Response(js, mimetype='application/javascript')


_png = {}
def make_png(n):
    import zlib
    if n in _png: return _png[n]
    rows = bytearray()
    for y in range(n):
        rows.append(0)
        for xx in range(n):
            t = (xx + y) / (2 * n); r, gg, b = int(79 + 43 * t), int(125 - 39 * t), int(255 - 17 * t)
            if .28 * n <= y <= .72 * n and (.28 * n <= xx <= .37 * n or .63 * n <= xx <= .72 * n or abs(xx - (.3 * n + (y - .28 * n) * .909)) < .045 * n):
                r, gg, b = 255, 255, 255
            rows += bytes((r, gg, b))
    def ch(t, d): c = struct.pack('>I', len(d)) + t + d; return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    _png[n] = b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', n, n, 8, 2, 0, 0, 0)) + ch(b'IDAT', zlib.compress(bytes(rows), 6)) + ch(b'IEND', b'')
    return _png[n]


@app.get('/icon-<int:n>.png')
def icon(n): return Response(make_png(192 if n < 300 else 512), mimetype='image/png', headers={'Cache-Control': 'public,max-age=86400'})


@app.get('/api/rules')
def rules(): return j({'rules': S('rules', RULES)})


# ---------------- auth ----------------
@app.post('/api/register')
def register():
    d = request.get_json(silent=True) or {}
    if not S('reg_open', True): raise E('ثبت‌نام موقتاً بسته است')
    if not rl('reg' + (request.remote_addr or ''), 10, 3600): raise E('تعداد ثبت‌نام بیش از حد مجاز است', 429)
    un = (d.get('username') or '').strip(); nick = (d.get('nick') or '').strip()[:40]; pw = d.get('password') or ''
    if not UN.match(un): raise E('نام کاربری باید ۴ تا ۲۴ کاراکتر انگلیسی، عدد یا _ باشد و با حرف شروع شود')
    if not nick: raise E('نام مستعار الزامی است')
    if len(pw) < 8: raise E('رمز عبور باید حداقل ۸ کاراکتر باشد')
    if pw != d.get('password2', pw): raise E('تکرار رمز عبور مطابقت ندارد')
    ph = phone(d.get('phone')); birth = (d.get('birth') or '').strip() or None
    if birth and not re.match(r'^\d{4}-\d{2}-\d{2}$', birth): raise E('تاریخ تولد نامعتبر است')
    if q('select 1 from users where username=?', (un,)): raise E('این نام کاربری قبلاً گرفته شده')
    if ph and q('select 1 from users where phone=?', (ph,)): raise E('این شماره قبلاً ثبت شده')
    first = q('select count(*) n from users', one=True)['n'] == 0
    role = 'owner' if first or un.lower() == os.environ.get('NEXO_ADMIN', '').lower() else 'user'
    recov = secrets.token_hex(5).upper(); recov = recov[:5] + '-' + recov[5:]
    uid = x('insert into users(username,phone,nick,pw,birth,role,created,recov) values(?,?,?,?,?,?,?,?)',
            (un, ph, nick, generate_password_hash(pw), birth, role, now(), generate_password_hash(recov)))
    ref = q('select id from users where username=? and id!=?', ((d.get('ref') or '').strip(), uid), True)
    if ref:
        db().execute('update users set invited_by=? where id=?', (ref['id'], uid))
        stars_move(uid, 50, 'پاداش دعوت'); stars_move(ref['id'], 50, 'پاداش دعوت دوست: ' + un); db().commit()
    login_user(uid); audit('sec', 'register', un, uid)
    return j({'user': uj(q('select * from users where id=?', (uid,), True), me()), 'recovery': recov})


@app.post('/api/login')
def login():
    d = request.get_json(silent=True) or {}; ident = (d.get('id') or '').strip(); pw = d.get('password') or ''
    if not rl('lg' + (request.remote_addr or '') + ident.lower(), 8, 600): raise E('تلاش بیش از حد؛ چند دقیقه بعد دوباره امتحان کنید', 429)
    u = q('select * from users where username=?', (ident.lstrip('@'),), True)
    if not u:
        try: u = q('select * from users where phone=?', (phone(ident),), True)
        except E: u = None
    if not u or not check_password_hash(u['pw'], pw):
        audit('sec', 'login_fail', ident, 0); raise E('نام کاربری یا رمز عبور اشتباه است')
    if u['banned']: raise E('حساب شما مسدود شده است', 403)
    if u['totp'] and not totp_ok(u['totp'], d.get('code', '')):
        if not d.get('code'): return j({'need2fa': True})
        audit('sec', '2fa_fail', u['username'], u['id']); raise E('کد دومرحله‌ای اشتباه است')
    login_user(u['id']); audit('sec', 'login', u['username'], u['id'])
    return j({'user': uj(u, u)})


@app.post('/api/logout')
def logout():
    if session.get('sid'): x('update sessions set revoked=1 where id=?', (session['sid'],))
    session.pop('sid', None); return j()


@app.post('/api/forgot')
def forgot():
    d = request.get_json(silent=True) or {}
    if not rl('fg' + (request.remote_addr or ''), 5, 900): raise E('تلاش بیش از حد', 429)
    u = q('select * from users where username=?', ((d.get('username') or '').strip().lstrip('@'),), True)
    if not u or not check_password_hash(u['recov'] or 'x', (d.get('code') or '').strip().upper()): raise E('نام کاربری یا کد بازیابی اشتباه است')
    if len(d.get('password') or '') < 8: raise E('رمز جدید حداقل ۸ کاراکتر باشد')
    x('update users set pw=? where id=?', (generate_password_hash(d['password']), u['id']))
    x('update sessions set revoked=1 where user=?', (u['id'],)); audit('sec', 'password_reset', u['username'], u['id'])
    return j()


@app.get('/api/me')
def get_me():
    u = me(); return j({'user': uj(u, u) if u else None})


@app.post('/api/me')
def upd_me():
    u = auth(); d = request.get_json(silent=True) or {}; f = {}
    if 'nick' in d:
        if not d['nick'].strip(): raise E('نام مستعار الزامی است')
        f['nick'] = d['nick'].strip()[:40]
    if 'bio' in d: f['bio'] = (d['bio'] or '')[:200]
    if 'birth' in d:
        b = d['birth'] or None
        if b and not re.match(r'^\d{4}-\d{2}-\d{2}$', b): raise E('تاریخ تولد نامعتبر است')
        f['birth'] = b
    if 'username' in d and d['username'] != u['username']:
        if not UN.match(d['username']): raise E('نام کاربری نامعتبر است')
        if q('select 1 from users where username=? and id!=?', (d['username'], u['id'])): raise E('این نام کاربری گرفته شده')
        f['username'] = d['username']
    if 'phone' in d:
        p = phone(d['phone']) if d['phone'] else None
        if p and q('select 1 from users where phone=? and id!=?', (p, u['id'])): raise E('این شماره قبلاً ثبت شده')
        f['phone'] = p
    if 'privacy' in d:
        pv = {}
        for k, vals in (('lastseen', ('all', 'nobody')), ('phone', ('show', 'hide')), ('pm', ('all', 'contacts')), ('receipts', ('on', 'off'))):
            v = (d['privacy'] or {}).get(k)
            if v in vals: pv[k] = v
        if pv.get('receipts') == 'off' and not prem(u): raise E('مخفی‌کردن رسید خواندن مخصوص Premium است', 402)
        f['privacy'] = json.dumps({**jl(u['privacy']), **pv})
    if 'prefs' in d:
        allow = {'notif': bool, 'sound': bool, 'vol': int, 'enter': bool, 'scale': float, 'font': str, 'lang': str, 'autoimg': bool, 'autovid': bool, 'bubble': str, 'mode': str, 'accent': str}
        pf = jl(u['prefs'])
        for k, v in (d['prefs'] or {}).items():
            if k in allow and isinstance(v, (allow[k], int)): pf[k] = v if k != 'scale' else max(.85, min(1.3, float(v)))
        f['prefs'] = json.dumps(pf)
    if f:
        x('update users set ' + ','.join(k + '=?' for k in f) + ' where id=?', (*f.values(), u['id']))
    return j({'user': uj(q('select * from users where id=?', (u['id'],), True), u)})


@app.post('/api/password')
def chpw():
    u = auth(); d = request.get_json(silent=True) or {}
    if not check_password_hash(u['pw'], d.get('old') or ''): raise E('رمز فعلی اشتباه است')
    if len(d.get('new') or '') < 8: raise E('رمز جدید حداقل ۸ کاراکتر باشد')
    x('update users set pw=? where id=?', (generate_password_hash(d['new']), u['id']))
    x('update sessions set revoked=1 where user=? and id!=?', (u['id'], session['sid'])); audit('sec', 'password_change', '', u['id'])
    return j()


@app.post('/api/recovery')
def new_recovery():
    u = auth(); d = request.get_json(silent=True) or {}
    if not check_password_hash(u['pw'], d.get('password') or ''): raise E('رمز عبور اشتباه است')
    c = secrets.token_hex(5).upper(); c = c[:5] + '-' + c[5:]
    x('update users set recov=? where id=?', (generate_password_hash(c), u['id'])); return j({'recovery': c})


@app.get('/api/sessions')
def sessions():
    u = auth()
    return j([{'id': s['id'][:12], 'device': s['device'], 'ip': s['ip'], 'created': s['created'], 'last': s['last'], 'current': s['id'] == session.get('sid')}
              for s in q('select * from sessions where user=? and revoked=0 order by last desc', (u['id'],))])


@app.delete('/api/sessions/<sid>')
def kill_session(sid):
    u = auth()
    if sid == 'others': x('update sessions set revoked=1 where user=? and id!=?', (u['id'], session['sid']))
    else: x('update sessions set revoked=1 where user=? and substr(id,1,12)=? and id!=?', (u['id'], sid, session['sid']))
    return j()


@app.get('/api/2fa/setup')
def tfa_setup():
    u = auth(); sec = base64.b32encode(secrets.token_bytes(10)).decode()
    session['tfa'] = sec; return j({'secret': sec, 'uri': 'otpauth://totp/NEXO:%s?secret=%s&issuer=NEXO' % (u['username'], sec)})


@app.post('/api/2fa/enable')
def tfa_enable():
    u = auth(); d = request.get_json(silent=True) or {}
    if d.get('off'):
        if not check_password_hash(u['pw'], d.get('password') or ''): raise E('رمز عبور اشتباه است')
        x('update users set totp=null where id=?', (u['id'],)); audit('sec', '2fa_off', '', u['id']); return j()
    sec = session.get('tfa')
    if not sec or not totp_ok(sec, d.get('code', '')): raise E('کد اشتباه است')
    x('update users set totp=? where id=?', (sec, u['id'])); session.pop('tfa', None); audit('sec', '2fa_on', '', u['id']); return j()


# ---------------- files ----------------
def sniff(ext, head):
    sig = {'png': b'\x89PNG', 'jpg': b'\xff\xd8', 'jpeg': b'\xff\xd8', 'gif': b'GIF8'}
    if ext in sig: return head.startswith(sig[ext])
    if ext == 'webp': return head[:4] == b'RIFF' and head[8:12] == b'WEBP'
    return True


@app.post('/api/upload')
def upload():
    u = auth(); f = request.files.get('file'); hint = request.form.get('kind', '')
    if not f or not f.filename: raise E('فایلی انتخاب نشده')
    if not rl('up%d' % u['id'], 40, 600): raise E('آپلود بیش از حد', 429)
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if hint == 'voice': kind = 'voice' if ext in AUD else None
    elif ext in IMG: kind = 'image'
    elif ext in VID: kind = 'video'
    elif ext in DOC or ext in {'mp3', 'ogg', 'wav', 'm4a'}: kind = 'file'
    else: kind = None
    if not kind: raise E('نوع فایل مجاز نیست')
    name = secrets.token_hex(16) + '.' + ext; path = os.path.join(UP, name); f.save(path); size = os.path.getsize(path)
    lim = (60 if prem(u) else 15) * 1024 * 1024
    with open(path, 'rb') as fh: head = fh.read(16)
    if size > lim or not sniff(ext, head):
        os.remove(path); raise E('حجم فایل بیش از سقف شما (%dMB) است' % (lim >> 20) if size > lim else 'محتوای فایل با پسوند مطابقت ندارد')
    x('insert into files values(?,?,?,?,?,?)', (name, os.path.basename(f.filename)[:120], size, u['id'], kind, now()))
    return j({'file': name, 'name': f.filename, 'size': size, 'kind': kind})


@app.post('/api/avatar')
def avatar():
    u = auth(); r = upload().get_json()
    if r['kind'] != 'image': raise E('فقط تصویر')
    x('update users set avatar=? where id=?', (r['file'], u['id'])); return j({'avatar': r['file']})


@app.get('/f/<name>')
def get_file(name):
    auth()
    r = q('select * from files where name=?', (name,), True)
    if not r: raise E('یافت نشد', 404)
    resp = send_from_directory(UP, name, conditional=True, as_attachment=r['kind'] == 'file', download_name=r['orig'])
    resp.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"; resp.headers['Cache-Control'] = 'private,max-age=86400'
    return resp


# ---------------- users / contacts ----------------
@app.get('/api/users/<int:uid>')
def user_profile(uid):
    v = auth(); u = q('select * from users where id=?', (uid,), True)
    if not u or u['banned']: raise E('کاربر یافت نشد', 404)
    d = uj(u, v, True)
    d['gifts'] = [{'id': r['id'], 'gid': r['gid'], 'emoji': r['emoji'], 'name': r['name'], 'price': r['price'], 'rare': r['rare'], 'from': r['fn'], 'from_id': r['fid'],
                   'from_avatar': r['fa'], 'note': r['note'], 'created': r['created']} for r in q(
        'select ug.id,g.id gid,g.emoji,g.name,g.price,g.rare,u.id fid,u.nick fn,u.avatar fa,ug.note,ug.created from user_gifts ug join gifts g on g.id=ug.gift join users u on u.id=ug.from_user where ug.to_user=? order by ug.id desc limit 60', (uid,))]
    d['posts'] = [dict(r) for r in q('select id,text,file,archived,created from posts where user=? and (archived=0 or user=?) order by id desc limit 50', (uid, v['id']))]
    d['contact'] = bool(q('select 1 from contacts where owner=? and contact=?', (v['id'], uid), True))
    d['blocked'] = bool(q('select 1 from blocks where owner=? and blocked=?', (v['id'], uid), True))
    return j(d)


@app.get('/api/search')
def search():
    u = auth(); s = (request.args.get('q') or '').strip().lstrip('@')
    if len(s) < 2: return j({'users': [], 'chats': []})
    like = '%' + s.replace('%', '').replace('_', '') + '%'
    us = q('select * from users where banned=0 and id!=? and (username like ? or nick like ? or phone=?) limit 20', (u['id'], like, like, s))
    cs = q("select id,title,username,type,avatar from chats where public=1 and type!='private' and (title like ? or username like ?) limit 15", (like, like))
    return j({'users': [uj(r, u) for r in us], 'chats': [dict(r) for r in cs]})


@app.get('/api/contacts')
def contacts():
    u = auth()
    return j([uj(r, u) for r in q('select u.* from contacts c join users u on u.id=c.contact where c.owner=? and u.banned=0 order by u.nick', (u['id'],))])


@app.post('/api/contacts')
def add_contact():
    u = auth(); d = request.get_json(silent=True) or {}; t = None
    if d.get('id'): t = q('select * from users where id=?', (int(d['id']),), True)
    else:
        k = (d.get('q') or '').strip().lstrip('@'); t = q('select * from users where username=?', (k,), True)
        if not t:
            try: t = q('select * from users where phone=?', (phone(k),), True)
            except E: pass
    if not t or t['banned']: raise E('کاربری پیدا نشد')
    if t['id'] == u['id']: raise E('نمی‌توانید خودتان را اضافه کنید')
    x('insert or ignore into contacts values(?,?)', (u['id'], t['id'])); return j(uj(t, u))


@app.delete('/api/contacts/<int:cid>')
def del_contact(cid): x('delete from contacts where owner=? and contact=?', (auth()['id'], cid)); return j()


@app.get('/api/blocks')
def blocks():
    u = auth(); return j([uj(r, u) for r in q('select u.* from blocks b join users u on u.id=b.blocked where b.owner=?', (u['id'],))])


@app.post('/api/block/<int:bid>')
def block(bid): x('insert or ignore into blocks values(?,?)', (auth()['id'], bid)); return j()


@app.delete('/api/block/<int:bid>')
def unblock(bid): x('delete from blocks where owner=? and blocked=?', (auth()['id'], bid)); return j()


@app.post('/api/posts')
def add_post():
    u = auth(); d = request.get_json(silent=True) or {}
    if not (d.get('text') or d.get('file')): raise E('پست خالی است')
    if d.get('file') and not q('select 1 from files where name=? and owner=?', (d['file'], u['id']), True): raise E('فایل نامعتبر')
    x('insert into posts(user,text,file,created) values(?,?,?,?)', (u['id'], (d.get('text') or '')[:1000], d.get('file'), now())); return j()


@app.post('/api/posts/<int:pid>')
def post_op(pid):
    u = auth(); d = request.get_json(silent=True) or {}
    if not q('select 1 from posts where id=? and user=?', (pid, u['id']), True): raise E('یافت نشد', 404)
    if d.get('op') == 'delete': x('delete from posts where id=?', (pid,))
    else: x('update posts set archived=1-archived where id=?', (pid,))
    return j()


# ---------------- chats ----------------
def need_member(cid, uid):
    m = q('select * from members where chat_id=? and user_id=?', (cid, uid), True)
    if not m: raise E('دسترسی ندارید', 403)
    return m


def chat_info(c, m, uid):
    last = q('select m.*,u.nick sn from messages m join users u on u.id=m.sender where m.chat_id=? and m.id>? and m.deleted=0 and m.created<=? and (m.expire_at is null or m.expire_at>?) order by m.id desc limit 1',
             (c['id'], m['cleared'], now(), now()), True)
    unread = q('select count(*) n from messages where chat_id=? and id>? and id>? and sender!=? and deleted=0 and created<=?', (c['id'], m['last_read'], m['cleared'], uid, now()), True)['n']
    d = {'id': c['id'], 'type': c['type'], 'title': c['title'], 'avatar': c['avatar'], 'username': c['username'], 'about': c['about'], 'pinned': m['pinned'], 'muted': m['muted'],
         'archived': m['archived'], 'folder': m['folder'], 'unread': unread, 'role': m['role'], 'owner': c['owner'], 'public': c['public'],
         'ts': last['created'] if last else c['created']}
    if last:
        pre = {'text': last['text'], 'image': '🖼 تصویر', 'video': '🎬 ویدیو', 'file': '📎 فایل', 'voice': '🎤 پیام صوتی', 'sticker': last['text']}.get(last['kind'], last['text'])
        if last['kind'] == 'gift': pre = '🎁 هدیه'
        d['last'] = {'text': (pre or '')[:80], 'sender': last['sn'], 'mine': last['sender'] == uid, 'id': last['id']}
    if c['type'] == 'private':
        p = q('select u.* from members m join users u on u.id=m.user_id where m.chat_id=? and m.user_id!=?', (c['id'], uid), True)
        if p:
            pu = uj(p, me()); d.update(title=p['nick'], avatar=p['avatar'], peer=pu)
    return d


@app.get('/api/chats')
def chats():
    u = auth(); uid = u['id']
    db().execute('update members set delivered=(select coalesce(max(id),0) from messages where chat_id=members.chat_id) where user_id=?', (uid,)); db().commit()
    rows = q('select c.*, m.pinned,m.muted,m.archived,m.folder,m.last_read,m.role mrole,m.cleared from members m join chats c on c.id=m.chat_id where m.user_id=? and m.hidden=0', (uid,))
    out = []
    for c in rows:
        m = {'pinned': c['pinned'], 'muted': c['muted'], 'archived': c['archived'], 'folder': c['folder'], 'last_read': c['last_read'], 'role': c['mrole'], 'cleared': c['cleared']}
        out.append(chat_info(c, m, uid))
    ads = [dict(r) for r in q("select id,title,body,link from ads where status='approved' order by id desc limit 3")]
    fl = [dict(r) for r in q('select id,name from folders where user=?', (uid,))]
    return j({'chats': out, 'ads': ads, 'folders': fl, 'ai': S('ai_enabled', True), 'me': {'stars': u['stars'], 'premium_until': u['premium_until']}})


def find_private(a, b):
    r = q("select c.id from chats c join members x on x.chat_id=c.id and x.user_id=? join members y on y.chat_id=c.id and y.user_id=? where c.type='private'", (a, b), True)
    return r['id'] if r else None


@app.post('/api/chats/private')
def new_private():
    u = auth(); t = int((request.get_json(silent=True) or {}).get('user_id', 0)); tu = q('select * from users where id=? and banned=0', (t,), True)
    if not tu or t == u['id']: raise E('کاربر نامعتبر')
    cid = find_private(u['id'], t)
    if not cid:
        cid = x("insert into chats(type,created) values('private',?)", (now(),))
        db().executemany('insert into members(chat_id,user_id) values(?,?)', [(cid, u['id']), (cid, t)]); db().commit()
    else: x('update members set hidden=0 where chat_id=? and user_id=?', (cid, u['id']))
    return j({'id': cid})


@app.post('/api/chats/group')
def new_group():
    u = auth(); d = request.get_json(silent=True) or {}; typ = 'channel' if d.get('type') == 'channel' else 'group'
    title = (d.get('title') or '').strip()[:60]
    if not title: raise E('عنوان الزامی است')
    un = (d.get('username') or '').strip() or None; pub = 1 if d.get('public') and un else 0
    if un and (not UN.match(un) or q('select 1 from chats where username=?', (un,))): raise E('نام کاربری عمومی نامعتبر یا تکراری است')
    cid = x('insert into chats(type,title,owner,username,public,about,created) values(?,?,?,?,?,?,?)', (typ, title, u['id'], un if pub else None, pub, (d.get('about') or '')[:200], now()))
    db().execute("insert into members(chat_id,user_id,role) values(?,?,'owner')", (cid, u['id']))
    for i in set(int(i) for i in d.get('members', [])):
        if i != u['id'] and q('select 1 from users where id=?', (i,), True): db().execute("insert or ignore into members(chat_id,user_id) values(?,?)", (cid, i))
    db().commit(); ping(chat_ids(cid)); return j({'id': cid})


def chat_ids(cid): return [r['user_id'] for r in q('select user_id from members where chat_id=?', (cid,))]


@app.post('/api/join')
def join():
    u = auth(); d = request.get_json(silent=True) or {}
    c = q('select * from chats where id=? and public=1', (int(d.get('id', 0)),), True) or q('select * from chats where username=? and public=1', ((d.get('username') or '').lstrip('@'),), True)
    if not c: raise E('گفتگوی عمومی پیدا نشد', 404)
    x('insert or ignore into members(chat_id,user_id) values(?,?)', (c['id'], u['id'])); return j({'id': c['id']})


@app.route('/api/chats/<int:cid>', methods=['PUT'])
def edit_chat(cid):
    u = auth(); m = need_member(cid, u['id']); d = request.get_json(silent=True) or {}
    if m['role'] not in ('owner', 'admin'): raise E('فقط مدیران', 403)
    if d.get('title', '').strip(): x('update chats set title=? where id=?', (d['title'].strip()[:60], cid))
    if 'about' in d: x('update chats set about=? where id=?', ((d['about'] or '')[:200], cid))
    ping(chat_ids(cid)); return j()


@app.get('/api/chats/<int:cid>/members')
def members(cid):
    u = auth(); need_member(cid, u['id'])
    return j([dict(uj(r, u), mrole=r['role']) for r in q('select u.*,m.role from members m join users u on u.id=m.user_id where m.chat_id=? order by m.role desc, u.nick', (cid,))])


@app.post('/api/chats/<int:cid>/members')
def add_members(cid):
    u = auth(); m = need_member(cid, u['id']); d = request.get_json(silent=True) or {}
    if m['role'] not in ('owner', 'admin'): raise E('فقط مدیران', 403)
    for i in d.get('user_ids', []): x('insert or ignore into members(chat_id,user_id) values(?,?)', (cid, int(i)))
    if d.get('promote'): x("update members set role='admin' where chat_id=? and user_id=?", (cid, int(d['promote'])))
    ping(chat_ids(cid)); return j()


@app.delete('/api/chats/<int:cid>/members/<int:uid>')
def rm_member(cid, uid):
    u = auth(); m = need_member(cid, u['id'])
    if uid != u['id'] and m['role'] not in ('owner', 'admin'): raise E('فقط مدیران', 403)
    leave(cid, uid); return j()


def leave(cid, uid):
    c = q('select * from chats where id=?', (cid,), True)
    if c['type'] == 'private':
        last = q('select coalesce(max(id),0) n from messages where chat_id=?', (cid,), True)['n']
        x('update members set hidden=1, cleared=?, pinned=0 where chat_id=? and user_id=?', (last, cid, uid)); return
    x('delete from members where chat_id=? and user_id=?', (cid, uid))
    rest = q('select user_id from members where chat_id=? order by user_id', (cid,))
    if not rest:
        x('delete from messages where chat_id=?', (cid,)); x('delete from chats where id=?', (cid,))
    elif c['owner'] == uid:
        x('update chats set owner=? where id=?', (rest[0]['user_id'], cid)); x("update members set role='owner' where chat_id=? and user_id=?", (cid, rest[0]['user_id']))


@app.post('/api/chats/bulk')
def bulk():
    u = auth(); d = request.get_json(silent=True) or {}; ids = [int(i) for i in d.get('ids', [])]; a = d.get('action'); uid = u['id']
    for cid in ids:
        m = need_member(cid, uid)
        if a == 'mute': x('update members set muted=1 where chat_id=? and user_id=?', (cid, uid))
        elif a == 'unmute': x('update members set muted=0 where chat_id=? and user_id=?', (cid, uid))
        elif a == 'archive': x('update members set archived=1 where chat_id=? and user_id=?', (cid, uid))
        elif a == 'unarchive': x('update members set archived=0 where chat_id=? and user_id=?', (cid, uid))
        elif a == 'pin':
            lim = 15 if prem(u) else 5
            if not m['pinned'] and q('select count(*) n from members where user_id=? and pinned=1', (uid,), True)['n'] >= lim: raise E('سقف پین %d گفتگو است؛ با Premium بیشتر می‌شود' % lim, 402)
            x('update members set pinned=1 where chat_id=? and user_id=?', (cid, uid))
        elif a == 'unpin': x('update members set pinned=0 where chat_id=? and user_id=?', (cid, uid))
        elif a == 'read': x('update members set last_read=(select coalesce(max(id),0) from messages where chat_id=?) where chat_id=? and user_id=?', (cid, cid, uid))
        elif a == 'delete': leave(cid, uid)
        elif a == 'folder':
            f = d.get('folder')
            if f and not q('select 1 from folders where id=? and user=?', (int(f), uid), True): raise E('پوشه نامعتبر')
            x('update members set folder=? where chat_id=? and user_id=?', (f or None, cid, uid))
    return j()


@app.get('/api/folders')
def folders(): return j([dict(r) for r in q('select id,name from folders where user=?', (auth()['id'],))])


@app.post('/api/folders')
def add_folder():
    u = auth(); n = ((request.get_json(silent=True) or {}).get('name') or '').strip()[:30]
    lim = 10 if prem(u) else 3
    if not n: raise E('نام پوشه الزامی است')
    if q('select count(*) n from folders where user=?', (u['id'],), True)['n'] >= lim: raise E('سقف %d پوشه است؛ با Premium بیشتر می‌شود' % lim, 402)
    return j({'id': x('insert into folders(user,name) values(?,?)', (u['id'], n))})


@app.delete('/api/folders/<int:fid>')
def del_folder(fid):
    u = auth(); x('update members set folder=null where user_id=? and folder=?', (u['id'], fid)); x('delete from folders where id=? and user=?', (fid, u['id'])); return j()


# ---------------- messages ----------------
def add_msg(cid, u, text='', kind='text', file=None, reply=None, fwd=None, ttl=None, at=None, skip_checks=False):
    m = need_member(cid, u['id']); c = q('select * from chats where id=?', (cid,), True)
    if c['type'] == 'channel' and m['role'] not in ('owner', 'admin'): raise E('فقط مدیران کانال می‌توانند پست بگذارند', 403)
    if c['type'] == 'private':
        p = q('select user_id from members where chat_id=? and user_id!=?', (cid, u['id']), True)
        if p:
            if q('select 1 from blocks where owner=? and blocked=?', (p['user_id'], u['id']), True): raise E('این کاربر شما را مسدود کرده است', 403)
            pu = q('select privacy from users where id=?', (p['user_id'],), True)
            if jl(pu['privacy']).get('pm') == 'contacts' and not q('select 1 from contacts where owner=? and contact=?', (p['user_id'], u['id']), True): raise E('این کاربر فقط از مخاطبینش پیام می‌پذیرد', 403)
    if not skip_checks:
        if not rl('send%d' % u['id'], 60, 60): raise E('ارسال بیش از حد سریع است', 429)
        if kind not in ('text', 'image', 'video', 'file', 'voice', 'sticker'): raise E('نوع پیام نامعتبر')
        text = (text or '').strip()[:4000]
        if kind == 'text' and not text: raise E('پیام خالی است')
        if kind == 'sticker' and text not in STICKERS + (PSTICKERS if prem(u) else []): raise E('این استیکر مخصوص Premium است', 402)
        if kind in ('image', 'video', 'file', 'voice'):
            f = q('select * from files where name=? and owner=?', (file, u['id']), True)
            if not f or (f['kind'] != kind and not (kind == 'file' and f['kind'] == 'file')): raise E('فایل نامعتبر')
        if reply and not q('select 1 from messages where id=? and chat_id=?', (int(reply), cid), True): reply = None
    exp = None; created = now()
    if ttl and int(ttl) in (10, 60, 3600, 86400, 604800): exp = created + int(ttl)
    if at:
        at = int(at)
        if created < at <= created + 30 * 86400: created = at; exp = (created + int(ttl)) if exp else None
    mid = x('insert into messages(chat_id,sender,text,kind,file,reply_to,fwd,created,expire_at,upd) values(?,?,?,?,?,?,?,?,?,?)',
            (cid, u['id'], text, kind, file, reply, fwd, created, exp, seq()))
    x('update members set hidden=0 where chat_id=?', (cid,)); ping(chat_ids(cid)); return mid


MSQL = '''select m.*, u.nick sname, u.premium_until sp,
 (select text from messages where id=m.reply_to) rt, (select kind from messages where id=m.reply_to) rk,
 (select nick from users where id=(select sender from messages where id=m.reply_to)) rn from messages m join users u on u.id=m.sender '''


def mjs(rows, uid):
    ids = [r['id'] for r in rows]; R = {}
    if ids:
        for r in q('select msg_id,user_id,emoji from reactions where msg_id in (%s)' % ','.join('?' * len(ids)), ids):
            R.setdefault(r['msg_id'], {}).setdefault(r['emoji'], []).append(r['user_id'])
    out = []
    for r in rows:
        if r['deleted']: out.append({'id': r['id'], 'deleted': 1, 'upd': r['upd']}); continue
        f = q('select orig,size from files where name=?', (r['file'],), True) if r['file'] else None
        out.append({'id': r['id'], 'sender': r['sender'], 'sname': r['sname'], 'sprem': (r['sp'] or 0) > now(), 'text': r['text'], 'kind': r['kind'], 'file': r['file'],
                    'fname': f['orig'] if f else None, 'fsize': f['size'] if f else None, 'reply': {'id': r['reply_to'], 'text': r['rt'] if r['rk'] == 'text' else {'image': '🖼 تصویر', 'video': '🎬 ویدیو', 'voice': '🎤 صوتی', 'file': '📎 فایل'}.get(r['rk'], r['rt']), 'name': r['rn']} if r['reply_to'] and r['rn'] else None,
                    'fwd': r['fwd'], 'edited': r['edited'], 'pinned': r['pinned'], 'created': r['created'], 'expire': r['expire_at'], 'upd': r['upd'], 'rx': R.get(r['id'], {})})
    return out


@app.get('/api/chats/<int:cid>/messages')
def get_msgs(cid):
    u = auth(); m = need_member(cid, u['id']); a = request.args
    cur = db().execute('select n from ctr').fetchone()[0]; n = now()
    since = int(a.get('since', 0)); ts = int(a.get('ts', 0)); before = int(a.get('before', 0)); s = (a.get('q') or '').strip()
    sql = MSQL + 'where m.chat_id=? and m.id>? and m.created<=? and (m.expire_at is null or m.expire_at>?)'; args = [cid, m['cleared'], n, n]
    if s: sql += " and m.deleted=0 and m.text like ? escape '\\'"; args.append('%' + s.replace('\\', '').replace('%', '\\%').replace('_', '\\_') + '%')
    if since: rows = q(sql + ' and (m.upd>? or (m.created>? and m.created<=?)) order by m.id', args + [since, ts, n])
    else:
        if before: sql += ' and m.id<?'; args.append(before)
        rows = list(reversed(q(sql + ' order by m.id desc limit 50', args)))
    others = q('select m.last_read,m.delivered,u.privacy,u.premium_until from members m join users u on u.id=m.user_id where m.chat_id=? and m.user_id!=?', (cid, u['id']))
    pr = max([o['last_read'] for o in others if not (jl(o['privacy']).get('receipts') == 'off' and (o['premium_until'] or 0) > n)] or [0])
    pd = max([o['delivered'] for o in others] or [0])
    pins = [{'id': r['id'], 'text': r['text'] or '📎'} for r in q('select id,text from messages where chat_id=? and pinned=1 and deleted=0 order by id desc limit 5', (cid,))]
    return j({'messages': mjs(rows, u['id']), 'upd': cur, 'ts': n, 'peer_read': pr, 'peer_delivered': pd, 'pins': pins, 'role': m['role']})


@app.post('/api/chats/<int:cid>/messages')
def send_msg(cid):
    u = auth(); d = request.get_json(silent=True) or {}
    mid = add_msg(cid, u, d.get('text'), d.get('kind', 'text'), d.get('file'), d.get('reply_to'), None, d.get('ttl'), d.get('at'))
    return j({'id': mid})


@app.post('/api/chats/<int:cid>/read')
def mark_read(cid):
    u = auth(); need_member(cid, u['id'])
    x('update members set last_read=(select coalesce(max(id),0) from messages where chat_id=? and created<=?) where chat_id=? and user_id=?', (cid, now(), cid, u['id']))
    ping(chat_ids(cid)); return j()


def get_msg(mid, uid):
    r = q('select * from messages where id=? and deleted=0', (mid,), True)
    if not r: raise E('پیام یافت نشد', 404)
    return r, need_member(r['chat_id'], uid)


@app.route('/api/messages/<int:mid>', methods=['PUT', 'DELETE'])
def msg_edit(mid):
    u = auth(); r, m = get_msg(mid, u['id'])
    if request.method == 'PUT':
        t = ((request.get_json(silent=True) or {}).get('text') or '').strip()[:4000]
        if r['sender'] != u['id'] or r['kind'] != 'text' or not t: raise E('امکان ویرایش نیست', 403)
        x('update messages set text=?, edited=1, upd=? where id=?', (t, seq(), mid))
    else:
        if r['sender'] != u['id'] and m['role'] not in ('owner', 'admin'): raise E('امکان حذف نیست', 403)
        x("update messages set deleted=1, text='', file=null, upd=? where id=?", (seq(), mid))
    ping(chat_ids(r['chat_id'])); return j()


@app.post('/api/messages/<int:mid>/react')
def react(mid):
    u = auth(); r, m = get_msg(mid, u['id']); e = (request.get_json(silent=True) or {}).get('emoji')
    if e not in REACT + PREACT: raise E('واکنش نامعتبر')
    if e in PREACT and not prem(u): raise E('این واکنش مخصوص Premium است', 402)
    old = q('select emoji from reactions where msg_id=? and user_id=?', (mid, u['id']), True)
    if old and old['emoji'] == e: db().execute('delete from reactions where msg_id=? and user_id=?', (mid, u['id']))
    else: db().execute('insert or replace into reactions values(?,?,?)', (mid, u['id'], e))
    db().execute('update messages set upd=? where id=?', (seq(), mid)); db().commit(); ping(chat_ids(r['chat_id'])); return j()


@app.post('/api/messages/<int:mid>/pin')
def pin_msg(mid):
    u = auth(); r, m = get_msg(mid, u['id']); c = q('select type from chats where id=?', (r['chat_id'],), True)
    if c['type'] != 'private' and m['role'] not in ('owner', 'admin'): raise E('فقط مدیران', 403)
    x('update messages set pinned=1-pinned, upd=? where id=?', (seq(), mid)); ping(chat_ids(r['chat_id'])); return j()


@app.post('/api/messages/<int:mid>/forward')
def forward(mid):
    u = auth(); r, m = get_msg(mid, u['id']); src = q('select nick from users where id=?', (r['sender'],), True)['nick']
    for cid in (request.get_json(silent=True) or {}).get('chats', []):
        x_ = add_msg(int(cid), u, r['text'], r['kind'], None, None, src, skip_checks=True)
        if r['file']:
            f = q('select * from files where name=?', (r['file'],), True)
            x('update messages set file=? where id=?', (r['file'], x_))
    return j()


@app.post('/api/report')
def report():
    u = auth(); d = request.get_json(silent=True) or {}
    if d.get('kind') not in ('user', 'message', 'chat'): raise E('نامعتبر')
    x('insert into reports(reporter,kind,target,reason,created) values(?,?,?,?,?)', (u['id'], d['kind'], int(d.get('target', 0)), (d.get('reason') or '')[:300], now())); return j()


# ---------------- premium / stars / gifts / ads ----------------
@app.get('/api/wallet')
def wallet():
    u = auth(); plans = S('plans', PLANS)
    tx = [dict(r) for r in q('select delta,reason,created from stars_tx where user=? order by id desc limit 60', (u['id'],))]
    return j({'stars': u['stars'], 'tx': tx, 'plans': plans, 'premium_until': u['premium_until'], 'daily_ready': now() - (u['daily'] or 0) >= 86400,
              'gifts': [dict(r) for r in q('select * from gifts where active=1 order by price')], 'ad_cost': S('ad_cost', 100),
              'packs': STAR_PACKS, 'shop': S('stars_shop', 'demo')})


@app.post('/api/stars/buy')
def buy_stars():
    u = auth(); d = request.get_json(silent=True) or {}
    if S('stars_shop', 'demo') != 'demo': raise E('خرید استارز فعلاً غیرفعال است', 403)
    try: n = int(d.get('pack', 0))
    except Exception: raise E('بستهٔ نامعتبر')
    if n not in STAR_PACKS: raise E('بستهٔ نامعتبر')
    if not rl('buys%d' % u['id'], 10, 3600): raise E('تعداد خرید بیش از حد مجاز است', 429)
    db().execute('begin immediate'); stars_move(u['id'], n, 'خرید بستهٔ %d استارز (آزمایشی)' % n); db().commit()
    return j({'amount': n})


@app.post('/api/stars/daily')
def daily():
    u = auth(); db().execute('begin immediate')
    if now() - (u['daily'] or 0) < 86400: db().rollback(); raise E('پاداش امروز را گرفته‌اید')
    amt = 25 if prem(u) else 10; db().execute('update users set daily=? where id=?', (now(), u['id'])); stars_move(u['id'], amt, 'پاداش روزانه'); db().commit()
    return j({'amount': amt})


@app.post('/api/stars/send')
def send_stars():
    u = auth(); d = request.get_json(silent=True) or {}; db().execute('begin immediate')
    try: amt = int(d.get('amount', 0)); t = int(d.get('to', 0))
    except Exception: raise E('مقدار نامعتبر')
    if amt <= 0 or amt > 100000 or t == u['id'] or not q('select 1 from users where id=? and banned=0', (t,), True): db().rollback(); raise E('مقدار یا گیرنده نامعتبر است')
    stars_move(u['id'], -amt, 'ارسال به کاربر #%d' % t); stars_move(t, amt, 'دریافت از %s' % u['nick']); db().commit(); ping([t]); return j()


@app.post('/api/premium/buy')
def buy_premium():
    u = auth(); d = request.get_json(silent=True) or {}; plans = S('plans', PLANS); p = plans.get(d.get('plan')); to = int(d.get('to') or u['id'])
    if not p or not q('select 1 from users where id=? and banned=0', (to,), True): raise E('پلن یا کاربر نامعتبر')
    db().execute('begin immediate'); stars_move(u['id'], -p[1], 'خرید Premium %d روزه%s' % (p[0], '' if to == u['id'] else ' (هدیه به #%d)' % to))
    add_premium(to, p[0]); db().commit(); ping([to]); return j()


@app.post('/api/gifts/send')
def send_gift():
    u = auth(); d = request.get_json(silent=True) or {}; gf = q('select * from gifts where id=? and active=1', (int(d.get('gift', 0)),), True); to = int(d.get('to', 0))
    if not gf or to == u['id'] or not q('select 1 from users where id=? and banned=0', (to,), True): raise E('هدیه یا گیرنده نامعتبر')
    if gf['premium_only'] and not prem(u): raise E('این هدیه مخصوص Premium است', 402)
    db().execute('begin immediate'); stars_move(u['id'], -gf['price'], 'هدیه %s به #%d' % (gf['name'], to))
    db().execute('insert into user_gifts(to_user,from_user,gift,note,created) values(?,?,?,?,?)', (to, u['id'], gf['id'], (d.get('note') or '')[:100], now())); db().commit()
    cid = find_private(u['id'], to)
    if cid:
        try: add_msg(cid, u, '%s|%s|%d|%s' % (gf['emoji'], gf['name'], gf['price'], (d.get('note') or '')[:100].replace('|', ' ')), 'gift', skip_checks=True)
        except E: pass
    ping([to]); return j()


@app.post('/api/ads')
def new_ad():
    u = auth(); d = request.get_json(silent=True) or {}; t = (d.get('title') or '').strip()[:60]; b = (d.get('body') or '').strip()[:140]; l = (d.get('link') or '').strip()[:200]
    if not t or not b: raise E('عنوان و متن تبلیغ الزامی است')
    if l and not re.match(r'^https?://', l): raise E('لینک باید با http یا https شروع شود')
    db().execute('begin immediate'); stars_move(u['id'], -S('ad_cost', 100), 'درخواست تبلیغ')
    aid = db().execute('insert into ads(user,title,body,link,created) values(?,?,?,?,?)', (u['id'], t, b, l, now())).lastrowid; db().commit(); return j({'id': aid})


@app.get('/api/ads/mine')
def my_ads(): return j([dict(r) for r in q('select id,title,body,status,views,clicks from ads where user=? order by id desc', (auth()['id'],))])


@app.post('/api/ads/<int:aid>/<ev>')
def ad_event(aid, ev):
    auth()
    if ev in ('views', 'clicks'): x('update ads set %s=%s+1 where id=? and status=?' % (ev, ev), (aid, 'approved'))
    return j()


# ---------------- AI ----------------
AI_MODELS = ['openai/gpt-4o-mini', 'google/gemini-2.0-flash-001', 'meta-llama/llama-3.3-70b-instruct', 'anthropic/claude-3.5-haiku', 'deepseek/deepseek-chat']


def llm(messages, model, uid):
    key = os.environ.get('NEXO_OPENROUTER_KEY') or S('openrouter_key')
    if not key: raise E('کلید OpenRouter روی سرور تنظیم نشده است (متغیر NEXO_OPENROUTER_KEY یا پنل مدیریت ← هوش مصنوعی).', 503)
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', json.dumps({'model': model, 'messages': messages}).encode(),
                                 {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'HTTP-Referer': 'http://localhost', 'X-Title': 'NEXO'})
    try: r = json.load(urllib.request.urlopen(req, timeout=75))
    except Exception as e: raise E('اتصال به OpenRouter ناموفق بود: %s' % str(e)[:80], 502)
    tok = (r.get('usage') or {}).get('total_tokens', 0)
    x('insert into ai_usage(user,model,tokens,created) values(?,?,?,?)', (uid, model, tok, now()))
    try: return r['choices'][0]['message']['content']
    except Exception: raise E('پاسخ نامعتبر از مدل', 502)


def ai_gate(u):
    if not S('ai_enabled', True): raise E('NEXO AI توسط مدیر غیرفعال شده است', 503)
    rate = S('ai_rate', 30); rate = rate if prem(u) else max(5, rate // 3)
    if not rl('ai%d' % u['id'], rate, 3600): raise E('سقف درخواست ساعتی AI تمام شد' + ('' if prem(u) else ' (Premium سقف بیشتری دارد)'), 429)


def ai_model(u, want):
    ms = S('ai_models', AI_MODELS); dm = S('ai_model', ms[0])
    return want if (want in ms and prem(u)) else (jl(u['ai_prefs']).get('model') if jl(u['ai_prefs']).get('model') in ms and prem(u) else dm)


def system_prompt(u):
    pf = jl(u['ai_prefs']); mem = [r['text'] for r in q('select text from ai_mem where user=? order by id desc limit 30', (u['id'],))]
    s = 'تو NEXO AI هستی، دستیار هوشمند داخل پیام‌رسان NEXO. به زبان کاربر (پیش‌فرض فارسی) مفید، دقیق و مختصر پاسخ بده. برای برنامه‌نویسی کد کامل و توضیح خطاها را بده.'
    if pf.get('persona'): s += '\nشخصیت/رفتار دلخواه کاربر: ' + pf['persona'][:400]
    if mem: s += '\nچیزهایی که درباره کاربر به خاطر داری:\n- ' + '\n- '.join(mem)
    return s


@app.get('/api/ai/state')
def ai_state():
    u = auth()
    return j({'history': [dict(r) for r in reversed(q('select role,content,created from ai_msgs where user=? order by id desc limit 60', (u['id'],)))],
              'mem': [dict(r) for r in q('select id,text from ai_mem where user=? order by id desc', (u['id'],))], 'prefs': jl(u['ai_prefs']),
              'models': S('ai_models', AI_MODELS), 'model': ai_model(u, None), 'enabled': S('ai_enabled', True), 'premium': prem(u), 'has_key': bool(os.environ.get('NEXO_OPENROUTER_KEY') or S('openrouter_key'))})


@app.post('/api/ai/chat')
def ai_chat():
    u = auth(); d = request.get_json(silent=True) or {}; t = (d.get('text') or '').strip()[:4000]
    if not t: raise E('پیام خالی است')
    ai_gate(u)
    m = re.match(r'^(یادت باشه|به خاطر بسپار)[:：،\s]+(.+)$', t, re.S)
    if m and q('select count(*) n from ai_mem where user=?', (u['id'],), True)['n'] < 60: x('insert into ai_mem(user,text) values(?,?)', (u['id'], m.group(2)[:300]))
    hist = [{'role': r['role'], 'content': r['content']} for r in reversed(q('select role,content from ai_msgs where user=? order by id desc limit 16', (u['id'],)))]
    ans = llm([{'role': 'system', 'content': system_prompt(u)}] + hist + [{'role': 'user', 'content': t}], ai_model(u, d.get('model')), u['id'])
    x('insert into ai_msgs(user,role,content,created) values(?,?,?,?)', (u['id'], 'user', t, now())); x('insert into ai_msgs(user,role,content,created) values(?,?,?,?)', (u['id'], 'assistant', ans, now()))
    return j({'answer': ans})


@app.post('/api/ai/analyze')
def ai_analyze():
    u = auth(); d = request.get_json(silent=True) or {}; cid = int(d.get('chat_id', 0)); m = need_member(cid, u['id']); ai_gate(u)
    rows = q('select u.nick,m.text from messages m join users u on u.id=m.sender where m.chat_id=? and m.id>? and m.deleted=0 and m.kind="text" order by m.id desc limit 40', (cid, m['cleared']))
    if not rows: raise E('پیامی برای تحلیل نیست')
    txt = '\n'.join('%s: %s' % (r['nick'], r['text'][:300]) for r in reversed(rows))
    ans = llm([{'role': 'system', 'content': 'گفتگوی زیر را به فارسی تحلیل کن: خلاصه، موضوعات اصلی، لحن، کارهای باقی‌مانده. مختصر و ساختارمند.'}, {'role': 'user', 'content': txt}], ai_model(u, None), u['id'])
    return j({'answer': ans})


@app.post('/api/ai/mem')
def ai_mem():
    u = auth(); d = request.get_json(silent=True) or {}
    if d.get('delete'): x('delete from ai_mem where id=? and user=?', (int(d['delete']), u['id']))
    elif d.get('text'): x('insert into ai_mem(user,text) values(?,?)', (u['id'], d['text'][:300]))
    return j()


@app.post('/api/ai/prefs')
def ai_prefs():
    u = auth(); d = request.get_json(silent=True) or {}; pf = jl(u['ai_prefs'])
    if 'persona' in d: pf['persona'] = (d['persona'] or '')[:400]
    if d.get('model') in S('ai_models', AI_MODELS):
        if not prem(u): raise E('انتخاب مدل مخصوص Premium است', 402)
        pf['model'] = d['model']
    x('update users set ai_prefs=? where id=?', (json.dumps(pf, ensure_ascii=False), u['id'])); return j()


@app.delete('/api/ai/history')
def ai_clear(): x('delete from ai_msgs where user=?', (auth()['id'],)); return j()


# ---------- AI UI designer (theme system, no arbitrary code) ----------
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
def clean_theme(t, premium=True):
    o = {}
    if not isinstance(t, dict): return o
    if t.get('mode') in ('dark', 'light'): o['mode'] = t['mode']
    for k in ('accent', 'accent2', 'bg', 'surface', 'text'):
        if isinstance(t.get(k), str) and HEX.match(t[k]): o[k] = t[k].lower()
    try:
        if 'radius' in t: o['radius'] = max(0, min(28, int(t['radius'])))
        if 'scale' in t: o['scale'] = max(.85, min(1.3, float(t['scale'])))
        if 'anim' in t: o['anim'] = max(0, min(2, int(t['anim'])))
    except Exception: pass
    if t.get('nav') in ('bottom', 'floating', 'compact'): o['nav'] = t['nav']
    if t.get('bubble') in ('gradient', 'flat'): o['bubble'] = t['bubble']
    if 'glass' in t: o['glass'] = bool(t['glass'])
    if not premium: o = {k: v for k, v in o.items() if k in ('mode', 'accent')}
    return o


COL = {'بنفش': ('#8b5cf6', '#c084fc'), 'آبی': ('#3b82f6', '#60a5fa'), 'قرمز': ('#ef4444', '#f87171'), 'سبز': ('#10b981', '#34d399'), 'نارنجی': ('#f97316', '#fb923c'),
       'صورتی': ('#ec4899', '#f472b6'), 'زرد': ('#eab308', '#facc15'), 'فیروزه': ('#14b8a6', '#2dd4bf'), 'طلایی': ('#d4a017', '#f5c542')}


def rule_theme(p):
    t = {}
    if 'مشکی' in p: t.update(mode='dark', bg='#050508', surface='#101016')
    elif re.search('سفید|روشن', p): t['mode'] = 'light'
    elif re.search('تیره|شب', p): t['mode'] = 'dark'
    for k, (a, b) in COL.items():
        if k in p: t.update(accent=a, accent2=b); break
    if 'شیشه' in p: t['glass'] = True
    if 'انیمیشن' in p: t['anim'] = 0 if re.search('کمتر|بدون|خاموش', p) else 2
    if 'منوی پایین' in p or 'منو' in p: t['nav'] = 'compact' if re.search('فشرده|کوچک', p) else 'floating'
    if re.search('گرد', p): t['radius'] = 24
    if re.search('تیز|مربع', p): t['radius'] = 4
    if re.search('فونت.*(بزرگ|درشت)|بزرگ‌تر', p): t['scale'] = 1.15
    if 'ساده' in p or 'تخت' in p: t['bubble'] = 'flat'
    return t


@app.post('/api/ai/theme')
def ai_theme():
    u = auth(); d = request.get_json(silent=True) or {}; p = (d.get('prompt') or '').strip()[:300]
    if not prem(u): raise E('AI UI Designer مخصوص کاربران Premium است', 402)
    if not p: raise E('دستور خالی است')
    ai_gate(u); eng = 'rules'; note = 'تغییرات بر اساس دستور شما تنظیم شد.'; t = None
    if (os.environ.get('NEXO_OPENROUTER_KEY') or S('openrouter_key')) and S('ai_enabled', True):
        sysm = ('تو طراح رابط NEXO هستی. فقط یک شیء JSON برگردان با کلیدهای مجاز: mode(dark|light), accent, accent2, bg, surface, text (هگز #rrggbb), radius(0-28), '
                'scale(0.85-1.3), anim(0|1|2), nav(bottom|floating|compact), bubble(gradient|flat), glass(true|false), note(توضیح کوتاه فارسی). فقط کلیدهای لازم را بده. هیچ کد یا متن دیگری ننویس.')
        try:
            raw = llm([{'role': 'system', 'content': sysm}, {'role': 'user', 'content': p}], ai_model(u, None), u['id'])
            m = re.search(r'\{.*\}', raw, re.S); o = json.loads(m.group(0)); note = str(o.get('note') or note)[:200]; t = clean_theme(o); eng = 'ai'
        except Exception: t = None
    if not t: t = clean_theme(rule_theme(p))
    if not t: raise E('متوجه تغییر مورد نظر نشدم؛ مثلاً بنویسید: «مشکی و بنفش کن» یا «حالت شیشه‌ای»')
    return j({'theme': t, 'note': note, 'engine': eng})


@app.post('/api/theme')
def save_theme():
    u = auth(); d = request.get_json(silent=True) or {}
    t = None if d.get('theme') is None else clean_theme(d['theme'], prem(u))
    x('update users set theme=? where id=?', (json.dumps(t) if t else None, u['id'])); return j({'theme': t})


# ---------------- admin API ----------------
def A(level='mod'): return adm(level)


@app.get('/api/admin/stats')
def a_stats():
    A(); n = now(); c = lambda s, a=(): q(s, a, True)[0]
    return j({'users': c('select count(*) from users'), 'online': c('select count(*) from users where last_seen>?', (n - 60,)), 'messages': c('select count(*) from messages where deleted=0'),
              'groups': c("select count(*) from chats where type='group'"), 'channels': c("select count(*) from chats where type='channel'"), 'premium': c('select count(*) from users where premium_until>?', (n,)),
              'stars': c('select coalesce(sum(stars),0) from users'), 'ads': c('select count(*) from ads'), 'ads_pending': c("select count(*) from ads where status='pending'"), 'ad_views': c('select coalesce(sum(views),0) from ads'),
              'ai_requests': c('select count(*) from ai_usage'), 'ai_tokens': c('select coalesce(sum(tokens),0) from ai_usage'), 'reports': c("select count(*) from reports where status='open'"), 'banned': c('select count(*) from users where banned=1')})


@app.get('/api/admin/users')
def a_users():
    A(); s = (request.args.get('q') or '').strip(); like = '%' + s + '%'
    rows = q('select * from users where username like ? or nick like ? or phone like ? or id=? order by id desc limit 60', (like, like, like, int(s) if s.isdigit() else -1)) if s else q('select * from users order by id desc limit 60')
    return j([{'id': r['id'], 'username': r['username'], 'nick': r['nick'], 'phone': r['phone'], 'role': r['role'], 'banned': r['banned'], 'stars': r['stars'], 'premium_until': r['premium_until'], 'created': r['created'], 'last_seen': r['last_seen']} for r in rows])


@app.get('/api/admin/users/<int:uid>')
def a_user(uid):
    A(); r = q('select * from users where id=?', (uid,), True)
    if not r: raise E('یافت نشد', 404)
    return j({'user': {k: r[k] for k in ('id', 'username', 'nick', 'phone', 'bio', 'birth', 'role', 'banned', 'stars', 'premium_until', 'created', 'last_seen')},
              'tx': [dict(t) for t in q('select s.delta,s.reason,s.created,a.nick admin from stars_tx s left join users a on a.id=s.admin where s.user=? order by s.id desc limit 40', (uid,))],
              'messages': q('select count(*) n from messages where sender=?', (uid,), True)['n']})


@app.post('/api/admin/users/<int:uid>')
def a_user_op(uid):
    d = request.get_json(silent=True) or {}; op = d.get('op'); reason = (d.get('reason') or '').strip()[:200]
    t = q('select * from users where id=?', (uid,), True)
    if not t: raise E('یافت نشد', 404)
    lvl = 'owner' if op == 'role' else ('admin' if op in ('premium', 'revoke', 'stars', 'delete') else 'mod'); a = A(lvl)
    if LV[t['role']] >= LV[a['role']] and t['id'] != a['id'] and a['role'] != 'owner': raise E('نمی‌توانید روی هم‌سطح یا بالاتر عمل کنید', 403)
    if op in ('premium', 'revoke', 'stars', 'delete', 'ban', 'role') and not reason: raise E('ثبت دلیل الزامی است')
    if op == 'ban':
        if t['id'] == a['id']: raise E('نمی‌توانید خودتان را مسدود کنید')
        x('update users set banned=1 where id=?', (uid,)); x('update sessions set revoked=1 where user=?', (uid,))
    elif op == 'unban': x('update users set banned=0 where id=?', (uid,))
    elif op == 'premium': add_premium(uid, max(1, min(3650, int(d.get('days', 30))))); db().commit(); ping([uid])
    elif op == 'revoke': x('update users set premium_until=0 where id=?', (uid,))
    elif op == 'stars':
        amt = int(d.get('amount', 0))
        if amt == 0 or abs(amt) > 1000000: raise E('مقدار نامعتبر')
        db().execute('begin immediate'); stars_move(uid, amt, 'مدیر: ' + reason, a['id']); db().commit(); ping([uid])
    elif op == 'role':
        if d.get('role') not in LV or d['role'] == 'owner' or t['role'] == 'owner': raise E('نقش نامعتبر')
        x('update users set role=? where id=?', (d['role'], uid))
    elif op == 'delete':
        if t['role'] == 'owner': raise E('حذف مدیر اصلی ممکن نیست')
        for tb, col in (('sessions', 'user'), ('members', 'user_id'), ('contacts', 'owner'), ('blocks', 'owner'), ('folders', 'user'), ('posts', 'user'), ('ai_msgs', 'user'), ('ai_mem', 'user')): db().execute('delete from %s where %s=?' % (tb, col), (uid,))
        db().execute("update messages set deleted=1, text='', file=null, upd=? where sender=?", (seq(), uid)); db().execute('delete from users where id=?', (uid,)); db().commit()
    else: raise E('عملیات نامعتبر')
    audit('admin', op + ' user#%d' % uid, reason or d.get('amount', ''), a['id']); return j()


@app.get('/api/admin/chats')
def a_chats():
    A(); return j([dict(r) for r in q("select c.id,c.type,c.title,c.username,c.public,c.created,(select count(*) from members where chat_id=c.id) members,(select count(*) from messages where chat_id=c.id) msgs from chats c where c.type!='private' order by c.id desc limit 100")])


@app.delete('/api/admin/chats/<int:cid>')
def a_chat_del(cid):
    a = A(); x('delete from messages where chat_id=?', (cid,)); x('delete from members where chat_id=?', (cid,)); x('delete from chats where id=?', (cid,)); audit('admin', 'delete chat#%d' % cid, '', a['id']); return j()


@app.get('/api/admin/ads')
def a_ads(): A(); return j([dict(r) for r in q('select a.*,u.username from ads a join users u on u.id=a.user order by a.id desc limit 100')])


@app.post('/api/admin/ads/<int:aid>')
def a_ad_op(aid):
    a = A(); op = (request.get_json(silent=True) or {}).get('op'); ad = q('select * from ads where id=?', (aid,), True)
    if not ad: raise E('یافت نشد', 404)
    if op == 'approve': x("update ads set status='approved' where id=?", (aid,))
    elif op in ('reject', 'delete'):
        if ad['status'] == 'pending':
            db().execute('begin immediate'); stars_move(ad['user'], S('ad_cost', 100), 'بازگشت هزینه تبلیغ رد شده', a['id']); db().commit()
        x("update ads set status='rejected' where id=?" if op == 'reject' else 'delete from ads where id=?', (aid,))
    else: raise E('نامعتبر')
    audit('admin', '%s ad#%d' % (op, aid), '', a['id']); return j()


@app.get('/api/admin/reports')
def a_reports(): A(); return j([dict(r) for r in q('select r.*,u.username from reports r join users u on u.id=r.reporter order by r.status, r.id desc limit 100')])


@app.post('/api/admin/reports/<int:rid>')
def a_report_op(rid): a = A(); x("update reports set status='closed' where id=?", (rid,)); audit('admin', 'close report#%d' % rid, '', a['id']); return j()


@app.get('/api/admin/gifts')
def a_gifts(): A(); return j([dict(r) for r in q('select * from gifts order by id')])


@app.post('/api/admin/gifts')
def a_gift_save():
    a = A('admin'); d = request.get_json(silent=True) or {}
    if d.get('id'): x('update gifts set price=?, active=? where id=?', (max(1, int(d['price'])), int(bool(d.get('active', 1))), int(d['id'])))
    else: x('insert into gifts(name,emoji,price,premium_only,rare) values(?,?,?,?,?)', ((d.get('name') or '')[:20], (d.get('emoji') or '🎁')[:4], max(1, int(d.get('price', 10))), int(bool(d.get('premium_only'))), int(bool(d.get('rare')))))
    audit('admin', 'gift save', d, a['id']); return j()


@app.get('/api/admin/ai')
def a_ai():
    A('admin'); k = os.environ.get('NEXO_OPENROUTER_KEY') or S('openrouter_key')
    return j({'enabled': S('ai_enabled', True), 'model': S('ai_model', AI_MODELS[0]), 'models': S('ai_models', AI_MODELS), 'rate': S('ai_rate', 30), 'key': ('••••' + k[-4:]) if k else '',
              'usage': [dict(r) for r in q('select u.username,count(*) n,sum(t.tokens) tok from ai_usage t join users u on u.id=t.user group by t.user order by tok desc limit 20')]})


@app.post('/api/admin/ai')
def a_ai_set():
    a = A('admin'); d = request.get_json(silent=True) or {}
    if 'enabled' in d: setS('ai_enabled', bool(d['enabled']))
    if d.get('models'): setS('ai_models', [m.strip() for m in d['models'] if m.strip()][:20])
    if d.get('model'): setS('ai_model', d['model'])
    if d.get('rate'): setS('ai_rate', max(1, min(1000, int(d['rate']))))
    if d.get('key'): setS('openrouter_key', d['key'].strip())
    audit('admin', 'ai settings', {k: v for k, v in d.items() if k != 'key'}, a['id']); return j()


@app.get('/api/admin/system')
def a_system():
    A('admin')
    return j({'errors': [dict(r) for r in q('select * from errors order by id desc limit 30')], 'audit': [dict(r) for r in q('select a.*,u.username from audit a left join users u on u.id=a.actor order by a.id desc limit 80')],
              'backups': sorted(os.listdir(BK))[-10:], 'settings': {'reg_open': S('reg_open', True), 'rules': S('rules', RULES), 'ad_cost': S('ad_cost', 100), 'plans': S('plans', PLANS), 'stars_shop': S('stars_shop', 'demo')}})


@app.post('/api/admin/system')
def a_system_set():
    d = request.get_json(silent=True) or {}; a = A('owner' if d.get('op') == 'backup' else 'admin')
    if d.get('op') == 'backup':
        name = 'nexo-%s.db' % time.strftime('%Y%m%d-%H%M%S'); src = sqlite3.connect(DB); dst = sqlite3.connect(os.path.join(BK, name)); src.backup(dst); dst.close(); src.close()
    else:
        if 'reg_open' in d: setS('reg_open', bool(d['reg_open']))
        if d.get('rules'): setS('rules', d['rules'][:3000])
        if d.get('ad_cost'): setS('ad_cost', max(0, int(d['ad_cost'])))
        if d.get('stars_shop') in ('demo', 'off'): setS('stars_shop', d['stars_shop'])
        if d.get('plans'): setS('plans', {k: [int(v[0]), int(v[1])] for k, v in d['plans'].items() if k in PLANS})
    audit('admin', 'system ' + str(d.get('op', 'settings')), '', a['id']); return j()


@app.get('/api/admin/backup/<name>')
def a_backup_dl(name):
    A('owner'); return send_from_directory(BK, os.path.basename(name), as_attachment=True)


def purge():
    while True:
        time.sleep(60)
        try:
            c = sqlite3.connect(DB); c.execute('delete from messages where expire_at is not null and expire_at<?', (now(),)); c.commit(); c.close()
        except Exception: pass


CLIENT = r'''<!doctype html>
<html lang="fa" dir="rtl" data-mode="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
<meta name="theme-color" content="#17212b"><link rel="manifest" href="/manifest.json"><link rel="apple-touch-icon" href="/icon-192.png">
<title>NEXO</title>
<style>
:root{--bg:#0e1621;--s1:#17212b;--s2:#202b36;--s3:#2b3947;--ln:#ffffff12;--tx:#f5f5f5;--mu:#7f91a4;--ac:#2f9bef;--ac2:#2f9bef;--lk:#6ab3f3;--ok:#4fae4e;--er:#ec3942;--r:16px;--bo:#2b5278;--bi:#182533;--me:var(--ac);--sh:0 8px 28px #0008}
[data-mode=light]{--bg:#e7ebf0;--s1:#fff;--s2:#f1f3f5;--s3:#e2e6ea;--ln:#0000001a;--tx:#000;--mu:#707579;--lk:#168acd;--bo:#eeffde;--bi:#fff;--sh:0 8px 24px #0002}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}[hidden]{display:none!important}
html,body{margin:0;height:100%;background:var(--bg);color:var(--tx);font-family:Vazirmatn,Roboto,"Noto Sans Arabic",Tahoma,system-ui,sans-serif;overflow:hidden;overscroll-behavior:none;font-size:16px}
button,input,textarea,select{font:inherit;color:inherit}button{background:none;border:0;cursor:pointer;padding:0;text-align:inherit}
input,textarea,select{background:transparent;border:0;border-bottom:1.5px solid var(--s3);border-radius:0;padding:10px 2px;width:100%;outline:0;transition:border-color .2s}
input:focus,textarea:focus,select:focus{border-color:var(--ac)}
select{background:var(--s1);border:1px solid var(--s3);border-radius:10px;padding:10px 12px}
input[type=checkbox],input[type=range]{border:0;padding:0;accent-color:var(--ac)}
h1,h2,h3,p{margin:0}.mu{color:var(--mu)}.sm{font-size:.82rem}.b{font-weight:700}
.ic{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}.ic.s{width:16px;height:16px}.ic.l{width:28px;height:28px}
.sx{flex:none;vertical-align:-.2em;display:inline-block}
[dir=ltr] .chev{transform:scaleX(-1)}
#root,.page{position:fixed;inset:0;display:flex;flex-direction:column;background:var(--bg);padding-top:env(safe-area-inset-top)}
.page{z-index:20;animation:in .22s ease}@keyframes in{from{transform:translateX(-24px);opacity:0}}
[data-anim="0"] *{animation:none!important;transition:none!important}
.hdr{display:flex;align-items:center;gap:6px;padding:4px 6px;min-height:56px;background:var(--s1);position:relative;z-index:3}
.hdr .t{flex:1;font-weight:600;font-size:1.2rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 8px}
.logo{font-weight:800;font-size:1.3rem;color:var(--tx)}
.ib{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;flex:none;color:var(--mu)}.ib:active{background:var(--ln)}
.hdr .ib{color:var(--tx)}
.view{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
.nav{display:flex;background:var(--s1);padding:4px 4px calc(4px + env(safe-area-inset-bottom));border-top:1px solid var(--ln)}
.nav button{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 0;color:var(--mu);font-size:.7rem;position:relative}
.nav button.on{color:var(--ac)}
[data-nav=floating] .nav{margin:0 12px calc(10px + env(safe-area-inset-bottom));border-radius:26px;border:1px solid var(--ln);box-shadow:var(--sh)}
[data-nav=compact] .nav button span{display:none}
[data-glass=on] .hdr,[data-glass=on] .nav,[data-glass=on] .card,[data-glass=on] .sheet{background:color-mix(in srgb,var(--s1) 55%,transparent);backdrop-filter:blur(18px)}
.bdg{background:var(--ac);color:#fff;border-radius:12px;min-width:22px;height:22px;padding:0 6px;display:inline-grid;place-items:center;font-size:.78rem;font-weight:600}.bdg.m{background:var(--s3);color:var(--mu)}
.nav .bdg{position:absolute;top:0;inset-inline-end:calc(50% - 24px);min-width:18px;height:18px;font-size:.68rem}
.row{display:flex;align-items:center;gap:12px;padding:8px 14px;position:relative;width:100%}.row:active,.row.sel{background:var(--ln)}
.row .mid{flex:1;min-width:0}.row .l1,.row .l2{display:flex;align-items:center;gap:5px}.row .l1{margin-bottom:2px}.row .l1 b{font-weight:600;font-size:1.02rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .l2{color:var(--mu);font-size:.93rem}.row .l2 span.tx{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}.pf{color:var(--lk);font-weight:400}
.av{width:54px;height:54px;border-radius:50%;object-fit:cover;display:grid;place-items:center;color:#fff;font-weight:600;flex:none;position:relative;background:var(--s3)}
.avw{position:relative;flex:none}.dot{position:absolute;bottom:1px;inset-inline-end:1px;width:14px;height:14px;border-radius:50%;background:#4fd36a;border:2.5px solid var(--bg)}
.chk{position:absolute;bottom:-2px;inset-inline-end:-2px;width:22px;height:22px;border-radius:50%;background:var(--ac);display:grid;place-items:center;color:#fff;border:2px solid var(--bg)}.chk .ic{width:13px;height:13px;stroke-width:3}
.tag{font-size:.7rem;padding:2px 8px;border-radius:8px;background:color-mix(in srgb,var(--ac) 20%,transparent);color:var(--lk)}.tag.ad{background:#ffb15e33;color:#ffb15e}
.premb{display:inline-flex}
.fab{position:absolute;bottom:18px;inset-inline-end:18px;width:56px;height:56px;border-radius:50%;background:var(--ac);color:#fff;display:grid;place-items:center;box-shadow:0 4px 14px #0007;z-index:5}
.chips{display:flex;gap:6px;overflow-x:auto;padding:8px 12px;scrollbar-width:none;border-bottom:1px solid var(--ln)}.chips button{padding:6px 14px;border-radius:16px;color:var(--mu);white-space:nowrap;font-size:.9rem;font-weight:600}.chips button.on{background:color-mix(in srgb,var(--ac) 18%,transparent);color:var(--lk)}
.card{background:var(--s1);border-radius:12px;margin:10px 12px;overflow:hidden}
.btn{background:var(--ac);color:#fff;border-radius:12px;padding:13px 18px;font-weight:600;text-align:center;display:block;width:100%;letter-spacing:.2px}.btn:active{filter:brightness(.9)}.btn:disabled{opacity:.45}
.btn.g{background:var(--s2);color:var(--lk)}.btn.d{background:var(--er)}
.set{display:flex;align-items:center;gap:16px;padding:12px 18px;width:100%;position:relative}.set:active{background:var(--ln)}.set .mid{flex:1;min-width:0;font-size:1rem}.set small{display:block;color:var(--mu);font-size:.8rem;margin-top:1px}
.set>.ic:first-child{color:var(--mu)}
.si{width:30px;height:30px;border-radius:8px;display:grid;place-items:center;color:#fff;flex:none}.si .ic{width:19px;height:19px;color:#fff}
.sec{padding:14px 18px 6px;color:var(--lk);font-weight:600;font-size:.9rem}
.pad{padding:16px;display:flex;flex-direction:column;gap:12px}.lab{font-size:.82rem;color:var(--lk);margin-bottom:2px;display:block}
.sw{position:relative;width:44px;height:24px;flex:none}.sw input{opacity:0;position:absolute;inset:0;z-index:2;width:100%;height:100%;margin:0}.sw i{position:absolute;inset:0;background:var(--s3);border-radius:12px;transition:.2s}.sw i:after{content:"";position:absolute;top:2px;inset-inline-start:2px;width:20px;height:20px;border-radius:50%;background:#fff;transition:.2s;box-shadow:0 1px 3px #0005}.sw input:checked+i{background:var(--ac)}.sw input:checked+i:after{transform:translateX(-20px)}[dir=ltr] .sw input:checked+i:after{transform:translateX(20px)}
.toast{position:fixed;bottom:90px;left:50%;transform:translateX(-50%);background:#000c;color:#fff;padding:11px 18px;border-radius:12px;z-index:99;max-width:88vw;text-align:center;animation:in .2s;font-size:.92rem}
.ov{position:fixed;inset:0;background:#0008;z-index:50;display:flex;align-items:flex-end;justify-content:center;animation:fd .2s}@keyframes fd{from{opacity:0}}
.sheet{background:var(--s1);width:100%;max-width:560px;border-radius:16px 16px 0 0;padding:6px 0 calc(10px + env(safe-area-inset-bottom));max-height:88vh;overflow-y:auto;animation:up .24s ease}@keyframes up{from{transform:translateY(60px);opacity:0}}
.sheet.tall{min-height:60vh;max-height:94vh;padding-top:0}.grab{display:block;width:36px;height:4px;border-radius:2px;background:var(--s3);margin:8px auto}
.sheet .set.d{color:var(--er)}.sheet .set.d .ic{color:var(--er)}
.mdl{align-self:center;background:var(--s1);border-radius:16px;padding:20px;width:min(92vw,420px);max-height:84vh;overflow:auto;box-shadow:var(--sh)}
.wel{display:flex;flex-direction:column;align-items:center;gap:16px;padding:28px 28px;overflow-y:auto;flex:1;text-align:center;justify-content:center}
.wel h1{font-size:1.7rem;line-height:1.5}.wel .lg{width:120px;height:120px;border-radius:50%;background:var(--ac);display:grid;place-items:center;color:#fff;box-shadow:0 10px 40px color-mix(in srgb,var(--ac) 45%,transparent)}.wel .lg .ic{width:56px;height:56px;stroke-width:1.5}
.wel ul{margin:0;padding:0;list-style:none;color:var(--mu);line-height:2}.ck{display:flex;gap:10px;align-items:center;text-align:start;font-size:.92rem}.ck input{width:22px;height:22px;flex:none}.ck a{color:var(--lk);text-decoration:none;cursor:pointer}
.duck{width:150px;height:150px}.duck .hd{transform-origin:100px 118px;animation:look 3.2s ease-in-out infinite}@keyframes look{0%,100%{transform:rotate(0)}20%{transform:rotate(-16deg)}45%{transform:rotate(14deg)}70%{transform:rotate(-8deg)}}
.apick{width:96px;height:96px;border-radius:50%;background:var(--s2);display:grid;place-items:center;align-self:center;overflow:hidden;color:var(--ac)}.apick img{width:100%;height:100%;object-fit:cover}
.big{display:flex;flex-direction:column;align-items:center;gap:4px;padding:20px 12px 8px;text-align:center}.big .av{width:110px;height:110px;font-size:2.4rem}
.acts{display:flex;gap:8px;padding:6px 12px}.acts button{flex:1;background:var(--s1);border-radius:12px;padding:10px 4px;display:flex;flex-direction:column;align-items:center;gap:5px;font-size:.78rem;color:var(--lk);font-weight:600}.acts button:active{background:var(--s2)}
.kv{padding:11px 18px;border-bottom:1px solid var(--ln)}.kv:last-child{border:0}.kv small{color:var(--mu);display:block;font-size:.78rem;margin-top:2px}
.seg{display:flex;background:var(--s2);border-radius:20px;margin:10px 14px;padding:3px;gap:2px}.seg button{flex:1;padding:7px;border-radius:17px;text-align:center;color:var(--mu);font-weight:600;font-size:.92rem}.seg button.on{background:var(--s3);color:var(--lk)}
.post{padding:12px 18px;border-bottom:1px solid var(--ln);background:var(--s1)}.post img{width:100%;border-radius:12px;margin-top:8px;max-height:320px;object-fit:cover}
.gs{display:flex;flex-wrap:wrap;gap:8px;padding:0 14px 12px}.gs span{background:var(--s2);border-radius:14px;padding:6px 10px;font-size:1.2rem}
#msgs{flex:1;overflow-y:auto;padding:8px 10px;display:flex;flex-direction:column;gap:3px;-webkit-overflow-scrolling:touch;background:radial-gradient(circle at 20% 10%,#1d3347 0,transparent 45%),radial-gradient(circle at 85% 80%,#162a1f 0,transparent 40%),var(--bg)}
[data-mode=light] #msgs{background:radial-gradient(circle at 20% 10%,#c9e0c0 0,transparent 50%),radial-gradient(circle at 85% 80%,#b9d3e6 0,transparent 45%),#d6e3d0}
[data-wall=off] #msgs{background:var(--bg)}
.day{align-self:center;background:#0006;color:#fff;font-size:.78rem;padding:3px 12px;border-radius:12px;margin:8px 0;font-weight:500}
.bb{max-width:84%;padding:6px 10px 4px;border-radius:15px;position:relative;word-break:break-word;white-space:pre-wrap;line-height:1.6;box-shadow:0 1px 1px #0003}
.bb.me{margin-left:auto;background:var(--bo);border-end-end-radius:4px}.bb.th{margin-right:auto;background:var(--bi);border-end-start-radius:4px}
[data-bubble=gradient] .bb.me{background:linear-gradient(160deg,color-mix(in srgb,var(--bo) 85%,#8a63f0),var(--bo))}
.bb.sel{outline:2px solid var(--ac)}.bb .nm{font-weight:600;font-size:.82rem;color:var(--lk);display:block}
.bb .ft{display:flex;gap:4px;justify-content:flex-end;align-items:center;font-size:.68rem;color:var(--mu);margin-top:1px;white-space:nowrap}.bb.me .ft{color:color-mix(in srgb,var(--lk) 80%,var(--tx))}.bb .ft .ic{width:15px;height:15px}.rd{color:var(--lk)}
.bb .rp{border-inline-start:2.5px solid var(--lk);background:color-mix(in srgb,var(--lk) 12%,transparent);padding:3px 8px;border-radius:6px;margin-bottom:4px;font-size:.82rem;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bb .fw{font-size:.78rem;color:var(--lk);display:block}.bb img.im,.bb video{max-width:100%;border-radius:10px;display:block;max-height:340px}.bb audio{max-width:100%;height:40px}
.bb .fl{display:flex;gap:10px;align-items:center;background:#0000002a;border-radius:10px;padding:8px 10px;color:inherit;text-decoration:none}
.stk{align-self:flex-start;font-size:3.6rem;line-height:1.1;padding:0 6px;cursor:pointer}.stk.me{margin-left:auto}.stk.th{margin-right:auto}
.anim{display:inline-block;animation:bnc 1.3s ease-in-out infinite}@keyframes bnc{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-6px) scale(1.12)}}
.rxs{display:flex;gap:4px;flex-wrap:wrap;margin-top:3px}.rx{background:#ffffff1c;border-radius:12px;padding:1px 8px;font-size:.82rem}.rx.mine{background:color-mix(in srgb,var(--ac) 40%,transparent)}
.comp{display:flex;align-items:flex-end;gap:6px;padding:6px 6px calc(6px + env(safe-area-inset-bottom));background:var(--bg)}
.comp .pill{flex:1;display:flex;align-items:flex-end;background:var(--s1);border-radius:24px;min-height:46px;padding:0 2px}
.comp .pill .ib{color:var(--mu);height:46px}
.comp textarea{flex:1;resize:none;max-height:120px;border:0;padding:12px 4px;line-height:1.5;min-height:46px}
.send{width:46px;height:46px;border-radius:50%;background:var(--ac);color:#fff;display:grid;place-items:center;flex:none}.send.rec{background:var(--er);animation:bnc 1s infinite}
.bar{display:flex;align-items:center;gap:10px;padding:7px 14px;background:var(--s1);font-size:.88rem;border-top:1px solid var(--ln)}.bar .tx{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#pinb{border-inline-start:3px solid var(--ac);border-top:0;color:var(--tx)}#pinb .ic{color:var(--ac)}
.panel{display:grid;grid-template-columns:repeat(auto-fill,minmax(46px,1fr));gap:2px;padding:10px;background:var(--s1);max-height:210px;overflow-y:auto;font-size:1.7rem;text-align:center}.panel span{padding:4px;cursor:pointer}
.prog{height:4px;background:#fff4;border-radius:2px;overflow:hidden;margin-top:4px}.prog i{display:block;height:100%;background:#fff}
.ai-m{max-width:88%;padding:9px 13px;border-radius:15px;white-space:pre-wrap;word-break:break-word;line-height:1.7}.ai-m.u{margin-left:auto;background:var(--bo)}.ai-m.a{margin-right:auto;background:var(--bi)}
.ai-m pre{background:#0006;color:#e6e9ff;padding:10px;border-radius:10px;overflow-x:auto;direction:ltr;text-align:left;white-space:pre}
.tbl{width:100%;border-collapse:collapse;font-size:.85rem}.tbl td,.tbl th{padding:8px;border-bottom:1px solid var(--ln);text-align:start}
.plan{display:flex;justify-content:space-between;align-items:center;background:var(--s2);border-radius:14px;padding:14px}
.st.on{color:var(--lk)}
/* ---- telegram-like additions ---- */
.gm{align-self:center;background:#0007;backdrop-filter:blur(8px);color:#fff;border-radius:16px;padding:14px 22px;margin:8px 0;text-align:center;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:190px;cursor:pointer}
.gm .gem{font-size:3.4rem;line-height:1.1;animation:bnc 2.2s ease-in-out infinite}.gm b{font-size:.92rem}.gm .gp{font-size:.8rem;opacity:.85}.gm i{font-style:normal;font-size:.8rem;opacity:.8}
.phd{position:relative;display:flex;flex-direction:column;align-items:center;padding:18px 14px 10px;background:linear-gradient(180deg,color-mix(in srgb,var(--ac) 22%,var(--s1)),var(--s1));text-align:center;gap:3px}
.phd .av{width:104px;height:104px;font-size:2.6rem;box-shadow:0 4px 18px #0006}.phd h2{font-size:1.35rem;margin-top:8px;display:flex;align-items:center;gap:6px;justify-content:center}.phd .stt{color:var(--mu);font-size:.88rem}.phd .stt.on{color:var(--lk)}
.ptabs{display:flex;justify-content:center;margin:6px 12px 10px}.ptabs .seg{margin:0;background:var(--s1);padding:3px}.ptabs .seg button{padding:7px 16px;white-space:nowrap}
.ggrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:0 10px 90px}
.gcard{position:relative;aspect-ratio:1/1.08;border-radius:14px;background:var(--s1);display:grid;place-items:center;overflow:hidden}
.gcard.rare{background:linear-gradient(160deg,var(--g1),var(--g2))}.gcard .gem{font-size:3.2rem;z-index:1;filter:drop-shadow(0 4px 6px #0005)}
.gcard .gfrom{position:absolute;top:6px;inset-inline-start:6px;z-index:2}.gcard .gfrom .av{border:1.5px solid #0004}
.gcard .gno{position:absolute;top:6px;inset-inline-end:7px;font-size:.62rem;color:#fff;opacity:.85;z-index:2;direction:ltr}
.gcard .gpat{position:absolute;color:#fff;opacity:.28;font-size:.7rem;z-index:0}
.gcard .gpr{position:absolute;bottom:6px;left:0;right:0;text-align:center;font-size:.76rem;z-index:2;font-weight:600;display:flex;gap:3px;justify-content:center;align-items:center}
.gcard.shop{background:var(--s1);padding-bottom:22px}.gcard.shop:active{background:var(--s2)}.gcard.shop.lock{opacity:.55}
.gcard .gtag{position:absolute;top:6px;inset-inline-end:6px;font-size:.6rem;background:#8a63f0;color:#fff;border-radius:6px;padding:1px 6px;z-index:2}
.gfab{position:absolute;bottom:22px;left:50%;transform:translateX(-50%);background:var(--ac);color:#fff;border-radius:24px;padding:12px 22px;display:flex;gap:8px;align-items:center;font-weight:600;box-shadow:0 6px 18px #0009;z-index:6;white-space:nowrap}
.gdet{text-align:center;padding:4px 0 6px}.gdet .hero{margin:-20px -20px 12px;padding:30px 20px 18px;border-radius:16px 16px 0 0;background:var(--s2);position:relative;overflow:hidden}
.gdet .hero.rare{background:linear-gradient(160deg,var(--g1),var(--g2))}.gdet .hero .gem{font-size:6rem;line-height:1.1;position:relative;z-index:1;display:block;animation:bnc 2.6s ease-in-out infinite;filter:drop-shadow(0 8px 10px #0006)}
.gdet .hero .gpat{position:absolute;color:#fff;opacity:.3;font-size:1rem}
.gdet .kvr{display:flex;justify-content:space-between;align-items:center;padding:11px 2px;border-bottom:1px solid var(--ln);text-align:start}.gdet .kvr:last-of-type{border:0}.gdet .kvr .lp{display:flex;align-items:center;gap:6px;color:var(--lk);font-weight:600}
.pcs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:0 12px}
.pc{position:relative;background:var(--s2);border-radius:14px;padding:26px 6px 12px;text-align:center;display:flex;flex-direction:column;gap:3px;align-items:center;border:2px solid transparent}.pc.on{border-color:var(--ac)}.pc:active{filter:brightness(1.1)}
.pc b{font-size:1.05rem}.pc .pp{color:var(--lk);font-size:.78rem;background:color-mix(in srgb,var(--ac) 16%,transparent);border-radius:8px;padding:2px 8px;margin-top:4px;display:inline-flex;gap:3px;align-items:center}
.pc .rib{position:absolute;top:0;inset-inline-start:0;inset-inline-end:0;background:linear-gradient(90deg,#8a63f0,#5b8cff);color:#fff;font-size:.68rem;font-weight:700;padding:2px 0;border-radius:12px 12px 0 0;direction:ltr}
.phero{position:relative;display:flex;flex-direction:column;align-items:center;padding:14px 20px 4px;text-align:center;gap:8px}
.phero .stage{position:relative;width:200px;height:170px;display:grid;place-items:center}.pst{width:100px;height:100px;filter:drop-shadow(0 8px 14px #f59a1b66);animation:flt 4s ease-in-out infinite}.phero .pst{width:150px;height:150px;filter:drop-shadow(0 10px 18px #4a6bd88a)}@keyframes flt{50%{transform:translateY(-6px) rotate(3deg)}}
.sp{position:absolute;width:7px;height:7px;background:#5aa6ff;clip-path:polygon(50% 0,62% 38%,100% 50%,62% 62%,50% 100%,38% 62%,0 50%,38% 38%);animation:tw 2.6s ease-in-out infinite}@keyframes tw{0%,100%{opacity:.15;transform:scale(.5)}50%{opacity:1;transform:scale(1.2)}}
.phero h2{font-size:1.15rem;line-height:1.7}.phero p{color:var(--mu);font-size:.9rem;line-height:1.7;max-width:330px}.phero .hl{color:var(--lk)}
.feat{display:flex;gap:14px;padding:11px 16px;align-items:center;position:relative;width:100%}.feat+.feat:before{content:"";position:absolute;top:0;inset-inline-start:66px;inset-inline-end:0;height:1px;background:var(--ln)}
.feat .fi{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;color:#fff;flex:none;font-weight:800;font-size:.8rem}.feat .fi .ic{width:20px;height:20px}
.feat b{display:block;font-size:.98rem;font-weight:600}.feat span.d{display:block;color:var(--mu);font-size:.84rem;line-height:1.6;margin-top:1px}.feat>.mid{flex:1;min-width:0}.feat>.ic{color:var(--mu);width:18px;height:18px}
.stick{position:absolute;bottom:0;left:0;right:0;padding:10px 14px calc(10px + env(safe-area-inset-bottom));background:linear-gradient(0deg,var(--bg) 70%,transparent);z-index:5}
.stick .btn{border-radius:24px;padding:14px;font-size:1rem;background:linear-gradient(90deg,#3a8dff,#2fa8f3);box-shadow:0 4px 14px #1b76d855}
.shero{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 20px 14px;background:var(--s1);margin-bottom:8px;text-align:center}
.shero .stage{position:relative;width:170px;height:130px;display:grid;place-items:center}.shero .pst{width:100px;height:100px}
.shero .bal{font-size:2.4rem;font-weight:800;display:flex;gap:8px;align-items:center;direction:ltr}.shero .btn{max-width:300px;margin-top:6px;border-radius:22px}
.sp.g{background:#ffc83d}
.pk{display:flex;align-items:center;gap:12px;padding:12px 18px;width:100%}.pk:active{background:var(--ln)}.pk .pks{display:flex;width:56px}.pk .pks .sx{margin-inline-end:-8px}.pk b{flex:1;font-size:1rem}.pk .pkp{color:var(--lk);background:color-mix(in srgb,var(--ac) 16%,transparent);padding:4px 12px;border-radius:14px;font-size:.82rem;font-weight:600}
.txr{display:flex;align-items:center;gap:12px;padding:10px 16px;width:100%}.txr .tic{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;color:#fff;flex:none}.txr .tic .ic{width:21px;height:21px}.txr .mid{flex:1;min-width:0}.txr .mid small{display:block;color:var(--mu);font-size:.78rem}.txr .am{font-weight:700;display:flex;gap:4px;align-items:center;direction:ltr}.txr .am.p{color:var(--ok)}.txr .am.n{color:var(--er)}
.gwallet{display:inline-flex;align-items:center;gap:5px;background:var(--s2);border-radius:14px;padding:4px 12px;font-weight:700;font-size:.9rem}
.shh{display:flex;align-items:center;gap:6px;padding:6px 10px 2px}.shh .t{flex:1;text-align:center;font-weight:600;font-size:1.05rem}
#fabw .fab,#fabw .gfab{bottom:76px}
.phero .stage .pst{position:relative;z-index:1}
</style></head><body><svg width="0" height="0" style="position:absolute"><defs><linearGradient id="gst" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffd54a"/><stop offset="1" stop-color="#f59a1b"/></linearGradient></defs></svg><div id="root"></div><div id="pages"></div>
<script>
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const CSRF='__CSRF__';
const PT={search:'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.3-4.3',plus:'M12 5v14M5 12h14',back:'M5 12h14M13 6l6 6-6 6',send:'M22 2L11 13M22 2l-7 20-4-9-9-4z',chat:'M21 12a8 8 0 0 1-11.5 7.2L3 21l1.8-6A8 8 0 1 1 21 12z',user:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',gear:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1',pin:'M12 17v5M9 3h6l-1 7 3 3H7l3-3z',mute:'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4M3 3l18 18',bell:'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4',arch:'M3 4h18v4H3zM5 8v12h14V8M10 12h4',trash:'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14',check:'M4 12l5 5L20 6',dbl:'M2 13l4 4 8-9M10 15l2 2 9-10',x:'M6 6l12 12M18 6L6 18',folder:'M3 6h6l2 2h10v11H3z',eye:'M3 12s4-7 9-7 9 7 9 7-4 7-9 7-9-7-9-7zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',broom:'M4 20l8-8M13 5l6 6-5 5-6-6z',clip:'M21 11l-9 9a6 6 0 0 1-9-9l9-9a4 4 0 0 1 6 6l-9 9a2 2 0 0 1-3-3l8-8',mic:'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3',smile:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 14a5 5 0 0 0 8 0M9 9h.01M15 9h.01',star:'M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z',more:'M12 5h.01M12 12h.01M12 19h.01',reply:'M9 14L4 9l5-5M4 9h10a6 6 0 0 1 6 6v3',fwd:'M15 14l5-5-5-5M20 9H10a6 6 0 0 0-6 6v3',edit:'M4 20h4L19 9l-4-4L4 16z',mega:'M3 11v3l11 5V6zM14 6l6-3v16l-6-3',spark:'M12 2l2 6 6 2-6 2-2 6-2-6-6-2 6-2z',lock:'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',users:'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M17 3a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6',file:'M6 2h8l6 6v14H6zM14 2v6h6',gift:'M3 9h18v4H3zM5 13v8h14v-8M12 9v12M12 9c-3 0-5-4-2-4s2 4 2 4zm0 0c3 0 5-4 2-4s-2 4-2 4z',clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',globe:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',cam:'M4 8h3l2-3h6l2 3h3v11H4zM12 11a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',dev:'M4 5h16v11H4zM2 19h20',shield:'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',pal:'M12 3a9 9 0 1 0 0 18c1.5 0 2-1 1.5-2s0-2 1.5-2h2a4 4 0 0 0 4-4c0-5-4-10-9-10zM7.5 11h.01M10 7.5h.01M15 7.5h.01',db:'M4 6c0-2 16-2 16 0v12c0 2-16 2-16 0zM4 12c0 2 16 2 16 0',help:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17h.01',flag:'M5 21V4h12l-2 4 2 4H5',copy:'M8 8h12v13H8zM4 16V3h12',chev:'M15 6l-6 6 6 6',bolt:'M13 2L4 14h7l-1 8 9-12h-7z',qr:'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h3v3h-3zM20 14v3M14 20h3M20 20h.01',heart:'M12 20s-8-5-8-11a4.5 4.5 0 0 1 8-2.5A4.5 4.5 0 0 1 20 9c0 6-8 11-8 11z'};
const ic=(n,c='')=>`<svg class="ic ${c}" viewBox="0 0 24 24"><path d="${PT[n]||''}"/></svg>`;
let ME=null,CH=[],ADS=[],FOLD=[],AIon=true,TAB='chats',FILT=null,ARCH=false,SEL=new Set(),CUR=null,LANG=localStorage.nexo_lang||'fa',TM=null,SOCK=null,PREV={},OV=0;
const EN={'گفتگوها':'Chats','مخاطبین':'Contacts','تنظیمات':'Settings','پروفایل':'Profile','همه':'All','بایگانی':'Archive','آنلاین':'online','تنظیم عکس':'Set photo','ویرایش اطلاعات':'Edit info','شماره تلفن':'Phone','نام کاربری':'Username','تاریخ تولد':'Birthday','پست‌ها':'Posts','پست‌های بایگانی‌شده':'Archived posts','افزودن پست':'Add post','حساب کاربری':'Account','تنظیمات گفتگو':'Chat settings','حریم خصوصی':'Privacy','اعلان‌ها':'Notifications','داده‌ها و ذخیره‌سازی':'Data & storage','پوشه‌های گفتگو':'Chat folders','دستگاه‌ها':'Devices','زبان':'Language','پوسته و تم':'Theme','هوش مصنوعی':'AI','Premium':'Premium','کیف پول Stars':'Stars wallet','خروج':'Log out','پیام':'Message','ارسال':'Send','پیام بنویسید...':'Message...','گفتگوی جدید':'New chat','ایجاد گروه':'New group','ایجاد کانال':'New channel','افزودن مخاطب':'Add contact','جستجو...':'Search...','ورود | ثبت‌نام':'Login | Sign up','امنیت':'Security','فونت':'Font','صدا':'Sound','بلاک‌شدگان':'Blocked users','تبلیغات':'Ads','ذخیره':'Save','لغو':'Cancel','حذف':'Delete','پین شده':'Pinned'};
const t=s=>LANG==='en'&&EN[s]||s;
const loc=()=>LANG==='en'?'en-US':'fa-IR',nf=n=>Number(n||0).toLocaleString(loc());
const fT=ts=>new Date(ts*1000).toLocaleTimeString(loc(),{hour:'2-digit',minute:'2-digit',hour12:false});
const fD=ts=>new Date(ts*1000).toLocaleDateString(LANG==='en'?'en-US':'fa-IR-u-ca-persian',{day:'numeric',month:'long',year:'numeric'});
const fDT=ts=>new Date(ts*1000).toDateString()===new Date().toDateString()?fT(ts):fD(ts);
const seen=o=>o.online?t('آنلاین'):(o.last_seen?'آخرین بازدید '+fDT(o.last_seen):'مدت‌ها پیش');
const sz=b=>b>1048576?(b/1048576).toFixed(1)+' MB':Math.ceil(b/1024)+' KB';
async function api(p,m='GET',b){const o={method:m,headers:{'X-CSRF':CSRF},credentials:'same-origin'};if(b!==undefined){o.headers['Content-Type']='application/json';o.body=JSON.stringify(b)}
 const r=await fetch('/api/'+p,o);let d={};try{d=await r.json()}catch(e){}if(!r.ok){const e=new Error(d.error||'خطا ('+r.status+')');e.status=r.status;if(r.status===401&&ME){ME=null;boot()}throw e}return d}
function toast(m){const e=document.createElement('div');e.className='toast';e.textContent=m;document.body.append(e);setTimeout(()=>e.remove(),2800)}
const run=f=>async(...a)=>{try{return await f(...a)}catch(e){toast(e.message)}};
function av(o,s=52){const st=`width:${s}px;height:${s}px;font-size:${s/2.6}px`;if(o.avatar)return`<img class="av" style="${st}" src="/f/${esc(o.avatar)}" alt="">`;
 return`<div class="av" style="${st};background:hsl(${((o.id||1)*67)%360} 50% 42%)">${esc((o.title||o.nick||'؟').trim().slice(0,1))}</div>`}
const prb=o=>o&&o.premium?`<span class="premb" title="Premium">${SI(17)}</span>`:'';
function sheet(items,head){const ov=document.createElement('div');ov.className='ov';const sh=document.createElement('div');sh.className='sheet';ov.append(sh);
 if(head){const h=document.createElement('div');h.innerHTML=head;sh.append(h)}
 items.filter(Boolean).forEach(it=>{const b=document.createElement('button');b.className='set'+(it.d?' d':'');b.innerHTML=(it.i?ic(it.i):'')+`<span class="mid">${esc(it.t)}</span>`;b.onclick=()=>{ov.remove();run(it.f)()};sh.append(b)});
 ov.onclick=e=>{if(e.target===ov)ov.remove()};document.body.append(ov);return ov}
function modal(html,mount){const ov=document.createElement('div');ov.className='ov';ov.style.alignItems='center';ov.innerHTML=`<div class="mdl">${html}</div>`;ov.onclick=e=>{if(e.target===ov)ov.remove()};document.body.append(ov);mount&&mount(ov.firstChild,()=>ov.remove());return ov}
const ask=(msg,okText='تأیید')=>new Promise(r=>{const m=modal(`<p style="margin-bottom:16px">${esc(msg)}</p><div style="display:flex;gap:10px"><button class="btn g" id="n">${t('لغو')}</button><button class="btn" id="y">${esc(okText)}</button></div>`,(e,c)=>{$('#y',e).onclick=()=>{c();r(true)};$('#n',e).onclick=()=>{c();r(false)}})});
const prompt2=(msg,val='',type='text')=>new Promise(r=>{modal(`<label class="lab">${esc(msg)}</label><input id="v" type="${type}" value="${esc(val)}"><div style="display:flex;gap:10px;margin-top:14px"><button class="btn g" id="n">${t('لغو')}</button><button class="btn" id="y">${t('ذخیره')}</button></div>`,(e,c)=>{$('#v',e).focus();$('#y',e).onclick=()=>{c();r($('#v',e).value)};$('#n',e).onclick=()=>{c();r(null)}})});
const PAGES=[];
function page(title,body,mount,hdrExtra=''){const el=document.createElement('div');el.className='page';el.innerHTML=`<div class="hdr"><button class="ib" data-a="back">${ic('back')}</button><div class="t">${esc(title)}</div>${hdrExtra}</div><div class="view" id="pv${PAGES.length}">${body}</div>`;
 $('#pages').append(el);PAGES.push(el);history.pushState({p:PAGES.length},'');mount&&mount(el);return el}
function closePage(){const e=PAGES.pop();if(e){e.remove();if(e._close)e._close()}}
window.addEventListener('popstate',()=>{const n=(history.state&&history.state.p)||0;while(PAGES.length>n)closePage();if(!PAGES.length&&ARCH&&!(history.state&&history.state.a)){ARCH=false;drawChats()}});
const later=f=>setTimeout(run(f),180);
function back(){if(PAGES.length)history.back()}
function applyTheme(th){const r=document.documentElement,p=(ME&&ME.prefs)||{};th={...(p.mode?{mode:p.mode}:{}),...(p.accent?{accent:p.accent}:{}),...(th||{})};
 ['--ac','--ac2','--bg','--s1','--tx','--r','--bo','--lk'].forEach(k=>r.style.removeProperty(k));
 r.dataset.mode=th.mode||'dark';if(th.accent){const lt=th.mode==='light';r.style.setProperty('--ac',th.accent);r.style.setProperty('--ac2',th.accent2||th.accent);r.style.setProperty('--bo','color-mix(in srgb,'+th.accent+' 55%,'+(lt?'#fff':'#0e1621')+')');r.style.setProperty('--lk','color-mix(in srgb,'+th.accent+' 78%,'+(lt?'#000':'#fff')+')')}
 if(th.bg){r.style.setProperty('--bg',th.bg);r.style.setProperty('--s1',th.surface||th.bg)}if(th.text)r.style.setProperty('--tx',th.text);
 r.style.setProperty('--r',(th.radius??16)+'px');r.dataset.nav=th.nav||'bottom';r.dataset.glass=th.glass?'on':'off';r.dataset.anim=th.anim===0?'0':(th.anim===2?'2':'1');r.dataset.bubble=th.bubble||'gradient';
 r.style.fontSize=(16*(th.scale||p.scale||1))+'px';document.body.style.fontFamily=p.font==='serif'?'Georgia,"Noto Naskh Arabic",serif':p.font==='mono'?'ui-monospace,Menlo,monospace':'';
 r.dataset.wall=p.wall===false?'off':'on';
 document.querySelector('meta[name=theme-color]').content=getComputedStyle(r).getPropertyValue('--s1')||'#17212b'}
function setLang(l){LANG=l;localStorage.nexo_lang=l;document.documentElement.lang=l;document.documentElement.dir=l==='en'?'ltr':'rtl'}
async function savePrefs(o){const d=await api('me','POST',{prefs:o});ME=d.user;applyTheme(ME.theme)}

/* ---------- boot / auth ---------- */
async function boot(){setLang(LANG);const d=await api('me').catch(()=>({user:null}));ME=d.user;
 if(ME){applyTheme(ME.theme);if(ME.prefs.lang&&ME.prefs.lang!==LANG)setLang(ME.prefs.lang);main()}else{applyTheme(null);localStorage.nexo_seen?authView('login'):welcome()}}
function welcome(){$('#root').innerHTML=`<div class="wel"><div class="lg">${ic('send')}</div><h1>سلام!<br>به NEXO خوش آمدی.</h1><p class="b">هدفمون چیه؟</p><ul><li>🔒 ارتباط امن</li><li>🇮🇷 ساخت یک پیام‌رسان ایرانی قدرتمند و پیشرفته</li></ul>
 <label class="ck"><input type="checkbox" id="ok"><span>«<a data-a="rules">قوانین</a> ورود به NEXO را خوانده‌ام و قبول می‌کنم.»</span></label><button class="btn" id="go" disabled>${t('ورود | ثبت‌نام')}</button></div>`;
 $('#ok').onchange=e=>$('#go').disabled=!e.target.checked;$('#go').onclick=()=>{localStorage.nexo_seen=1;authView('login')}}
const duck=`<svg class="duck" viewBox="0 0 200 200"><ellipse cx="100" cy="150" rx="62" ry="40" fill="#ffd23f"/><path d="M60 150q-22-6-20-28 26 4 38 16z" fill="#f2b705"/><g class="hd"><circle cx="100" cy="82" r="38" fill="#ffd23f"/><ellipse cx="124" cy="92" rx="26" ry="11" fill="#ff8a2b"/><circle cx="106" cy="72" r="6.5" fill="#222"/><circle cx="108" cy="70" r="2" fill="#fff"/></g><path d="M70 190h18M112 190h18" stroke="#ff8a2b" stroke-width="7" stroke-linecap="round"/></svg>`;
function authView(mode,pre){const reg=mode==='register';let ava=null;
 $('#root').innerHTML=`<div class="view"><div class="pad" style="max-width:460px;margin:auto"><div style="text-align:center"><div class="logo" style="font-size:2rem">NEXO</div><h2 style="margin-top:8px">${reg?'کاربر جدید، خوش اومدی!':'ورود به حساب'}</h2></div>
 ${reg?`<label class="apick" id="ap">${ic('cam','l')}<input type="file" accept="image/*" hidden id="af"></label><div><label class="lab">نام مستعار</label><input id="nick" maxlength="40"></div><div><label class="lab">Username (یکتا)</label><input id="un" dir="ltr" autocapitalize="off"></div><div><label class="lab">شماره تلفن (اختیاری)</label><input id="ph" dir="ltr" inputmode="tel" placeholder="09xxxxxxxxx"></div><div><label class="lab">تاریخ تولد (اختیاری)</label><input id="bd" type="date"></div>`
 :`<div><label class="lab">شماره تلفن یا نام کاربری</label><input id="id" dir="ltr" autocapitalize="off" value="${esc(pre||'')}"></div>`}
 <div><label class="lab">رمز عبور</label><input id="pw" type="password" dir="ltr"></div>
 ${reg?`<div><label class="lab">تکرار رمز عبور</label><input id="pw2" type="password" dir="ltr"></div><div><label class="lab">کد دعوت (نام‌کاربری دوست، اختیاری)</label><input id="ref" dir="ltr"></div>`:`<div id="c2" hidden><label class="lab">کد برنامه احراز هویت (۶ رقم)</label><input id="code" inputmode="numeric" dir="ltr"></div>`}
 <button class="btn" id="sub">${reg?'ثبت‌نام':'ورود'}</button>
 <button class="btn g" id="sw">${reg?'حساب دارم؛ ورود':'حساب ندارم؛ ثبت‌نام'}</button>${reg?'':'<button class="mu" id="fg" style="text-align:center">فراموشی رمز عبور</button>'}</div></div>`;
 $('#sw').onclick=()=>authView(reg?'login':'register');
 if(reg){$('#af').onchange=e=>{ava=e.target.files[0];if(ava)$('#ap').innerHTML=`<img src="${URL.createObjectURL(ava)}"><input type="file" accept="image/*" hidden id="af">`}}
 else $('#fg').onclick=forgot;
 $('#sub').onclick=run(async()=>{if(reg){const b={nick:$('#nick').value,username:$('#un').value,phone:$('#ph').value,birth:$('#bd').value,password:$('#pw').value,password2:$('#pw2').value,ref:$('#ref').value};
   const d=await api('register','POST',b);ME=d.user;if(ava){const fd=new FormData();fd.append('file',ava);await fetch('/api/avatar',{method:'POST',headers:{'X-CSRF':CSRF},body:fd})}
   const m=await api('me');ME=m.user;modal(`<h3>کد بازیابی شما</h3><p class="mu sm" style="margin:8px 0">این کد فقط یک بار نمایش داده می‌شود. برای بازیابی رمز عبور لازم است؛ آن را جایی امن ذخیره کنید.</p><div style="font-size:1.6rem;text-align:center;letter-spacing:3px;direction:ltr;margin:14px 0" class="b">${esc(d.recovery)}</div><button class="btn" id="k">ذخیره کردم</button>`,(e,c)=>$('#k',e).onclick=()=>{c();applyTheme(ME.theme);main()})}
  else{const d=await api('login','POST',{id:$('#id').value,password:$('#pw').value,code:($('#code')||{}).value});if(d.need2fa){$('#c2').hidden=false;$('#code').focus();return toast('کد دومرحله‌ای را وارد کنید')}
   ME=d.user;applyTheme(ME.theme);if(ME.prefs.lang)setLang(ME.prefs.lang);backScreen()}})}
function backScreen(){$('#root').innerHTML=`<div class="wel">${duck}<h1>خوش برگشتی!</h1><p class="mu">ما هنوز پیام‌هات رو اینجا داریم.</p><button class="btn" id="go" style="max-width:360px">ورود به پیام‌رسانی</button></div>`;$('#go').onclick=main}
function forgot(){modal(`<h3 style="margin-bottom:10px">بازیابی رمز عبور</h3><div class="pad" style="padding:0"><input id="u" dir="ltr" placeholder="نام کاربری"><input id="c" dir="ltr" placeholder="کد بازیابی XXXXX-XXXXX"><input id="p" type="password" dir="ltr" placeholder="رمز جدید (حداقل ۸ کاراکتر)"><button class="btn" id="s">تغییر رمز</button></div>`,(e,c)=>$('#s',e).onclick=run(async()=>{await api('forgot','POST',{username:$('#u',e).value,code:$('#c',e).value,password:$('#p',e).value});c();toast('رمز تغییر کرد؛ وارد شوید')}))}
async function logout(){await api('logout','POST');clearInterval(TM);ME=null;PAGES.splice(0).forEach(e=>e.remove());applyTheme(null);authView('login')}

/* ---------- main shell ---------- */
async function main(){PAGES.splice(0).forEach(e=>e.remove());TAB='chats';await sync().catch(()=>{});shell();connectSock();clearInterval(TM);TM=setInterval(()=>{if(!document.hidden&&ME)sync().catch(()=>{})},3000);
 if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{})}
function connectSock(){if(SOCK||!ME)return;const s=document.createElement('script');s.src='https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js';s.onload=()=>{try{SOCK=io();SOCK.on('sync',()=>sync().catch(()=>{}))}catch(e){}};document.head.append(s)}
function shell(){$('#root').innerHTML=`<div id="top"></div><div class="view" id="vw" style="position:relative"></div><div class="nav">${[['chats','chat','گفتگوها'],['contacts','users','مخاطبین'],['settings','gear','تنظیمات'],['profile','user','پروفایل']].map(([k,i,l])=>`<button data-a="tab:${k}" class="${TAB===k?'on':''}">${k==='profile'&&ME.avatar?`<img src="/f/${esc(ME.avatar)}" style="width:24px;height:24px;border-radius:50%;object-fit:cover">`:ic(i)}<span>${t(l)}</span>${k==='chats'&&CH.reduce((a,c)=>a+(c.muted?0:c.unread),0)?`<b class="bdg">${nf(CH.reduce((a,c)=>a+(c.muted?0:c.unread),0))}</b>`:''}</button>`).join('')}</div><div id="fabw"></div>`;
 draw()}
function draw(){if(!ME||!$('#vw'))return;({chats:drawChats,contacts:drawContacts,settings:drawSettings,profile:drawProfile})[TAB]()}
function beep(){try{const a=new (window.AudioContext||window.webkitAudioContext)(),o=a.createOscillator(),g=a.createGain();o.connect(g);g.connect(a.destination);o.frequency.value=880;g.gain.value=(ME.prefs.vol??60)/400;o.start();o.stop(a.currentTime+.12)}catch(e){}}
async function sync(){if(!ME)return;const d=await api('chats');let newMsg=null;
 d.chats.forEach(c=>{const p=PREV[c.id];if(p!==undefined&&c.unread>p&&!c.muted&&!(CUR&&CUR.id===c.id&&!document.hidden))newMsg=c;PREV[c.id]=c.unread});
 CH=d.chats;ADS=d.ads;FOLD=d.folders;AIon=d.ai;ME.stars=d.me.stars;ME.premium_until=d.me.premium_until;ME.premium=d.me.premium_until>Date.now()/1000;
 if(newMsg){if(ME.prefs.sound!==false)beep();if(ME.prefs.notif&&document.hidden&&window.Notification&&Notification.permission==='granted')new Notification(newMsg.title,{body:newMsg.last?newMsg.last.text:'',icon:'/icon-192.png'})}
 if(TAB==='chats'&&!PAGES.length&&!document.querySelector('.ov'))drawChats(true);if(CUR)await pollChat()}

/* ---------- chats tab ---------- */
const sortC=l=>l.slice().sort((a,b)=>(b.pinned-a.pinned)||(a.muted-b.muted)||(b.ts-a.ts));
function drawChats(keep){const vw=$('#vw'),y=vw.scrollTop;const selMode=SEL.size>0;
 $('#top').innerHTML=selMode?`<div class="hdr"><button class="ib" data-a="selx">${ic('x')}</button><div class="t">${nf(SEL.size)}</div>${[['mute','mute'],['unmute','bell'],['archive','arch'],['delete','trash'],['pin','pin'],['folder','folder'],['read','check'],['cache','broom']].map(([a,i])=>`<button class="ib" data-a="bulk:${a}">${ic(i)}</button>`).join('')}</div>`
 :`<div class="hdr"><div class="t">NEXO ${ME.premium?SI(17):''}</div><button class="ib" data-a="gsearch">${ic('search')}</button></div>`;
 const vis=CH.filter(c=>!!c.archived===ARCH&&(FILT==null||c.folder===FILT));let h='';
 if(!ARCH){h+=`<div class="chips"><button class="${FILT==null?'on':''}" data-a="filt:all">${t('همه')} <span class="bdg ${FILT==null?'':'m'}">${nf(CH.filter(c=>!c.archived).length)}</span></button>${FOLD.map(f=>`<button class="${FILT===f.id?'on':''}" data-a="filt:${f.id}">${esc(f.name)}</button>`).join('')}</div>`;
  const na=CH.filter(c=>c.archived).length;h+=`<button class="row" data-a="arch">${`<div class="av" style="width:52px;height:52px;background:var(--s3)">${ic('arch')}</div>`}<div class="mid"><div class="l1"><b>${t('بایگانی')}</b></div><div class="l2"><span class="tx">${na?nf(na)+' گفتگو':'خالی'}</span></div></div></button>`;
  if(AIon&&FILT==null)h+=`<button class="row" data-a="ai"><div class="av" style="background:var(--me)">${ic('spark')}</div><div class="mid"><div class="l1"><b>NEXO AI</b><span class="tag">AI</span></div><div class="l2"><span class="tx">دستیار هوشمند NEXO</span></div></div></button>`;
  ADS.forEach(a=>h+=`<button class="row" data-a="ad:${a.id}" data-link="${esc(a.link||'')}"><div class="av" style="background:#ffb15e44;color:#ffb15e">${ic('mega')}</div><div class="mid"><div class="l1"><b>${esc(a.title)}</b><span class="tag ad">تبلیغات</span></div><div class="l2"><span class="tx">${esc(a.body)}</span></div></div></button>`)}
 else h+=`<div class="sec">${t('بایگانی')}</div>`;
 sortC(vis).forEach(c=>{const o=c.peer||{};h+=`<button class="row ${SEL.has(c.id)?'sel':''}" data-a="chat:${c.id}" data-lp="${c.id}"><div class="avw">${av(c)}${o.online?'<i class="dot"></i>':''}${SEL.has(c.id)?`<span class="chk">${ic('check')}</span>`:''}</div>
  <div class="mid"><div class="l1">${c.type==='channel'?ic('mega','s'):c.type==='group'?ic('users','s'):''}<b>${esc(c.title||'؟')}</b>${prb(o)}${c.muted?ic('mute','s'):''}</div>
  <div class="l2"><span class="tx">${c.last?(c.type!=='private'&&c.last.sender&&!c.last.mine?'<b class="pf">'+esc(c.last.sender)+':</b> ':c.last.mine?'<b class="pf">شما:</b> ':'')+esc(c.last.text):'<i>بدون پیام</i>'}</span></div></div>
  <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px"><span class="mu sm">${c.last?fT(c.ts):''}</span><span style="display:flex;gap:6px;align-items:center">${c.pinned?`<span class="mu" title="${t('پین شده')}">${ic('pin','s')}</span>`:''}${c.unread?`<b class="bdg ${c.muted?'m':''}">${nf(c.unread)}</b>`:''}</span></div></button>`});
 if(!vis.length&&ARCH)h+='<p class="mu" style="text-align:center;padding:40px">گفتگوی بایگانی‌شده‌ای نیست</p>';
 vw.innerHTML=h;vw.scrollTop=keep?y:0;$('#fabw').innerHTML=ARCH?'':`<button class="fab" data-a="fab">${ic('edit','l')}</button>`;
 const tot=CH.reduce((a,c)=>a+(c.muted?0:c.unread),0),nb=$$('.nav button')[0];if(nb){let b=nb.querySelector('.bdg');if(!tot){b&&b.remove()}else{if(!b){b=document.createElement('b');b.className='bdg';nb.append(b)}b.textContent=nf(tot)}}
 ADS.forEach(a=>{if(!ADV.has(a.id)){ADV.add(a.id);api('ads/'+a.id+'/views','POST').catch(()=>{})}})}
const ADV=new Set();
let lpT=null,lpFired=false;
document.addEventListener('pointerdown',e=>{const r=e.target.closest('[data-lp]');if(!r)return;lpFired=false;lpT=setTimeout(()=>{lpFired=true;setTimeout(()=>lpFired=false,600);navigator.vibrate&&navigator.vibrate(15);SEL.add(+r.dataset.lp);drawChats(true)},450)});
['pointerup','pointercancel','pointermove','scroll'].forEach(ev=>document.addEventListener(ev,()=>clearTimeout(lpT),true));
const bulk=run(async a=>{const ids=[...SEL];const cs=CH.filter(c=>SEL.has(c.id));let act=a;
 if(a==='mute')act=cs.every(c=>c.muted)?'unmute':'mute';if(a==='unmute')act='unmute';if(a==='pin')act=cs.every(c=>c.pinned)?'unpin':'pin';if(a==='archive')act=ARCH?'unarchive':'archive';
 if(a==='delete'&&!await ask('گفتگوهای انتخاب‌شده حذف شوند؟','حذف'))return;
 if(a==='cache'){await clearCache();SEL.clear();return drawChats(true)}
 if(a==='folder'){const fs=FOLD.map(f=>({t:f.name,i:'folder',f:async()=>{await api('chats/bulk','POST',{ids,action:'folder',folder:f.id});SEL.clear();await sync()}}));return sheet([...fs,{t:'بدون پوشه',f:async()=>{await api('chats/bulk','POST',{ids,action:'folder',folder:null});SEL.clear();await sync()}},{t:'مدیریت پوشه‌ها',i:'gear',f:foldersPage}])}
 await api('chats/bulk','POST',{ids,action:act});SEL.clear();await sync();drawChats(true)});
async function clearCache(){if(window.caches)await caches.delete('nexo-files');PREV={};toast('حافظه کش پاک شد')}
function fab(){sheet([{t:t('گفتگوی جدید'),i:'chat',f:()=>pickUser(u=>startChat(u.id))},{t:'جستجوی کاربران',i:'search',f:globalSearch},{t:t('ایجاد گروه'),i:'users',f:()=>newGroup('group')},{t:t('ایجاد کانال'),i:'mega',f:()=>newGroup('channel')},{t:t('افزودن مخاطب'),i:'plus',f:addContact}])}
async function startChat(uid){const d=await api('chats/private','POST',{user_id:uid});await sync();openChat(d.id)}
function pickUser(cb){api('contacts').then(l=>{if(!l.length)return toast('مخاطبی ندارید؛ ابتدا مخاطب اضافه کنید');const ov=sheet(l.map(u=>({t:u.nick+' @'+u.username,i:'user',f:()=>cb(u)})),'<div class="sec">انتخاب مخاطب</div>')})}
const addContact=async()=>{const v=await prompt2('نام کاربری یا شماره تلفن');if(!v)return;await api('contacts','POST',{q:v});toast('مخاطب اضافه شد');if(TAB==='contacts')drawContacts()};
function globalSearch(){const p=page('جستجو',`<div class="pad"><input id="gq" placeholder="${t('جستجو...')}" autofocus></div><div id="gr"></div>`,e=>{let T;$('#gq',e).oninput=ev=>{clearTimeout(T);T=setTimeout(run(async()=>{const q=ev.target.value.trim();if(q.length<2)return $('#gr',e).innerHTML='';const d=await api('search?q='+encodeURIComponent(q));
  $('#gr',e).innerHTML=d.users.map(u=>`<button class="row" data-a="user:${u.id}">${av(u,46)}<div class="mid"><b>${esc(u.nick)}</b> ${prb(u)}<div class="mu sm">@${esc(u.username)}</div></div></button>`).join('')+d.chats.map(c=>`<button class="row" data-a="joinc:${c.id}">${av(c,46)}<div class="mid"><b>${esc(c.title)}</b><div class="mu sm">${c.type==='channel'?'کانال':'گروه'} عمومی @${esc(c.username||'')}</div></div></button>`).join('')||'<p class="mu" style="text-align:center;padding:30px">نتیجه‌ای نیست</p>'}),300)}})}
function newGroup(type){const ch=type==='channel';let sel=new Set();api('contacts').then(cs=>page(ch?t('ایجاد کانال'):t('ایجاد گروه'),`<div class="pad"><input id="gt" placeholder="عنوان"><input id="ga" placeholder="توضیحات (اختیاری)"><label class="ck"><input type="checkbox" id="gp"><span>عمومی (قابل جستجو با نام کاربری)</span></label><input id="gu" dir="ltr" placeholder="username عمومی" hidden>${ch?'':'<div class="sec" style="padding:0">اعضا</div>'+cs.map(u=>`<label class="ck"><input type="checkbox" value="${u.id}" class="mm">${esc(u.nick)}</label>`).join('')}<button class="btn" id="mk">ایجاد</button></div>`,e=>{$('#gp',e).onchange=ev=>$('#gu',e).hidden=!ev.target.checked;
  $('#mk',e).onclick=run(async()=>{const d=await api('chats/group','POST',{type,title:$('#gt',e).value,about:$('#ga',e).value,public:$('#gp',e).checked,username:$('#gu',e).value,members:$$('.mm:checked',e).map(i=>+i.value)});back();await sync();later(()=>openChat(d.id))})}))}

/* ---------- contacts / profile / settings tabs ---------- */
async function drawContacts(){$('#top').innerHTML=`<div class="hdr"><div class="t">${t('مخاطبین')}</div><button class="ib" data-a="addc">${ic('plus')}</button></div>`;$('#fabw').innerHTML='';
 const vw=$('#vw');vw.innerHTML=`<div class="pad" style="padding-bottom:0"><input id="cq" placeholder="${t('جستجو...')}"></div><div id="cl"></div>`;const l=await api('contacts');
 const rend=f=>$('#cl').innerHTML=l.filter(u=>!f||(u.nick+u.username).toLowerCase().includes(f.toLowerCase())).map(u=>`<button class="row" data-a="user:${u.id}">${`<div class="avw">${av(u,48)}${u.online?'<i class="dot"></i>':''}</div>`}<div class="mid"><div class="l1"><b>${esc(u.nick)}</b>${prb(u)}</div><div class="l2"><span class="tx">${seen(u)}</span></div></div></button>`).join('')||'<p class="mu" style="text-align:center;padding:40px">مخاطبی نیست؛ با + اضافه کنید</p>';
 rend('');$('#cq').oninput=e=>rend(e.target.value)}
function posts(list){return list.map(p=>`<div class="post"><div class="mu sm">${fDT(p.created)}${p.mine?` · <a data-a="parch:${p.id}">${p.archived?'خروج از بایگانی':'بایگانی'}</a> · <a data-a="pdel:${p.id}" style="color:var(--er)">حذف</a>`:''}</div>${esc(p.text)}${p.file?`<img src="/f/${esc(p.file)}">`:''}</div>`).join('')||'<p class="mu" style="text-align:center;padding:30px">هنوز پستی نیست</p>'}
const pickFile=(accept,cb)=>{const i=document.createElement('input');i.type='file';i.accept=accept;i.onchange=()=>i.files[0]&&cb(i.files[0]);i.click()};
function up(f,hint,onp){return new Promise((res,rej)=>{const x=new XMLHttpRequest(),fd=new FormData();fd.append('file',f);if(hint)fd.append('kind',hint);x.open('POST','/api/upload');x.setRequestHeader('X-CSRF',CSRF);
 x.upload.onprogress=e=>e.lengthComputable&&onp&&onp(Math.round(e.loaded/e.total*100));x.onload=()=>{let d={};try{d=JSON.parse(x.responseText)}catch(e){}x.status<300?res(d):rej(new Error(d.error||'آپلود ناموفق'))};x.onerror=()=>rej(new Error('خطای شبکه'));x.send(fd)})}
const LV=()=>({user:0,mod:1,admin:2,owner:3}[ME.role]||0);
'''

CLIENT += r'''
/* ---------- settings sub pages ---------- */
const swh=(k,l,v)=>`<label class="set"><span class="mid">${l}</span><span class="sw"><input type="checkbox" data-k="${k}" ${v?'checked':''}><i></i></span></label>`;
const sel=(k,l,v,o)=>`<div style="padding:8px 16px"><label class="lab">${l}</label><select data-k="${k}">${o.map(([a,b])=>`<option value="${a}" ${v==a?'selected':''}>${b}</option>`).join('')}</select></div>`;
function prefPage(title,html,keys){return page(title,html,e=>$$('[data-k]',e).forEach(i=>i.onchange=run(async()=>{const k=i.dataset.k;let v=i.type==='checkbox'?i.checked:i.type==='range'?+i.value:i.value;if(v==='true')v=true;if(v==='false')v=false;await savePrefs({[k]:v});toast('ذخیره شد')})))}
function privPost(k,v){return api('me','POST',{privacy:{[k]:v}}).then(d=>{ME=d.user;toast('ذخیره شد')})}
const SET={
account(){page('حساب کاربری',`<div class="pad">${[['nick','نام مستعار',ME.nick],['username','Username',ME.username],['phone','شماره تلفن',ME.phone||''],['bio','بیوگرافی',ME.bio||''],['birth','تاریخ تولد',ME.birth||'']].map(([k,l,v])=>`<div><label class="lab">${l}</label><input id="f_${k}" ${k==='birth'?'type="date"':''} value="${esc(v)}" ${k==='phone'||k==='username'?'dir="ltr"':''}></div>`).join('')}<button class="btn" id="sv">${t('ذخیره')}</button><hr style="border:0;border-top:1px solid var(--ln);width:100%"><button class="btn g" id="cpw">تغییر رمز عبور</button></div>`,e=>{
  $('#sv',e).onclick=run(async()=>{const b={};['nick','username','phone','bio','birth'].forEach(k=>b[k]=$('#f_'+k,e).value);const d=await api('me','POST',b);ME=d.user;toast('ذخیره شد')});
  $('#cpw',e).onclick=()=>modal(`<div class="pad" style="padding:0"><input id="o" type="password" placeholder="رمز فعلی" dir="ltr"><input id="n" type="password" placeholder="رمز جدید" dir="ltr"><button class="btn" id="s">تغییر</button></div>`,(m,c)=>$('#s',m).onclick=run(async()=>{await api('password','POST',{old:$('#o',m).value,new:$('#n',m).value});c();toast('رمز تغییر کرد؛ سایر نشست‌ها بسته شدند')}))})},
chat(){const p=ME.prefs;prefPage('تنظیمات گفتگو',swh('enter','ارسال پیام با Enter',p.enter===true)+swh('wall','پس‌زمینه رنگی گفتگو',p.wall!==false)+sel('bubble','سبک حباب پیام‌ها',p.bubble||'gradient',[['gradient','گرادیان'],['flat','ساده']]))},
privacy(){const pv=ME.privacy||{};page('حریم خصوصی',`${sel('lastseen','آخرین بازدید و آنلاین',pv.lastseen||'all',[['all','همه'],['nobody','هیچ‌کس']])}${sel('phone','شماره تلفن من',pv.phone||'show',[['show','نمایش'],['hide','مخفی']])}${sel('pm','دریافت پیام از',pv.pm||'all',[['all','همه'],['contacts','فقط مخاطبین']])}${sel('receipts','رسید خواندن پیام (Premium)',pv.receipts||'on',[['on','نمایش'],['off','مخفی']])}`,e=>$$('select',e).forEach(s=>s.onchange=run(()=>privPost(s.dataset.k,s.value))))},
security(){page('امنیت',`<div class="card"><div class="kv"><b>تأیید دومرحله‌ای (TOTP)</b><small>${ME.has2fa?'فعال':'غیرفعال'}</small></div><button class="set" id="tf"><span class="mid">${ME.has2fa?'غیرفعال‌سازی':'فعال‌سازی'}</span></button><button class="set" id="rc"><span class="mid">دریافت کد بازیابی جدید</span></button><button class="set" data-a="set:devices"><span class="mid">نشست‌ها و دستگاه‌ها</span></button></div>`,e=>{
  $('#tf',e).onclick=run(async()=>{if(ME.has2fa){const p=await prompt2('برای غیرفعال‌سازی رمز عبور را وارد کنید','','password');if(!p)return;await api('2fa/enable','POST',{off:1,password:p});ME.has2fa=false;toast('غیرفعال شد');back()}
   else{const d=await api('2fa/setup');modal(`<h3>فعال‌سازی</h3><p class="mu sm" style="margin:8px 0">این کلید را در برنامه احراز هویت (Google Authenticator یا Aegis) به‌صورت دستی وارد کنید:</p><div dir="ltr" class="b" style="word-break:break-all;margin:10px 0">${esc(d.secret)}</div><input id="c" inputmode="numeric" placeholder="کد ۶ رقمی" dir="ltr"><button class="btn" id="s" style="margin-top:12px">تأیید و فعال‌سازی</button>`,(m,c)=>$('#s',m).onclick=run(async()=>{await api('2fa/enable','POST',{code:$('#c',m).value});c();ME.has2fa=true;toast('فعال شد');back()}))}});
  $('#rc',e).onclick=run(async()=>{const p=await prompt2('رمز عبور','','password');if(!p)return;const d=await api('recovery','POST',{password:p});modal(`<h3>کد بازیابی جدید</h3><div style="font-size:1.5rem;text-align:center;direction:ltr;margin:16px 0" class="b">${esc(d.recovery)}</div><p class="mu sm">کد قبلی باطل شد.</p>`)})})},
notif(){const p=ME.prefs;page('اعلان‌ها',swh('notif','اعلان مرورگر (وقتی برنامه در پس‌زمینه است)',p.notif)+`<div class="pad"><p class="mu sm">اجازه نمایش اعلان باید توسط مرورگر تأیید شود.</p></div>`,e=>$('[data-k=notif]',e).onchange=run(async ev=>{if(ev.target.checked&&window.Notification&&Notification.permission!=='granted'){const r=await Notification.requestPermission();if(r!=='granted'){ev.target.checked=false;return toast('اجازه داده نشد')}}await savePrefs({notif:ev.target.checked});toast('ذخیره شد')}))},
sound(){const p=ME.prefs;prefPage('صدا',swh('sound','صدای پیام جدید',p.sound!==false)+`<div style="padding:8px 16px"><label class="lab">بلندی صدا</label><input type="range" min="0" max="100" data-k="vol" value="${p.vol??60}"></div><div class="pad"><button class="btn g" onclick="beep()">آزمایش صدا</button></div>`)},
data(){const p=ME.prefs;prefPage('داده‌ها و ذخیره‌سازی',swh('autoimg','دانلود خودکار تصاویر',p.autoimg!==false)+swh('autovid','بارگذاری خودکار ویدیو',p.autovid===true)+`<div class="pad"><div class="mu" id="us">در حال محاسبه...</div><button class="btn g" id="cc">${ic('broom')} پاک‌سازی حافظه کش</button></div>`);
 if(navigator.storage&&navigator.storage.estimate)navigator.storage.estimate().then(s=>{const e=$('#us');if(e)e.textContent='حجم استفاده‌شده: '+sz(s.usage||0)});$('#cc')&&($('#cc').onclick=run(async()=>{await clearCache();SET.data_=0}))},
folders(){foldersPage()},
devices(){const e=page('دستگاه‌ها و نشست‌ها','<div id="sl"></div>',async e=>{const l=await api('sessions');$('#sl',e).innerHTML=`<div class="card">${l.map(s=>`<div class="set"><span class="mid">${s.current?'<b>این دستگاه</b>':'دستگاه دیگر'}<small dir="ltr" style="text-align:start">${esc(s.device.slice(0,70))}</small><small>IP ${esc(s.ip)} · ${fDT(s.last)}</small></span>${s.current?'':`<button class="tag" style="color:var(--er)" data-sid="${s.id}">پایان</button>`}</div>`).join('')}</div><div class="pad"><button class="btn d" id="ko">خروج از همه دستگاه‌های دیگر</button></div>`;
  $$('[data-sid]',e).forEach(b=>b.onclick=run(async()=>{await api('sessions/'+b.dataset.sid,'DELETE');b.closest('.set').remove()}));$('#ko',e).onclick=run(async()=>{await api('sessions/others','DELETE');toast('انجام شد');back()})})},
lang(){page('زبان',`<div class="card"><button class="set" data-l="fa"><span class="mid">فارسی</span>${LANG==='fa'?ic('check'):''}</button><button class="set" data-l="en"><span class="mid">English</span>${LANG==='en'?ic('check'):''}</button></div>`,e=>$$('[data-l]',e).forEach(b=>b.onclick=run(async()=>{await savePrefs({lang:b.dataset.l});setLang(b.dataset.l);back();shell()})))},
theme(){const p=ME.prefs,th=ME.theme||{};page('پوسته و تم',`${sel('mode','حالت نمایش',th.mode||p.mode||'dark',[['dark','تاریک'],['light','روشن']])}<div style="padding:8px 16px"><label class="lab">رنگ اصلی</label><div style="display:flex;gap:10px;flex-wrap:wrap">${['#5b8cff','#8b5cf6','#ec4899','#ef4444','#f97316','#10b981','#14b8a6','#eab308'].map(c=>`<button data-c="${c}" style="width:38px;height:38px;border-radius:50%;background:${c};border:3px solid ${(th.accent||p.accent)===c?'var(--tx)':'transparent'}"></button>`).join('')}</div></div>
 <div class="card"><button class="set" id="aid"><span class="si" style="background:var(--me)">${ic('spark')}</span><span class="mid">AI UI Designer <span class="tag">Premium</span><small>با دستور متنی ظاهر برنامه را تغییر دهید</small></span></button><button class="set" id="shr"><span class="mid">اشتراک / وارد کردن تم</span></button><button class="set d" id="rst"><span class="mid">بازگشت به ظاهر اصلی NEXO</span></button></div>`,e=>{
  const setTh=run(async o=>{const nt={...(ME.theme||{}),...o};const d=await api('theme','POST',{theme:nt});ME.theme=d.theme;applyTheme(ME.theme);toast('اعمال شد')});
  $('[data-k=mode]',e)||0;$$('select',e).forEach(s=>s.onchange=()=>setTh({mode:s.value}));$$('[data-c]',e).forEach(b=>b.onclick=()=>setTh({accent:b.dataset.c,accent2:b.dataset.c}));
  $('#aid',e).onclick=designer;$('#rst',e).onclick=run(async()=>{await api('theme','POST',{theme:null});await savePrefs({mode:'dark',accent:''});ME.theme=null;applyTheme(null);toast('ظاهر اصلی بازگردانی شد')});
  $('#shr',e).onclick=()=>modal(`<label class="lab">کد تم (قابل اشتراک)</label><textarea id="tc" rows="4" dir="ltr">${esc(btoa(unescape(encodeURIComponent(JSON.stringify(ME.theme||{})))))}</textarea><button class="btn" id="im" style="margin-top:12px">وارد کردن این کد</button>`,(m,c)=>$('#im',m).onclick=run(async()=>{const o=JSON.parse(decodeURIComponent(escape(atob($('#tc',m).value.trim()))));c();setTh(o)}))})},
font(){const p=ME.prefs;prefPage('فونت',`<div style="padding:8px 16px"><label class="lab">اندازه فونت</label><input type="range" min="0.85" max="1.3" step="0.05" data-k="scale" value="${p.scale||1}"></div>${sel('font','نوع فونت',p.font||'sys',[['sys','پیش‌فرض'],['serif','سریف'],['mono','تک‌فاصله']])}`)},
ai(){aiPage()},premium(){premiumPage()},wallet(){walletPage()},
ads(){page('تبلیغات',`<div class="pad"><p class="mu sm">تبلیغ پس از تأیید مدیر در بالای فهرست گفتگوها نمایش داده می‌شود. هزینه ثبت از Stars کسر و در صورت رد، بازگردانده می‌شود.</p><input id="at" maxlength="60" placeholder="عنوان"><input id="ab" maxlength="140" placeholder="متن کوتاه"><input id="al" dir="ltr" placeholder="https:// لینک (اختیاری)"><button class="btn" id="as">ثبت درخواست تبلیغ</button></div><div class="sec">تبلیغات من</div><div id="ml"></div>`,e=>{const ld=async()=>{const l=await api('ads/mine');$('#ml',e).innerHTML=l.map(a=>`<div class="kv"><b>${esc(a.title)}</b> <span class="tag">${{pending:'در انتظار',approved:'فعال',rejected:'رد شده'}[a.status]}</span><small>${nf(a.views)} نمایش · ${nf(a.clicks)} کلیک</small></div>`).join('')||'<p class="mu" style="text-align:center;padding:20px">موردی نیست</p>'};ld();$('#as',e).onclick=run(async()=>{await api('ads','POST',{title:$('#at',e).value,body:$('#ab',e).value,link:$('#al',e).value});toast('ثبت شد؛ منتظر تأیید مدیر');ld()})})},
blocks(){page('بلاک‌شدگان','<div id="bl"></div>',async e=>{const ld=async()=>{const l=await api('blocks');$('#bl',e).innerHTML=l.map(u=>`<div class="row">${av(u,44)}<div class="mid"><b>${esc(u.nick)}</b></div><button class="tag" data-u="${u.id}">رفع مسدودی</button></div>`).join('')||'<p class="mu" style="text-align:center;padding:30px">لیست خالی است</p>';$$('[data-u]',e).forEach(b=>b.onclick=run(async()=>{await api('block/'+b.dataset.u,'DELETE');ld()}))};ld()})},
invite(){page('دعوت دوستان',`<div class="pad"><p>دوستانتان هنگام ثبت‌نام نام‌کاربری شما را در «کد دعوت» وارد کنند. برای هر دعوت موفق، هر دو نفر ۵۰ Stars می‌گیرید.</p><div class="card" style="margin:0;padding:18px;text-align:center" dir="ltr"><b style="font-size:1.4rem">${esc(ME.username)}</b></div><button class="btn" id="cp">کپی کد دعوت</button></div>`,e=>$('#cp',e).onclick=()=>{navigator.clipboard&&navigator.clipboard.writeText(ME.username);toast('کپی شد')})},
rules(){rulesView()},about(){page('درباره NEXO',`<div class="pad" style="text-align:center"><div class="logo" style="font-size:2.4rem">NEXO</div><p>پیام‌رسان مستقل فارسی با هوش مصنوعی اختصاصی</p><p class="mu sm">ارتباط رمزنگاری‌شده سرتاسری (E2EE) در این نسخه پیاده‌سازی نشده است؛ پیام‌ها روی سرور شما ذخیره می‌شوند. برای ارتباط امن حتماً از HTTPS استفاده کنید.</p></div>`)},
admin(){location.href='/admin'}};
function foldersPage(){page('پوشه‌های گفتگو','<div id="fl"></div><div class="pad"><button class="btn" id="fa">افزودن پوشه</button><p class="mu sm">سقف: ۳ پوشه (Premium: ۱۰). با نگه‌داشتن روی گفتگو و دکمه پوشه، گفتگو را به پوشه اضافه کنید.</p></div>',e=>{const ld=async()=>{FOLD=await api('folders');$('#fl',e).innerHTML=FOLD.map(f=>`<div class="set"><span class="si" style="background:#2d9cdb">${ic('folder')}</span><span class="mid">${esc(f.name)}</span><button data-f="${f.id}" class="ib">${ic('trash')}</button></div>`).join('');$$('[data-f]',e).forEach(b=>b.onclick=run(async()=>{await api('folders/'+b.dataset.f,'DELETE');ld()}))};ld();$('#fa',e).onclick=run(async()=>{const n=await prompt2('نام پوشه');if(!n)return;await api('folders','POST',{name:n});ld()})})}
async function rulesView(){const d=await api('rules');page('قوانین NEXO',`<div class="pad" style="white-space:pre-wrap;line-height:2">${esc(d.rules)}</div>`)}
/* wallet / premium */
'''

CLIENT += r'''
/* ---------- Telegram-style: Stars / Premium / Gifts / Profile ---------- */
const SI=(s=15)=>`<svg class="sx" style="width:${s}px;height:${s}px" viewBox="0 0 24 24"><path d="M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2 6.3 20.3l1.2-6.4L2.8 9.5l6.4-.8z" fill="url(#gst)" stroke="url(#gst)" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
const stp=(cx,cy,R,r)=>[...Array(10)].map((_,i)=>{const a=-Math.PI/2+i*Math.PI/5,d=i%2?r:R;return(cx+d*Math.cos(a)).toFixed(1)+','+(cy+d*Math.sin(a)).toFixed(1)}).join(' ');
function sparks(n,cls=''){let s=7,o='';const rnd=()=>(s=(s*9301+49297)%233280)/233280;for(let i=0;i<n;i++){const a=rnd()*6.283,rd=.36+rnd()*.14+(i%3)*.03,x=50+Math.cos(a)*rd*100,y=50+Math.sin(a)*rd*100;o+=`<i class="sp ${cls}" style="left:${x}%;top:${y}%;animation-delay:${(rnd()*2.6).toFixed(2)}s"></i>`}return o}
const premStar=()=>`<svg class="pst" viewBox="0 0 200 200"><defs><linearGradient id="pg1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#a28bff"/><stop offset=".5" stop-color="#6f8fff"/><stop offset="1" stop-color="#3fb0f7"/></linearGradient></defs><g transform="rotate(-10 100 100)"><polygon points="${stp(100,112,78,38)}" fill="#2a56c4" stroke="#2a56c4" stroke-width="16" stroke-linejoin="round"/><polygon points="${stp(100,100,78,38)}" fill="url(#pg1)" stroke="url(#pg1)" stroke-width="16" stroke-linejoin="round"/></g></svg>`;
const goldStar=()=>`<svg class="pst" viewBox="0 0 200 200"><defs><linearGradient id="gg1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffe27a"/><stop offset="1" stop-color="#f59a1b"/></linearGradient></defs><g transform="rotate(-8 100 100)"><polygon points="${stp(100,110,78,40)}" fill="#c9780f" stroke="#c9780f" stroke-width="16" stroke-linejoin="round"/><polygon points="${stp(100,100,78,40)}" fill="url(#gg1)" stroke="url(#gg1)" stroke-width="16" stroke-linejoin="round"/></g></svg>`;
const PN={m1:'۱ ماه',m3:'۳ ماه',y1:'۱ سال'},PN2={m1:'یک ماه',m3:'سه ماه',y1:'یک سال'};
const disc=(P,k)=>{const b=P.m1?P.m1[1]/P.m1[0]:0;return b?Math.max(0,Math.round((1-(P[k][1]/P[k][0])/b)*100)):0};
const fDTF=ts=>new Date(ts*1000).toLocaleString(loc(),{dateStyle:'medium',timeStyle:'short'});
const GPAL=[['#7a5316','#d9992f'],['#215c8a','#47a3dc'],['#7d2c66','#d8589d'],['#25664a','#43b97f'],['#503783','#9468e3'],['#85382c','#e07a5c']];
const gpat=n=>{const P=[[8,10],[78,8],[16,72],[82,70],[46,4],[4,44],[90,42],[50,86],[30,30],[66,28]];return P.slice(0,n).map(([x,y],i)=>`<span class="gpat" style="left:${x}%;top:${y}%;font-size:${10+i%3*3}px">${i%2?'✦':'✧'}</span>`).join('')};
const FEATS=[['x2','#f5922f','محدودیت‌های بیشتر','تا ۱۰ پوشه، ۱۵ پیام سنجاق‌شده، آپلود تا ۶۰ مگابایت و موارد بیشتر.'],
['spark','#7a6cf0','هوش مصنوعی قدرتمندتر','انتخاب مدل‌های قوی‌تر و سقف مصرف بیشتر در NEXO AI.'],
['pal','#e84393','AI UI Designer','ظاهر برنامه را فقط با یک دستور متنی طراحی کنید.'],
['smile','#9b59f5','استیکرهای متحرک','استیکرهای انحصاری و متحرک برای همهٔ گفتگوها.'],
['heart','#ef5a6f','واکنش‌های ویژه','واکنش‌های متحرک 🔥 💎 🚀 👑 برای پیام‌ها.'],
['gift','#25b6a2','هدایای کمیاب','ارسال هدایای مخصوص Premium و هدایای کمیاب.'],
['bolt','#f5a623','پاداش روزانهٔ بیشتر','۲٫۵ برابر استارز در پاداش روزانه.'],
['eye','#4a8cf0','رسید خواندن','رسید خواندن پیام‌هایتان را مخفی کنید.'],
['check','#2f9bef','نشان Premium','نشان ستاره کنار نام شما در همه‌جا.']];
function bsheet(html,mount){const ov=document.createElement('div');ov.className='ov';const sh=document.createElement('div');sh.className='sheet tall';sh.innerHTML='<i class="grab"></i>'+html;ov.append(sh);ov.onclick=e=>{if(e.target===ov)ov.remove()};document.body.append(ov);const close=()=>ov.remove();$$('[data-x]',sh).forEach(b=>b.onclick=close);mount&&mount(sh,close);return ov}
function noStars(){modal(`<div style="text-align:center"><div style="font-size:2.4rem">${SI(44)}</div><h3 style="margin:8px 0">موجودی استارز کافی نیست</h3><p class="mu sm" style="margin-bottom:14px">برای ادامه، استارز بیشتری دریافت کنید.</p><button class="btn" id="y">دریافت استارز</button></div>`,(m,c)=>$('#y',m).onclick=()=>{c();buyStars()})}
const lowStars=er=>(er.message||'').includes('کافی نیست');

/* ---- gifts ---- */
function giftGrid(list,mine){if(!list.length)return`<div class="mu" style="text-align:center;padding:40px 20px"><div style="font-size:3rem">🎁</div><p style="margin-top:8px">${mine?'هنوز هدیه‌ای دریافت نکرده‌اید':'هنوز هدیه‌ای دریافت نکرده'}</p></div>`;
 return`<div class="ggrid">${list.map((g,i)=>`<button class="gcard ${g.rare?'rare':''}" style="--g1:${GPAL[g.gid%6][0]};--g2:${GPAL[g.gid%6][1]}" data-gi="${i}">${g.rare?gpat(8):''}<span class="gfrom">${av({id:g.from_id,nick:g.from,avatar:g.from_avatar},20)}</span>${g.rare?`<span class="gno">#${nf(g.id)}</span>`:''}<span class="gem">${g.emoji}</span></button>`).join('')}</div>`}
const bindGifts=(root,list)=>$$('[data-gi]',root).forEach(b=>b.onclick=()=>giftDetail(list[+b.dataset.gi]));
function giftDetail(g){const p=GPAL[g.gid%6];modal(`<div class="gdet"><div class="hero ${g.rare?'rare':''}" style="--g1:${p[0]};--g2:${p[1]}">${g.rare?gpat(10):''}<span class="gem">${g.emoji}</span></div><h3>${esc(g.name)}</h3>
 <div style="margin-top:10px"><div class="kvr"><span class="mu">از طرف</span><span class="lp">${av({id:g.from_id,nick:g.from,avatar:g.from_avatar},24)} ${esc(g.from)}</span></div><div class="kvr"><span class="mu">تاریخ</span><span>${fDTF(g.created)}</span></div><div class="kvr"><span class="mu">ارزش</span><span class="lp" style="color:var(--tx)">${SI(15)} ${nf(g.price)}</span></div></div>${g.note?`<p class="mu" style="margin:8px 0">«${esc(g.note)}»</p>`:''}<button class="btn" id="ok" style="margin-top:14px">تأیید</button></div>`,(m,c)=>$('#ok',m).onclick=c)}
function giftConfirm(g,u,ok){const p=GPAL[g.id%6];modal(`<div class="gdet"><div class="hero ${g.rare?'rare':''}" style="--g1:${p[0]};--g2:${p[1]}">${g.rare?gpat(10):''}<span class="gem">${g.emoji}</span></div><h3>${esc(g.name)}</h3><div class="mu sm" style="margin:2px 0 12px">هدیه برای ${esc(u.nick)}</div><input id="nt" maxlength="100" placeholder="یادداشت (اختیاری)" style="margin-bottom:14px"><button class="btn" id="go">ارسال هدیه · ${nf(g.price)} ${SI(15)}</button></div>`,
 (m,c)=>$('#go',m).onclick=run(async()=>{try{await api('gifts/send','POST',{gift:g.id,to:u.id,note:$('#nt',m).value})}catch(er){if(lowStars(er)){c();return noStars()}throw er}c();toast('هدیه ارسال شد 🎁');sync();ok&&ok()}))}
async function giftShop(u,done){const w=await api('wallet'),P=w.plans;
 bsheet(`<div class="shh"><button class="ib" data-x>${ic('x')}</button><div class="t">ارسال هدیه</div><span class="gwallet">${SI(15)} ${nf(w.stars)}</span></div>
 <div class="phero" style="padding-top:2px"><div class="stage" style="width:190px;height:150px">${sparks(22)}<span style="z-index:1;display:grid">${av(u,84)}</span></div><h2>اهدای پرمیوم</h2><p>با NEXO Premium، به <b class="hl">${esc(u.nick)}</b> امکان دسترسی به قابلیت‌های انحصاری را بدهید. <a class="hl" id="sf">مشاهده قابلیت‌ها ‹</a></p></div>
 <div class="pcs" style="margin:10px 0 6px">${Object.keys(P).map(k=>{const d=disc(P,k);return`<button class="pc" data-pl="${k}">${d>0?`<span class="rib">-${nf(d)}٪</span>`:''}<b>${PN[k]||k}</b><span class="mu sm">پرمیوم</span><span class="pp">${SI(12)} ${nf(P[k][1])}</span></button>`}).join('')}</div>
 <div class="sec">هدیه‌ها</div><div class="ggrid" style="padding-bottom:20px">${w.gifts.map(g=>`<button class="gcard shop ${g.premium_only&&!ME.premium?'lock':''}" data-g="${g.id}">${g.rare?'<span class="gtag">کمیاب</span>':''}<span class="gem">${g.emoji}</span><span class="gpr">${SI(13)} ${nf(g.price)}</span></button>`).join('')}</div>`,(sh,close)=>{
  $('#sf',sh).onclick=()=>{close();premiumPage()};
  $$('[data-pl]',sh).forEach(b=>b.onclick=run(async()=>{const k=b.dataset.pl;if(!await ask(`اهدای NEXO Premium ${PN2[k]||k} به ${u.nick} با ${nf(P[k][1])} استارز؟`,'ارسال هدیه'))return;
   try{await api('premium/buy','POST',{plan:k,to:u.id})}catch(er){if(lowStars(er)){close();return noStars()}throw er}close();toast('Premium هدیه داده شد ✨');sync();done&&done()}));
  $$('[data-g]',sh).forEach(b=>b.onclick=()=>giftConfirm(w.gifts.find(g=>g.id===+b.dataset.g),u,()=>{close();done&&done()}))})}

/* ---- stars ---- */
const TXI=r=>/هدیه/.test(r)?['gift','#e8637a']:/Premium|پرمیوم/.test(r)?['star','#7a6cf0']:/پاداش|دعوت/.test(r)?['bolt','#2fb67a']:['star','#f5a623'];
async function walletPage(){const w=await api('wallet');let f='all';
 page('استارز',`<div class="shero"><div class="stage">${sparks(18,'g')}${goldStar()}</div><div class="mu sm">موجودی شما</div><div class="bal">${nf(w.stars)}${SI(30)}</div><button class="btn" id="by">خرید استارز بیشتر</button></div>
 <div class="card"><button class="set" id="dl" ${w.daily_ready?'':'style="opacity:.55"'}><span class="si" style="background:#2fb67a">${ic('bolt')}</span><span class="mid">${w.daily_ready?'دریافت پاداش روزانه':'پاداش امروز دریافت شد'}<small>هر ۲۴ ساعت یک‌بار${ME.premium?' · ×۲٫۵ با Premium':''}</small></span>${ic('chev','chev')}</button>
 <button class="set" id="sd"><span class="si" style="background:#f5a623">${ic('send')}</span><span class="mid">ارسال استارز به دیگران</span>${ic('chev','chev')}</button>
 <button class="set" id="gf"><span class="si" style="background:#e8637a">${ic('gift')}</span><span class="mid">ارسال هدیه</span>${ic('chev','chev')}</button>
 <button class="set" data-a="set:invite"><span class="si" style="background:#2f9bef">${ic('users')}</span><span class="mid">دعوت دوستان<small>۵۰ استارز برای هر دعوت</small></span>${ic('chev','chev')}</button></div>
 <div class="sec">تراکنش‌ها</div><div class="seg" id="sg"><button class="on" data-f="all">همه</button><button data-f="in">دریافتی</button><button data-f="out">ارسالی</button></div><div class="card" id="txl"></div><div style="height:30px"></div>`,e=>{e._wallet=1;
  const rows=()=>{const l=w.tx.filter(x=>f==='all'||(f==='in'?x.delta>0:x.delta<0));$('#txl',e).innerHTML=l.map(x=>{const[i,c]=TXI(x.reason);return`<div class="txr"><span class="tic" style="background:${c}">${ic(i)}</span><span class="mid">${esc(x.reason)}<small>${fDTF(x.created)}</small></span><span class="am ${x.delta>0?'p':'n'}">${x.delta>0?'+':'−'}${nf(Math.abs(x.delta))}${SI(14)}</span></div>`}).join('')||'<div class="mu" style="text-align:center;padding:24px">تراکنشی نیست</div>'};rows();
  $$('[data-f]',e).forEach(b=>b.onclick=()=>{f=b.dataset.f;$$('[data-f]',e).forEach(x=>x.classList.toggle('on',x===b));rows()});
  $('#by',e).onclick=buyStars;
  $('#dl',e).onclick=run(async()=>{if(!w.daily_ready)return toast('پاداش امروز را دریافت کرده‌اید');const d=await api('stars/daily','POST');toast('+'+d.amount+' استارز');await sync();back();later(walletPage)});
  $('#sd',e).onclick=()=>pickUser(async u=>{const a=await prompt2('تعداد استارز برای '+u.nick,'10','number');if(!a)return;try{await api('stars/send','POST',{to:u.id,amount:+a})}catch(er){if(lowStars(er))return noStars();throw er}toast('ارسال شد');await sync();back();later(walletPage)});
  $('#gf',e).onclick=()=>pickUser(u=>giftShop(u,()=>{back();later(walletPage)}))})}
function buyStars(){api('wallet').then(w=>{const ps=w.packs||[50,100,250,500,1000,2500],off=w.shop==='off';
 bsheet(`<div class="shh"><button class="ib" data-x>${ic('x')}</button><div class="t">خرید استارز</div><span style="width:44px"></span></div><div class="phero" style="padding-top:0"><div class="stage" style="height:120px;width:170px">${sparks(14,'g')}${goldStar()}</div><p>${off?'خرید استارز فعلاً غیرفعال است.':'درگاه پرداخت واقعی به NEXO متصل نیست؛ بسته‌ها در این نسخه آزمایشی و رایگان‌اند.'}</p></div>
  <div class="card" style="margin:8px 12px">${ps.map(n=>`<button class="pk" data-n="${n}" ${off?'disabled style="opacity:.5"':''}><span class="pks">${SI(22)}${n>=250?SI(22):''}${n>=1000?SI(22):''}</span><b>${nf(n)} استارز</b><span class="pkp">دریافت</span></button>`).join('')}</div>`,(sh,close)=>{
  $$('[data-n]',sh).forEach(b=>b.onclick=run(async()=>{const d=await api('stars/buy','POST',{pack:+b.dataset.n});close();toast('+'+nf(d.amount)+' استارز');await sync();const top=PAGES[PAGES.length-1];if(top&&top._wallet){back();later(walletPage)}}))})}).catch(er=>toast(er.message))}

/* ---- premium ---- */
async function premiumPage(){const w=await api('wallet'),P=w.plans,keys=Object.keys(P),prem=ME.premium;let sel=keys.includes('y1')?'y1':keys[0];
 const el=page('NEXO Premium',`<div class="phero"><div class="stage">${sparks(28)}${premStar()}</div>
 <h2>${prem?`<span class="hl">${esc(ME.nick)}</span> مشترک NEXO Premium است.`:'NEXO Premium'}</h2>
 <p>${prem?`اشتراک شما تا ${fD(w.premium_until)} فعال است. دارندگان Premium به چندین قابلیت انحصاری دسترسی دارند.`:'با ارتقا به Premium از محدودیت‌های کمتر و قابلیت‌های انحصاری بهره‌مند شوید.'}</p></div>
 <div id="pl" class="pcs" style="margin:4px 0 10px"></div>
 <div class="card">${FEATS.map(f=>`<div class="feat"><span class="fi" style="background:${f[1]}">${f[0]==='x2'?'x2':ic(f[0])}</span><div class="mid"><b>${f[2]}</b><span class="d">${f[3]}</span></div>${ic('chev','chev')}</div>`).join('')}</div><div style="height:96px"></div>`,e=>{
  const draw=()=>{$('#pl',e).innerHTML=keys.map(k=>{const d=disc(P,k);return`<button class="pc ${sel===k?'on':''}" data-k="${k}">${d>0?`<span class="rib">-${nf(d)}٪</span>`:''}<b>${PN[k]||k}</b><span class="mu sm">پرمیوم</span><span class="pp">${SI(12)} ${nf(P[k][1])}</span></button>`}).join('');
   $$('[data-k]',e).forEach(b=>b.onclick=()=>{sel=b.dataset.k;draw()});$('#bb',e).innerHTML=(prem?'تمدید اشتراک با ':'خرید اشتراک با ')+nf(P[sel][1])+' '+SI(16)+' برای '+(PN2[sel]||sel)};
  const st=document.createElement('div');st.className='stick';st.innerHTML='<button class="btn" id="bb"></button>';e.append(st);draw();
  $('#bb',e).onclick=run(async()=>{if(!await ask(`${prem?'تمدید':'خرید'} NEXO Premium ${PN2[sel]||sel} با ${nf(P[sel][1])} استارز؟`,'تأیید'))return;
   try{await api('premium/buy','POST',{plan:sel})}catch(er){if(lowStars(er))return noStars();throw er}toast('NEXO Premium فعال شد ✨');await sync();back();later(premiumPage)})})}

/* ---- profiles ---- */
const infoCard=(u,mine)=>`<div class="card">${u.bio?`<div class="kv"><div>${esc(u.bio)}</div><small>بیوگرافی</small></div>`:''}<div class="kv" data-x="cp" style="display:flex;align-items:center;justify-content:space-between;cursor:pointer"><div><div dir="ltr" style="text-align:start">@${esc(u.username)}</div><small>${t('نام کاربری')}</small></div><span class="mu">${ic('qr')}</span></div>${u.phone?`<div class="kv"><div dir="ltr" style="text-align:start">${esc(u.phone)}</div><small>${t('شماره تلفن')}</small></div>`:''}${u.birth?`<div class="kv"><div>${fD(Date.parse(u.birth)/1000)}</div><small>تاریخ تولد</small></div>`:''}</div>`;
async function showUser(id){if(id===ME.id)return;let u=await api('users/'+id),tab=0;
 const el=page('',`<div id="ub"></div>`,e=>{
  const render=()=>{$('#ub',e).innerHTML=`<div class="phd">${av(u,104)}<h2>${esc(u.nick)}${prb(u)}</h2><div class="stt ${u.online?'on':''}">${seen(u)}</div></div>
   <div class="acts" style="margin-top:8px"><button data-x="msg">${ic('chat')}${t('پیام')}</button><button data-x="gift">${ic('gift')}هدیه</button><button data-x="star">${SI(24)}استارز</button></div>
   ${infoCard(u)}<div class="card"><button class="set" data-x="ct">${ic(u.contact?'x':'user')}<span class="mid" style="color:var(--lk)">${u.contact?'حذف از مخاطبین':'افزودن به مخاطبین'}</span></button></div>
   <div class="ptabs"><div class="seg"><button class="${tab?'':'on'}" data-t="0">${t('پست‌ها')}</button><button class="${tab?'on':''}" data-t="1">هدایا ${u.gifts.slice(0,3).map(g=>g.emoji).join('')}</button></div></div>
   <div>${tab?giftGrid(u.gifts,false):posts(u.posts)}</div><div style="height:90px"></div>`;
   $$('[data-t]',e).forEach(b=>b.onclick=()=>{tab=+b.dataset.t;render()});bindGifts(e,u.gifts);$$('[data-x]',e).forEach(b=>b.onclick=run(X[b.dataset.x]))};
  const reload=async()=>{u=await api('users/'+id);render()};e._reload=reload;
  const X={msg:()=>{back();later(()=>startChat(id))},gift:()=>giftShop(u,reload),
   star:async()=>{const a=await prompt2('تعداد استارز برای '+u.nick,'10','number');if(!a)return;try{await api('stars/send','POST',{to:id,amount:+a})}catch(er){if(lowStars(er))return noStars();throw er}toast('ارسال شد');sync()},
   cp:()=>{navigator.clipboard&&navigator.clipboard.writeText('@'+u.username);toast('کپی شد')},
   ct:async()=>{u.contact?await api('contacts/'+id,'DELETE'):await api('contacts','POST',{id});toast('انجام شد');reload()}};
  e._menu=()=>sheet([{t:u.blocked?'رفع مسدودی':'مسدود کردن',i:'x',f:async()=>{u.blocked?await api('block/'+id,'DELETE'):await api('block/'+id,'POST');toast('انجام شد');reload()}},{t:'گزارش تخلف',i:'flag',d:1,f:async()=>{const r=await prompt2('دلیل گزارش');if(r){await api('report','POST',{kind:'user',target:id,reason:r});toast('گزارش ثبت شد')}}}]);
  $('#um',e).onclick=()=>e._menu();render()},`<button class="ib" id="um">${ic('more')}</button>`);
 const fb=document.createElement('button');fb.className='gfab';fb.innerHTML=`${ic('gift')} ارسال هدیه به ${esc(u.nick)}`;fb.onclick=()=>giftShop(u,()=>el._reload());el.append(fb)}
let OTAB=0;
async function drawProfile(){$('#top').innerHTML=`<div class="hdr"><div class="t">${t('پروفایل')}</div><button class="ib" data-a="tab:settings">${ic('gear')}</button></div>`;$('#fabw').innerHTML='';const u=await api('users/'+ME.id);ME={...ME,...u};const vw=$('#vw');const ps=u.posts.map(p=>({...p,mine:1}));
 vw.innerHTML=`<div class="phd">${av(u,104)}<h2>${esc(u.nick)}${prb(u)}</h2><div class="stt on">${t('آنلاین')}</div></div>
 <div class="acts" style="margin-top:8px"><button data-a="setphoto">${ic('cam')}${t('تنظیم عکس')}</button><button data-a="editinfo">${ic('edit')}${t('ویرایش اطلاعات')}</button><button data-a="wallet">${SI(24)}استارز</button></div>
 ${infoCard(u,1)}
 <div class="ptabs"><div class="seg"><button class="${OTAB===0?'on':''}" data-a="ptab:0">${t('پست‌ها')}</button><button class="${OTAB===1?'on':''}" data-a="ptab:1">هدایا ${u.gifts.slice(0,3).map(g=>g.emoji).join('')}</button><button class="${OTAB===2?'on':''}" data-a="ptab:2">${t('بایگانی')}</button></div></div>
 <div>${OTAB===1?giftGrid(u.gifts,true):posts(ps.filter(p=>!!p.archived===(OTAB===2)))}</div><div style="height:90px"></div>`;
 bindGifts(vw,u.gifts);$$('[data-x=cp]',vw).forEach(b=>b.onclick=()=>{navigator.clipboard&&navigator.clipboard.writeText('@'+u.username);toast('کپی شد')});
 $('#fabw').innerHTML=OTAB===1?`<button class="gfab" data-a="sendgift">${ic('gift')} ارسال هدیه</button>`:OTAB===0?`<button class="fab" data-a="addpost">${ic('cam','l')}</button>`:''}
function drawSettings(){$('#top').innerHTML=`<div class="hdr"><div class="t">${t('تنظیمات')}</div><button class="ib" data-a="gsearch">${ic('search')}</button></div>`;$('#fabw').innerHTML='';
 const it=(k,i,tt,s)=>`<button class="set" data-a="set:${k}">${ic(i)}<span class="mid">${t(tt)}${s?`<small>${s}</small>`:''}</span></button>`;
 const sp=(a,bg,icon,tt,s)=>`<button class="set" data-a="${a}"><span class="si" style="background:${bg}">${icon}</span><span class="mid">${tt}${s?`<small>${s}</small>`:''}</span></button>`;
 $('#vw').innerHTML=`<div class="phd" style="padding-top:20px"><button data-a="setphoto" style="display:block">${av(ME,96)}</button><h2>${esc(ME.nick)}${prb(ME)}</h2><div class="stt" dir="ltr">${esc(ME.phone||'')}${ME.phone?' · ':''}@${esc(ME.username)}</div></div>
 <div class="card">${it('account','user','حساب کاربری','نام، نام کاربری، بیو')}${it('chat','chat','تنظیمات گفتگو','ارسال با Enter، حباب‌ها، پس‌زمینه')}${it('privacy','lock','حریم خصوصی','آخرین بازدید، شماره، پیام‌ها')}${it('security','shield','امنیت','رمز عبور، تأیید دومرحله‌ای')}${it('notif','bell','اعلان‌ها','')}${it('sound','mic','صدا','')}${it('data','db','داده‌ها و ذخیره‌سازی','')}${it('folders','folder','پوشه‌های گفتگو','')}${it('devices','dev','دستگاه‌ها','')}${it('lang','globe','زبان',LANG==='en'?'English':'فارسی')}</div>
 <div class="card">${sp('premium','linear-gradient(135deg,#8d7af5,#3fb0f7)',ic('star'),'NEXO Premium',ME.premium?'فعال تا '+fD(ME.premium_until):'')}${sp('wallet','linear-gradient(135deg,#ffd54a,#f59a1b)',ic('star'),'استارز',nf(ME.stars)+' استارز')}${sp('sendgift','linear-gradient(135deg,#ff7a8a,#e8446e)',ic('gift'),'ارسال هدیه','')}</div>
 <div class="card">${it('theme','pal','پوسته و تم','حالت تاریک/روشن، رنگ، AI UI Designer')}${it('font','edit','فونت','')}${it('ai','spark','هوش مصنوعی','NEXO AI، حافظه، شخصیت')}${it('ads','mega','تبلیغات','')}${it('blocks','x','بلاک‌شدگان','')}${it('invite','users','دعوت دوستان','۵۰ استارز برای هر دعوت')}</div>
 <div class="card">${it('rules','help','قوانین NEXO','')}${it('about','help','درباره NEXO','نسخه ۱.۰')}${LV()>=1?it('admin','gear','پنل مدیریت','فقط مدیران'):''}<button class="set" data-a="logout" style="color:var(--er)">${ic('x')}<span class="mid">${t('خروج')}</span></button></div><div style="height:30px"></div>`}

/* ---------- AI ---------- */
const md=s=>esc(s).replace(/```(\w*)\n?([\s\S]*?)```/g,(m,l,c)=>`<pre>${c}</pre>`).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
async function aiPage(){const st=await api('ai/state');const el=page('NEXO AI',`<div id="msgs" style="flex:1">${st.history.map(m=>`<div class="ai-m ${m.role==='user'?'u':'a'}">${m.role==='user'?esc(m.content):md(m.content)}</div>`).join('')||'<p class="mu" style="text-align:center;padding:30px">سلام! من NEXO AI هستم. از برنامه‌نویسی تا نوشتن؛ بپرس. با «یادت باشه: ...» چیزی را به خاطر می‌سپارم.</p>'}</div><div class="comp"><textarea id="ai" rows="1" placeholder="${t('پیام بنویسید...')}"></textarea><button class="send" id="as">${ic('send')}</button></div>`,e=>{
  const box=$('#msgs',e),vw=e.querySelector('.view');vw.style.cssText='display:flex;flex-direction:column;overflow:hidden';box.scrollTop=box.scrollHeight;
  const send=run(async()=>{const ta=$('#ai',e),tx=ta.value.trim();if(!tx)return;ta.value='';if(box.querySelector('p.mu'))box.innerHTML='';box.insertAdjacentHTML('beforeend',`<div class="ai-m u">${esc(tx)}</div><div class="ai-m a" id="th">در حال فکر کردن...</div>`);box.scrollTop=box.scrollHeight;
   try{const d=await api('ai/chat','POST',{text:tx});$('#th',e).outerHTML=`<div class="ai-m a">${md(d.answer)}</div>`}catch(er){$('#th',e).remove();toast(er.message)}box.scrollTop=box.scrollHeight});
  $('#as',e).onclick=send;$('#ai',e).onkeydown=ev=>{if(ev.key==='Enter'&&!ev.shiftKey&&ME.prefs.enter){ev.preventDefault();send()}}},
  `<button class="ib" id="ao">${ic('gear')}</button>`);
 $('#ao',el).onclick=()=>aiSettings(st)}
function aiSettings(st){page('تنظیمات AI',`<div class="pad"><div><label class="lab">رفتار و شخصیت NEXO AI</label><textarea id="ps" rows="3" placeholder="مثلاً: خلاصه و رسمی جواب بده">${esc(st.prefs.persona||'')}</textarea></div>
 <div><label class="lab">مدل ${st.premium?'':'(انتخاب مدل مخصوص Premium)'}</label><select id="md" ${st.premium?'':'disabled'}>${st.models.map(m=>`<option ${m===st.model?'selected':''}>${esc(m)}</option>`).join('')}</select></div>
 <button class="btn" id="sv">${t('ذخیره')}</button>
 <div class="sec" style="padding:0">حافظه بلندمدت</div><div id="ml">${st.mem.map(m=>`<div class="set" style="padding:6px 0"><span class="mid">${esc(m.text)}</span><button class="ib" data-d="${m.id}">${ic('trash')}</button></div>`).join('')||'<p class="mu sm">چیزی ذخیره نشده</p>'}</div>
 <button class="btn g" id="am">افزودن به حافظه</button><button class="btn d" id="ch">پاک‌کردن تاریخچه مکالمات</button>${st.has_key?'':'<p class="sm" style="color:var(--er)">کلید OpenRouter روی سرور تنظیم نشده؛ مدیر باید آن را در پنل مدیریت یا متغیر NEXO_OPENROUTER_KEY وارد کند.</p>'}</div>`,e=>{
  $('#sv',e).onclick=run(async()=>{await api('ai/prefs','POST',{persona:$('#ps',e).value,model:st.premium?$('#md',e).value:undefined});toast('ذخیره شد')});
  $$('[data-d]',e).forEach(b=>b.onclick=run(async()=>{await api('ai/mem','POST',{delete:+b.dataset.d});b.closest('.set').remove()}));
  $('#am',e).onclick=run(async()=>{const v=await prompt2('چه چیزی را به خاطر بسپارد؟');if(v){await api('ai/mem','POST',{text:v});back();back();later(aiPage)}});
  $('#ch',e).onclick=run(async()=>{if(await ask('تاریخچه پاک شود؟','پاک‌کردن')){await api('ai/history','DELETE');back();back();later(aiPage)}})})}
/* AI UI Designer: theme-only (never executes generated code) */
function designer(){if(!ME.premium)return toast('AI UI Designer مخصوص Premium است'),premiumPage();let pend=null;
 page('AI UI Designer',`<div class="pad"><p class="mu sm">تغییرات فقط روی حساب شما اعمال می‌شود و از طریق سیستم تم کنترل‌شده انجام می‌شود (نه اجرای کد). همیشه می‌توانید به ظاهر اصلی برگردید.</p><textarea id="pr" rows="3" placeholder="مثلاً: ظاهر NEXO رو مشکی و بنفش کن"></textarea>
 <div class="chips" style="padding:0;flex-wrap:wrap">${['ظاهر NEXO رو مشکی و بنفش کن','صفحه چت رو شبیه رابط شیشه‌ای طراحی کن','انیمیشن‌های برنامه رو بیشتر کن','منوی پایین رو تغییر بده','گوشه‌ها گردتر و فونت بزرگ‌تر'].map(s=>`<button data-s="${s}">${s}</button>`).join('')}</div>
 <button class="btn" id="go">ساخت پیش‌نمایش</button><div id="pv" hidden class="card" style="margin:0;padding:14px"><div id="nt" class="mu sm"></div><div style="display:flex;gap:10px;margin-top:12px"><button class="btn" id="ap">اعمال و ذخیره</button><button class="btn g" id="cn">لغو</button></div></div></div>`,e=>{
  $$('[data-s]',e).forEach(b=>b.onclick=()=>$('#pr',e).value=b.dataset.s);
  const cancel=()=>{applyTheme(ME.theme);$('#pv',e).hidden=true;pend=null};e._close=()=>applyTheme(ME.theme);
  $('#go',e).onclick=run(async()=>{const d=await api('ai/theme','POST',{prompt:$('#pr',e).value});pend=d.theme;applyTheme({...(ME.theme||{}),...pend});$('#pv',e).hidden=false;$('#nt',e).textContent='پیش‌نمایش فعال است — '+d.note+(d.engine==='rules'?' (تحلیل محلی، بدون AI)':'')});
  $('#cn',e).onclick=cancel;$('#ap',e).onclick=run(async()=>{const d=await api('theme','POST',{theme:{...(ME.theme||{}),...pend}});ME.theme=d.theme;applyTheme(ME.theme);$('#pv',e).hidden=true;pend=null;toast('تغییرات ذخیره شد')})})}

/* ---------- user profile ---------- */

/* ---------- chat screen ---------- */
const EMO=['😀','😂','🤣','😊','😍','🥰','😘','😎','🤔','😴','😭','😡','🥳','🤯','😇','🙄','👍','👎','👏','🙏','💪','🤝','👀','🔥','❤️','💔','✨','🎉','🎂','🌹','☕','🍕','⭐','💯','✅','❌'];
const STK=['😀','😂','😍','🥳','😎','😭','😡','👍','🙏','❤️','🔥','🎉'],PSTK=['🦄','🐉','🪐','🌈','👑','💎'],RX=['👍','❤️','😂','😮','😢','🙏'],PRX=['🔥','💎','🚀','👑'];
const tick=(m)=>{if(m.sender!==ME.id)return'';return m.id<=CUR.pr?`<span class="rd">${ic('dbl')}</span>`:m.id<=CUR.pd?ic('dbl'):ic('check')};
function msgHtml(m){const mine=m.sender===ME.id,s=CUR.sel.has(m.id)?' sel':'';
 if(m.kind==='gift'){const[gm,gn,gp,gnote]=(m.text||'').split('|');return`<div class="gm${s}" data-a="mm:${m.id}"><span class="gem">${esc(gm)}</span><b>${mine?'شما هدیه‌ای فرستادید':esc(m.sname)+' هدیه‌ای فرستاد'}</b><span>${esc(gn)}</span><span class="gp">${SI(13)} ${nf(gp)}</span>${gnote?`<i>«${esc(gnote)}»</i>`:''}</div>`}
 if(m.kind==='sticker')return`<div class="stk ${mine?'me':'th'}${s}" data-a="mm:${m.id}"><span class="${PSTK.includes(m.text)?'anim':''}">${esc(m.text)}</span></div>`;
 let c='';if(m.kind==='image')c=(ME.prefs.autoimg===false&&!LOADED.has(m.file))?`<button class="fl" data-a="ld:${esc(m.file)}">${ic('cam')} دانلود تصویر (${sz(m.fsize)})</button>`:`<img class="im" src="/f/${esc(m.file)}" data-a="img:${esc(m.file)}" loading="lazy">`;
 else if(m.kind==='video')c=`<video src="/f/${esc(m.file)}" controls preload="${ME.prefs.autovid?'metadata':'none'}" playsinline></video>`;
 else if(m.kind==='voice')c=`<audio src="/f/${esc(m.file)}" controls preload="none"></audio>`;
 else if(m.kind==='file')c=`<a class="fl" href="/f/${esc(m.file)}" download>${ic('file')}<span><b>${esc(m.fname||'فایل')}</b><br><small>${sz(m.fsize||0)}</small></span></a>`;
 if(m.text&&m.kind!=='sticker')c+=(c?'<div>':'<span>')+esc(m.text)+(c?'</div>':'</span>');
 const rx=Object.entries(m.rx||{}).map(([e,u])=>`<span class="rx ${u.includes(ME.id)?'mine':''} " data-a="rx:${m.id}:${e}"><span class="${PRX.includes(e)?'anim':''}">${e}</span> ${u.length}</span>`).join('');
 return`<div class="bb ${mine?'me':'th'}${s}" data-id="${m.id}" data-a="mm:${m.id}">${!mine&&CUR.c.type!=='private'?`<span class="nm">${esc(m.sname)} ${m.sprem?'⭐':''}</span>`:''}${m.fwd?`<span class="fw">↪ فوروارد از ${esc(m.fwd)}</span>`:''}${m.reply?`<span class="rp" data-a="goto:${m.reply.id}"><b>${esc(m.reply.name)}</b> ${esc((m.reply.text||'').slice(0,60))}</span>`:''}${c}${rx?`<div class="rxs">${rx}</div>`:''}<div class="ft">${m.pinned?ic('pin','s'):''}${m.expire?'⏱':''}${m.edited?'ویرایش‌شده':''}<span>${fT(m.created)}</span>${tick(m)}</div></div>`}
const LOADED=new Set();
function renderMsgs(){const box=$('#msgs');if(!box||!CUR||CUR.ai)return;const near=box.scrollHeight-box.scrollTop-box.clientHeight<140;let h='',last='';const n=Date.now()/1000;
 if(CUR.more)h+=`<button class="tag" style="align-self:center" data-a="older">پیام‌های قدیمی‌تر</button>`;
 CUR.msgs.filter(m=>!m.deleted&&!(m.expire&&m.expire<n)).forEach(m=>{const d=fD(m.created);if(d!==last){h+=`<div class="day">${d}</div>`;last=d}h+=msgHtml(m)});
 CUR.pending.forEach(p=>h+=`<div class="bb me"><span>${ic('clip','s')} ${esc(p.name)}</span><div class="prog"><i style="width:${p.pct}%"></i></div><div class="ft">${p.pct}٪</div></div>`);
 box.innerHTML=h;if(near||CUR.first){box.scrollTop=box.scrollHeight;CUR.first=false}
 const pin=CUR.pins[0];$('#pinb').hidden=!pin;if(pin)$('#pinb .tx').textContent=pin.text}
function chatHdr(){const c=CUR.c,o=c.peer;if(CUR.sel.size)return`<button class="ib" data-a="csx">${ic('x')}</button><div class="t">${nf(CUR.sel.size)}</div><button class="ib" data-a="cs:fwd">${ic('fwd')}</button><button class="ib" data-a="cs:copy">${ic('copy')}</button><button class="ib" data-a="cs:del">${ic('trash')}</button>`;
 return`<button class="ib" data-a="back">${ic('back')}</button><button class="row" style="padding:0;flex:1" data-a="cinfo">${av(c,40)}<div class="mid"><div class="l1"><b>${esc(c.title)}</b>${prb(o)}</div><div class="sm st ${CUR.c.peer&&CUR.c.peer.online?'on':'mu'}">${CUR.sub||''}</div></div></button><button class="ib" data-a="csearch">${ic('search')}</button><button class="ib" data-a="cmenu">${ic('more')}</button>`}
function openChat(id){const c=CH.find(x=>x.id===id);if(!c)return;if(PAGES.length&&CUR)return;
 CUR={id,c,msgs:[],upd:0,ts:0,pr:0,pd:0,pins:[],reply:null,edit:null,sel:new Set(),role:c.role,pending:[],first:true,more:false,ttl:null,at:null,rec:null,sub:''};
 const canPost=!(c.type==='channel'&&!['owner','admin'].includes(c.role));
 const el=document.createElement('div');el.className='page';el.id='chatpg';
 el.innerHTML=`<div class="hdr" id="ch">${chatHdr()}</div><div class="bar" id="pinb" hidden>${ic('pin','s')}<span class="tx"></span></div><div id="sb" class="pad" style="padding:8px 14px;background:var(--s1)" hidden><input id="sq" placeholder="جستجو در پیام‌ها"></div><div id="msgs"></div>
 <div id="rb" class="bar" hidden></div><div id="pnl" hidden></div>
 ${canPost?`<div class="comp"><div class="pill"><button class="ib" data-a="emo">${ic('smile')}</button><textarea id="inp" rows="1" placeholder="${t('پیام بنویسید...')}"></textarea><button class="ib" data-a="attach">${ic('clip')}</button></div><button class="send" id="sendb" data-a="send">${ic('mic')}</button></div>`:`<div class="bar" style="justify-content:center"><button class="tag" data-a="cmute">${c.muted?'فعال‌کردن صدا':'بی‌صدا کردن'}</button></div>`}`;
 $('#pages').append(el);PAGES.push(el);history.pushState({p:PAGES.length},'');el._close=()=>{if(CUR&&CUR.rec)recStop(true);CUR=null;draw()};
 const ta=$('#inp',el);if(ta){const upd=()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,120)+'px';$('#sendb').innerHTML=ic(ta.value.trim()||CUR.edit?'send':'mic')};ta.oninput=upd;ta.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&ME.prefs.enter){e.preventDefault();sendText()}}}
 let T;$('#sq',el).oninput=e=>{clearTimeout(T);T=setTimeout(run(async()=>{const q=e.target.value.trim();if(!q){CUR.search=null;return pollFull()}const d=await api(`chats/${id}/messages?q=${encodeURIComponent(q)}`);CUR.search=1;CUR.msgs=d.messages;renderMsgs()}),300)};
 pollFull()}
async function pollFull(){CUR.upd=0;CUR.msgs=[];CUR.first=true;await pollChat()}
async function pollChat(){if(!CUR||CUR.search)return;const id=CUR.id,d=await api(`chats/${id}/messages?since=${CUR.upd}&ts=${CUR.ts}`);if(!CUR||CUR.id!==id)return;
 const fresh=!CUR.upd;let got=false;d.messages.forEach(m=>{const i=CUR.msgs.findIndex(x=>x.id===m.id);if(i>=0)CUR.msgs[i]=m;else{CUR.msgs.push(m);if(m.sender!==ME.id)got=true}});
 CUR.msgs.sort((a,b)=>(a.created-b.created)||(a.id-b.id));if(fresh)CUR.more=d.messages.length>=50;CUR.upd=d.upd;CUR.ts=d.ts;CUR.pr=d.peer_read;CUR.pd=d.peer_delivered;CUR.pins=d.pins;CUR.role=d.role;
 const c=CH.find(x=>x.id===id);if(c){CUR.c=c;const o=c.peer;CUR.sub=c.type==='private'?(o?seen(o):''):(c.type==='channel'?'کانال':'گروه');}
 const h=$('#ch');if(h&&!CUR.sel.size)h.innerHTML=chatHdr();renderMsgs();
 if(!document.hidden&&(got||fresh||(c&&c.unread)))api(`chats/${id}/read`,'POST').catch(()=>{})}
const sendText=run(async()=>{const ta=$('#inp'),tx=ta.value.trim();if(!tx)return;
 if(CUR.edit){await api('messages/'+CUR.edit,'PUT',{text:tx});CUR.edit=null}
 else{await api(`chats/${CUR.id}/messages`,'POST',{text:tx,reply_to:CUR.reply&&CUR.reply.id,ttl:CUR.ttl,at:CUR.at});if(CUR.at)toast('پیام زمان‌بندی شد')}
 ta.value='';ta.dispatchEvent(new Event('input'));CUR.reply=null;CUR.ttl=CUR.at=null;bars();CUR.first=true;await pollChat()});
function bars(){const r=$('#rb');if(!r)return;let h='';if(CUR.edit)h=`${ic('edit','s')}<span class="tx">در حال ویرایش پیام</span>`;else if(CUR.reply)h=`${ic('reply','s')}<span class="tx"><b>${esc(CUR.reply.name)}</b> ${esc((CUR.reply.text||'').slice(0,50))}</span>`;
 if(CUR.ttl||CUR.at)h+=`<span class="tag">${CUR.at?'⏰ '+fDT(CUR.at):''} ${CUR.ttl?'⏱ خودتخریب '+({10:'۱۰ ثانیه',60:'۱ دقیقه',3600:'۱ ساعت',86400:'۱ روز'}[CUR.ttl]):''}</span>`;
 r.hidden=!h;r.innerHTML=h+(h?`<button class="ib" data-a="rbx">${ic('x','s')}</button>`:'')}
async function sendFile(f,hint){const p={name:f.name,pct:0};CUR.pending.push(p);const id=CUR.id;renderMsgs();
 try{const r=await up(f,hint,v=>{p.pct=v;renderMsgs()});await api(`chats/${id}/messages`,'POST',{kind:r.kind,file:r.file,reply_to:CUR.reply&&CUR.reply.id,ttl:CUR.ttl});CUR.reply=null;bars()}catch(e){toast(e.message)}
 CUR.pending=CUR.pending.filter(x=>x!==p);CUR.first=true;await pollChat()}
async function recStart(){try{const s=await navigator.mediaDevices.getUserMedia({audio:true});const mr=new MediaRecorder(s),ch=[];mr.ondataavailable=e=>ch.push(e.data);
 mr.onstop=()=>{s.getTracks().forEach(t=>t.stop());if(mr.cancel)return;const ty=mr.mimeType||'audio/webm';const ext=ty.includes('ogg')?'ogg':ty.includes('mp4')?'m4a':'webm';sendFile(new File([new Blob(ch,{type:ty})],'voice.'+ext,{type:ty}),'voice')};mr.start();CUR.rec=mr;$('#sendb').classList.add('rec');$('#sendb').innerHTML=ic('send');toast('در حال ضبط؛ برای ارسال دوباره بزنید')}catch(e){toast('دسترسی به میکروفون ممکن نیست (نیازمند localhost یا HTTPS)')}}
function recStop(cancel){const r=CUR.rec;if(!r)return;r.cancel=!!cancel;r.stop();CUR.rec=null;const b=$('#sendb');if(b){b.classList.remove('rec');b.innerHTML=ic('mic')}}
function msgById(id){return CUR.msgs.find(m=>m.id===id)}
function msgSheet(id){const m=msgById(id);if(!m||m.deleted)return;const mine=m.sender===ME.id,adm=['owner','admin'].includes(CUR.role);const rx=[...RX,...PRX].map(e=>`<button style="font-size:1.6rem;padding:6px 9px" data-e="${e}" class="${PRX.includes(e)?'anim':''}">${e}${PRX.includes(e)&&!ME.premium?'🔒':''}</button>`).join('');
 const ov=sheet([{t:'پاسخ',i:'reply',f:()=>{CUR.reply={id:m.id,name:m.sname,text:m.text||'📎'};bars();$('#inp')&&$('#inp').focus()}},m.text&&{t:'کپی',i:'copy',f:()=>{navigator.clipboard&&navigator.clipboard.writeText(m.text);toast('کپی شد')}},mine&&m.kind==='text'&&{t:'ویرایش',i:'edit',f:()=>{CUR.edit=m.id;const ta=$('#inp');ta.value=m.text;ta.dispatchEvent(new Event('input'));ta.focus();bars()}},
  {t:'فوروارد',i:'fwd',f:()=>fwd([m.id])},(adm||CUR.c.type==='private')&&{t:m.pinned?'برداشتن سنجاق':'سنجاق کردن پیام',i:'pin',f:async()=>{await api(`messages/${id}/pin`,'POST');pollChat()}},{t:'انتخاب',i:'check',f:()=>{CUR.sel.add(id);$('#ch').innerHTML=chatHdr();renderMsgs()}},
  !mine&&{t:'گزارش تخلف',i:'flag',f:async()=>{const r=await prompt2('دلیل گزارش');if(r){await api('report','POST',{kind:'message',target:id,reason:r});toast('ثبت شد')}}},(mine||adm)&&{t:'حذف برای همه',i:'trash',d:1,f:async()=>{if(await ask('پیام حذف شود؟','حذف')){await api('messages/'+id,'DELETE');pollChat()}}}],`<div style="display:flex;justify-content:center;flex-wrap:wrap;padding:6px">${rx}</div>`);
 $$('[data-e]',ov).forEach(b=>b.onclick=run(async()=>{ov.remove();await api(`messages/${id}/react`,'POST',{emoji:b.dataset.e});pollChat()}))}
function fwd(ids){sheet(CH.map(c=>({t:c.title,i:'chat',f:async()=>{for(const i of ids)await api(`messages/${i}/forward`,'POST',{chats:[c.id]});CUR.sel.clear();toast('فوروارد شد');$('#ch').innerHTML=chatHdr();renderMsgs();sync()}})),'<div class="sec">فوروارد به...</div>')}
function chatMenu(){const c=CUR.c;sheet([{t:'جستجو در پیام‌ها',i:'search',f:()=>{$('#sb').hidden=false;$('#sq').focus()}},{t:c.muted?'فعال‌کردن صدا':'بی‌صدا کردن',i:c.muted?'bell':'mute',f:async()=>{await api('chats/bulk','POST',{ids:[c.id],action:c.muted?'unmute':'mute'});await sync()}},
 {t:'تحلیل گفتگو با NEXO AI',i:'spark',f:async()=>{toast('در حال تحلیل...');const d=await api('ai/analyze','POST',{chat_id:c.id});modal(`<h3 style="margin-bottom:10px">تحلیل NEXO AI</h3><div class="ai-m a" style="max-width:100%">${md(d.answer)}</div>`)}},
 {t:'زمان‌بندی / خودتخریب',i:'clock',f:timerSheet},{t:'پاک‌کردن کش این گفتگو',i:'broom',f:clearCache},{t:'بایگانی',i:'arch',f:async()=>{await api('chats/bulk','POST',{ids:[c.id],action:'archive'});back();sync()}},
 c.type!=='private'&&{t:'خروج از '+(c.type==='channel'?'کانال':'گروه'),i:'x',d:1,f:async()=>{if(await ask('خارج می‌شوید؟','خروج')){await api(`chats/${c.id}/members/${ME.id}`,'DELETE');back();sync()}}}])}
function timerSheet(){sheet([{t:'ارسال عادی',f:()=>{CUR.ttl=CUR.at=null;bars()}},...[[10,'خودتخریب ۱۰ ثانیه'],[60,'خودتخریب ۱ دقیقه'],[3600,'خودتخریب ۱ ساعت'],[86400,'خودتخریب ۱ روز']].map(([s,l])=>({t:l,i:'clock',f:()=>{CUR.ttl=s;bars()}})),
 {t:'زمان‌بندی ارسال...',i:'clock',f:async()=>{const v=await prompt2('زمان ارسال','','datetime-local');if(v){const ts=Math.floor(new Date(v).getTime()/1000);if(ts<=Date.now()/1000)return toast('زمان باید در آینده باشد');CUR.at=ts;bars()}}}])}
async function groupInfo(){const c=CUR.c,ms=await api(`chats/${c.id}/members`),adm=['owner','admin'].includes(CUR.role);
 page(c.title,`<div class="big">${av(c,100)}<h2>${esc(c.title)}</h2><div class="mu">${c.type==='channel'?'کانال':'گروه'}${c.username?' · @'+esc(c.username):''} · ${nf(ms.length)} عضو</div><p class="mu">${esc(c.about||'')}</p></div>${adm?`<div class="card"><button class="set" id="ed"><span class="mid">ویرایش عنوان و توضیحات</span></button><button class="set" id="ad"><span class="mid">افزودن عضو</span></button></div>`:''}<div class="sec">اعضا</div>${ms.map(u=>`<button class="row" data-u="${u.id}">${av(u,44)}<div class="mid"><b>${esc(u.nick)}</b>${prb(u)} <span class="mu sm">${u.mrole==='owner'?'مالک':u.mrole==='admin'?'مدیر':''}</span></div></button>`).join('')}`,e=>{
  $$('[data-u]',e).forEach(b=>b.onclick=()=>showUser(+b.dataset.u));
  adm&&($('#ed',e).onclick=run(async()=>{const t2=await prompt2('عنوان',c.title);if(!t2)return;const a=await prompt2('توضیحات',c.about||'');await api('chats/'+c.id,'PUT',{title:t2,about:a});toast('ذخیره شد');sync()}),$('#ad',e).onclick=()=>pickUser(async u=>{await api(`chats/${c.id}/members`,'POST',{user_ids:[u.id]});toast('افزوده شد')}))})}
'''

CLIENT += r'''
/* ---------- global click router ---------- */
const ACT={
 back,tab:(el,k)=>{TAB=k;SEL.clear();ARCH=false;shell()},
 rules:()=>rulesView(),filt:(el,v)=>{FILT=v==='all'?null:+v;drawChats(true)},arch:()=>{ARCH=true;history.pushState({a:1},'');drawChats()},
 selx:()=>{SEL.clear();drawChats(true)},bulk:(el,a)=>bulk(a),fab,gsearch:globalSearch,addc:addContact,
 chat:(el,id)=>{if(lpFired){lpFired=false;return}if(SEL.size){SEL.has(+id)?SEL.delete(+id):SEL.add(+id);drawChats(true)}else openChat(+id)},
 ai:()=>aiPage(),ad:(el,id)=>{api('ads/'+id+'/clicks','POST').catch(()=>{});const l=el.dataset.link;if(l&&/^https?:\/\//.test(l))window.open(l,'_blank','noopener')},
 user:(el,id)=>showUser(+id),joinc:run(async(el,id)=>{await api('join','POST',{id:+id});await sync();back();later(()=>openChat(+id))}),
 setphoto:()=>pickFile('image/*',run(async f=>{const fd=new FormData();fd.append('file',f);const r=await fetch('/api/avatar',{method:'POST',headers:{'X-CSRF':CSRF},body:fd});if(!r.ok)throw new Error((await r.json()).error);const m=await api('me');ME=m.user;drawProfile();toast('تصویر تغییر کرد')})),
 editinfo:()=>SET.account(),addpost:()=>modal(`<textarea id="pt" rows="4" placeholder="چه خبر؟"></textarea><div style="display:flex;gap:10px;margin-top:12px"><button class="btn g" id="pf">${ic('cam')} تصویر</button><button class="btn" id="ps">انتشار</button></div><div id="pn" class="mu sm" style="margin-top:6px"></div>`,(e,c)=>{let file=null;$('#pf',e).onclick=()=>pickFile('image/*',f=>{file=f;$('#pn',e).textContent=f.name});$('#ps',e).onclick=run(async()=>{let fn=null;if(file){fn=(await up(file)).file}await api('posts','POST',{text:$('#pt',e).value,file:fn});c();drawProfile()})}),
 ptab:(el,v)=>{OTAB=+v;drawProfile()},parch:run(async(el,id)=>{await api('posts/'+id,'POST',{op:'archive'});drawProfile()}),pdel:run(async(el,id)=>{if(await ask('پست حذف شود؟','حذف')){await api('posts/'+id,'POST',{op:'delete'});drawProfile()}}),
 set:(el,k)=>SET[k](),sendgift:()=>pickUser(u=>giftShop(u)),wallet:walletPage,premium:premiumPage,logout:run(async()=>{if(await ask('از حساب خارج می‌شوید؟','خروج'))logout()}),
 /* chat */
 send:()=>{const ta=$('#inp');if(ta.value.trim()||CUR.edit)sendText();else CUR.rec?recStop():recStart()},
 emo:()=>{const p=$('#pnl');if(!p.hidden){p.hidden=true;return}p.hidden=false;p.innerHTML=`<div class="seg" style="margin:0"><button class="on" data-pt="e">ایموجی</button><button data-pt="s">استیکر</button></div><div class="panel" id="pc"></div>`;
  const show=k=>{$$('[data-pt]',p).forEach(b=>b.classList.toggle('on',b.dataset.pt===k));$('#pc').innerHTML=k==='e'?EMO.map(e=>`<span data-em="${e}">${e}</span>`).join(''):STK.map(e=>`<span data-st="${e}">${e}</span>`).join('')+PSTK.map(e=>`<span data-st="${e}" class="anim">${e}${ME.premium?'':'<small>🔒</small>'}</span>`).join('')};show('e');
  p.onclick=run(async ev=>{const b=ev.target.closest('[data-pt]'),e=ev.target.closest('[data-em]'),s=ev.target.closest('[data-st]');if(b)show(b.dataset.pt);if(e){const ta=$('#inp');ta.value+=e.dataset.em;ta.dispatchEvent(new Event('input'))}if(s){await api(`chats/${CUR.id}/messages`,'POST',{kind:'sticker',text:s.dataset.st,reply_to:CUR.reply&&CUR.reply.id});CUR.reply=null;bars();p.hidden=true;CUR.first=true;pollChat()}})},
 attach:()=>sheet([{t:'تصویر',i:'cam',f:()=>pickFile('image/*',f=>sendFile(f))},{t:'ویدیو',i:'cam',f:()=>pickFile('video/*',f=>sendFile(f))},{t:'فایل',i:'file',f:()=>pickFile('*/*',f=>sendFile(f))}]),
 mm:(el,id)=>{id=+id;if(CUR.sel.size){CUR.sel.has(id)?CUR.sel.delete(id):CUR.sel.add(id);$('#ch').innerHTML=chatHdr();renderMsgs()}else msgSheet(id)},
 rx:run(async(el,id,e)=>{event.stopPropagation();await api(`messages/${id}/react`,'POST',{emoji:e});pollChat()}),
 goto:(el,id)=>{event.stopPropagation();const t=$(`[data-id="${id}"]`);t&&t.scrollIntoView({block:'center',behavior:'smooth'})},
 csx:()=>{CUR.sel.clear();$('#ch').innerHTML=chatHdr();renderMsgs()},
 cs:run(async(el,a)=>{const ids=[...CUR.sel];if(a==='fwd')fwd(ids);else if(a==='copy'){navigator.clipboard&&navigator.clipboard.writeText(CUR.msgs.filter(m=>ids.includes(m.id)).map(m=>m.text).join('\n'));toast('کپی شد')}else if(a==='del'&&await ask('پیام‌های انتخاب‌شده حذف شوند؟','حذف')){for(const i of ids)await api('messages/'+i,'DELETE').catch(()=>{});CUR.sel.clear();pollChat()}}),
 rbx:()=>{CUR.reply=null;CUR.edit=null;CUR.ttl=CUR.at=null;const ta=$('#inp');if(ta){ta.value='';ta.dispatchEvent(new Event('input'))}bars()},
 cinfo:()=>CUR.c.type==='private'?showUser(CUR.c.peer.id):groupInfo(),csearch:()=>{const s=$('#sb');s.hidden=!s.hidden;if(!s.hidden)$('#sq').focus();else{CUR.search=null;pollFull()}},cmenu:chatMenu,
 cmute:run(async()=>{await api('chats/bulk','POST',{ids:[CUR.id],action:CUR.c.muted?'unmute':'mute'});await sync();toast('انجام شد')}),
 older:run(async()=>{const first=CUR.msgs[0];const d=await api(`chats/${CUR.id}/messages?before=${first.id}`);CUR.msgs=[...d.messages,...CUR.msgs];CUR.more=d.messages.length>=50;const b=$('#msgs'),h=b.scrollHeight;renderMsgs();b.scrollTop=b.scrollHeight-h}),
 ld:(el,f)=>{LOADED.add(f);renderMsgs()},
 img:(el,f)=>{event.stopPropagation();const o=document.createElement('div');o.className='ov';o.style.alignItems='center';o.innerHTML=`<img src="/f/${f}" style="max-width:96vw;max-height:92vh;border-radius:8px">`;o.onclick=()=>o.remove();document.body.append(o)}};
document.addEventListener('click',ev=>{const el=ev.target.closest('[data-a]');if(!el)return;const[a,...p]=el.dataset.a.split(':');const f=ACT[a];if(!f)return;if(el.closest('[data-lp]')&&a!=='chat')return;
 try{const r=f(el,...p);if(r&&r.catch)r.catch(e=>toast(e.message))}catch(e){toast(e.message)}});
window.addEventListener('online',()=>ME&&sync().catch(()=>{}));document.addEventListener('visibilitychange',()=>{if(!document.hidden&&ME)sync().catch(()=>{})});
boot();
</script></body></html>'''


ADMIN = r'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXO Admin</title>
<style>
:root{--bg:#0d0f1c;--s1:#161a2e;--s2:#1f2440;--s3:#2a3157;--ln:#ffffff18;--tx:#eef0ff;--mu:#9aa0c4;--ac:#5b8cff;--ok:#35d49a;--er:#ff6b7a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:Vazirmatn,Tahoma,system-ui,sans-serif}
button,input,textarea,select{font:inherit;color:inherit}input,textarea,select{background:var(--s2);border:1px solid var(--ln);border-radius:10px;padding:9px 12px;width:100%}
button{cursor:pointer;background:var(--s3);border:0;border-radius:10px;padding:8px 14px}button.p{background:linear-gradient(145deg,#5b8cff,#8a63f0);color:#fff}button.d{background:var(--er);color:#fff}
.top{display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--s1);border-bottom:1px solid var(--ln);position:sticky;top:0;z-index:5}.top b{font-size:1.2rem;letter-spacing:2px}
.tabs{display:flex;gap:6px;overflow-x:auto;padding:10px 12px;background:var(--s1)}.tabs button{white-space:nowrap}.tabs button.on{background:var(--ac);color:#fff}
.w{padding:14px;max-width:1100px;margin:auto}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}.c{background:var(--s1);border:1px solid var(--ln);border-radius:14px;padding:14px}.c b{font-size:1.5rem;display:block}.c span{color:var(--mu);font-size:.85rem}
.tw{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.86rem}td,th{padding:8px;border-bottom:1px solid var(--ln);text-align:start;white-space:nowrap}th{color:var(--mu)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;align-items:center}.row>input{flex:1;min-width:140px}.mu{color:var(--mu)}.tag{padding:2px 8px;border-radius:8px;background:var(--s3);font-size:.75rem}
.ov{position:fixed;inset:0;background:#000a;display:grid;place-items:center;z-index:20}.mdl{background:var(--s1);padding:18px;border-radius:16px;width:min(94vw,560px);max-height:88vh;overflow:auto}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--s3);padding:10px 18px;border-radius:12px;z-index:50}
</style></head><body><div id="app"></div>
<script>
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)],CSRF='__CSRF__';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fd=t=>t?new Date(t*1000).toLocaleString('fa-IR'):'—',nf=n=>Number(n||0).toLocaleString('fa-IR');
async function api(p,m='GET',b){const o={method:m,headers:{'X-CSRF':CSRF}};if(b!==undefined){o.headers['Content-Type']='application/json';o.body=JSON.stringify(b)}const r=await fetch('/api/'+p,o);const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'خطا');return d}
function toast(m){const e=document.createElement('div');e.className='toast';e.textContent=m;document.body.append(e);setTimeout(()=>e.remove(),2600)}
const run=f=>async(...a)=>{try{return await f(...a)}catch(e){toast(e.message)}};
function modal(h,m){const o=document.createElement('div');o.className='ov';o.innerHTML=`<div class="mdl">${h}</div>`;o.onclick=e=>{if(e.target===o)o.remove()};document.body.append(o);m&&m(o.firstChild,()=>o.remove());return o}
const ask=(msg,ph)=>new Promise(r=>modal(`<p>${esc(msg)}</p>${ph?`<input id="v" placeholder="${esc(ph)}">`:''}<div class="row"><button id="y" class="p">تأیید</button><button id="n">لغو</button></div>`,(e,c)=>{$('#y',e).onclick=()=>{const v=ph?$('#v',e).value:true;c();r(v)};$('#n',e).onclick=()=>{c();r(null)}}));
let ME=null,TAB='dash';const LV={user:0,mod:1,admin:2,owner:3},lv=()=>LV[ME.role]||0;
async function boot(){const d=await api('me');ME=d.user;if(!ME||lv()<1)return login();shell()}
function login(msg){$('#app').innerHTML=`<div class="w" style="max-width:380px;margin-top:12vh"><h2>ورود به پنل مدیریت NEXO</h2>${msg?`<p style="color:var(--er)">${esc(msg)}</p>`:''}<div class="row"><input id="u" placeholder="نام کاربری" dir="ltr"></div><div class="row"><input id="p" type="password" placeholder="رمز عبور" dir="ltr"></div><div class="row"><input id="c" placeholder="کد دومرحله‌ای (در صورت فعال بودن)" dir="ltr"></div><button class="p" id="go" style="width:100%">ورود</button></div>`;
 $('#go').onclick=run(async()=>{const d=await api('login','POST',{id:$('#u').value,password:$('#p').value,code:$('#c').value});if(d.need2fa)return toast('کد دومرحله‌ای را وارد کنید');if(LV[d.user.role]<1){await api('logout','POST');return login('شما مجوز ورود ندارید')}boot()})}
const TABS=[['dash','داشبورد',1],['users','کاربران',1],['content','محتوا',1],['ads','تبلیغات',1],['reports','گزارش‌ها',1],['gifts','هدایا',1],['ai','هوش مصنوعی',2],['sys','سیستم',2]];
function shell(){$('#app').innerHTML=`<div class="top"><b>NEXO Admin</b><span class="mu">${esc(ME.nick)} · ${ME.role}</span><span style="flex:1"></span><a href="/" style="color:var(--ac)">برنامه</a><button id="lo">خروج</button></div><div class="tabs">${TABS.filter(t=>lv()>=t[2]).map(([k,l])=>`<button data-t="${k}" class="${TAB===k?'on':''}">${l}</button>`).join('')}</div><div class="w" id="m"></div>`;
 $$('[data-t]').forEach(b=>b.onclick=()=>{TAB=b.dataset.t;shell()});$('#lo').onclick=run(async()=>{await api('logout','POST');login()});V[TAB]().catch(e=>toast(e.message))}
const tbl=(h,rows)=>`<div class="tw"><table><tr>${h.map(x=>`<th>${x}</th>`).join('')}</tr>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</table></div>`;
const V={
async dash(){const s=await api('admin/stats');const L={users:'کل کاربران',online:'آنلاین',messages:'پیام‌ها',groups:'گروه‌ها',channels:'کانال‌ها',premium:'کاربران Premium',stars:'Stars در گردش',ads:'تبلیغات',ads_pending:'تبلیغ در انتظار',ad_views:'بازدید تبلیغات',ai_requests:'درخواست‌های AI',ai_tokens:'توکن AI',reports:'گزارش باز',banned:'مسدود'};
 $('#m').innerHTML=`<div class="g">${Object.keys(L).map(k=>`<div class="c"><b>${nf(s[k])}</b><span>${L[k]}</span></div>`).join('')}</div>`},
async users(q=''){$('#m').innerHTML=`<div class="row"><input id="q" placeholder="جستجو: نام، یوزرنیم، شماره، شناسه" value="${esc(q)}"><button class="p" id="s">جستجو</button></div><div id="l"></div>`;const l=await api('admin/users?q='+encodeURIComponent(q));
 $('#l').innerHTML=tbl(['#','یوزرنیم','نام','نقش','Stars','Premium','وضعیت',''],l.map(u=>[u.id,'@'+esc(u.username),esc(u.nick),u.role,nf(u.stars),u.premium_until>Date.now()/1000?'✓':'—',u.banned?'<span class="tag" style="color:var(--er)">مسدود</span>':'فعال',`<button data-u="${u.id}">مدیریت</button>`]));
 $('#s').onclick=()=>V.users($('#q').value).catch(e=>toast(e.message));$$('[data-u]').forEach(b=>b.onclick=()=>userModal(+b.dataset.u))},
async content(){const l=await api('admin/chats');$('#m').innerHTML=tbl(['#','نوع','عنوان','یوزرنیم','اعضا','پیام‌ها','ایجاد',''],l.map(c=>[c.id,c.type==='channel'?'کانال':'گروه',esc(c.title),esc(c.username||'—'),nf(c.members),nf(c.msgs),fd(c.created),`<button class="d" data-c="${c.id}">حذف</button>`]))||'';
 $$('[data-c]').forEach(b=>b.onclick=run(async()=>{if(await ask('این گفتگو و تمام پیام‌هایش حذف شود؟')){await api('admin/chats/'+b.dataset.c,'DELETE');V.content()}}))},
async ads(){const l=await api('admin/ads');$('#m').innerHTML=tbl(['#','کاربر','عنوان','متن','وضعیت','نمایش','کلیک',''],l.map(a=>[a.id,esc(a.username),esc(a.title),esc(a.body),a.status,nf(a.views),nf(a.clicks),`${a.status!=='approved'?`<button class="p" data-o="approve" data-i="${a.id}">تأیید</button>`:''} ${a.status==='pending'?`<button data-o="reject" data-i="${a.id}">رد</button>`:''} <button class="d" data-o="delete" data-i="${a.id}">حذف</button>`]));
 $$('[data-o]').forEach(b=>b.onclick=run(async()=>{await api('admin/ads/'+b.dataset.i,'POST',{op:b.dataset.o});V.ads()}))},
async reports(){const l=await api('admin/reports');$('#m').innerHTML=tbl(['#','گزارش‌دهنده','نوع','هدف','دلیل','وضعیت','تاریخ',''],l.map(r=>[r.id,esc(r.username),r.kind,r.target,esc(r.reason),r.status,fd(r.created),r.status==='open'?`<button class="p" data-r="${r.id}">بستن</button>`:'']));
 $$('[data-r]').forEach(b=>b.onclick=run(async()=>{await api('admin/reports/'+b.dataset.r,'POST',{});V.reports()}))},
async gifts(){const l=await api('admin/gifts');$('#m').innerHTML=tbl(['','نام','قیمت','ویژه','کمیاب','فعال',''],l.map(g=>[g.emoji,esc(g.name),`<input type="number" value="${g.price}" id="p${g.id}" style="width:90px">`,g.premium_only?'✓':'—',g.rare?'✓':'—',`<input type="checkbox" id="a${g.id}" ${g.active?'checked':''} style="width:auto">`,`<button class="p" data-g="${g.id}">ذخیره</button>`]))+`<h3>هدیه جدید</h3><div class="row"><input id="ne" placeholder="ایموجی" style="max-width:90px"><input id="nn" placeholder="نام"><input id="np" type="number" placeholder="قیمت"><label><input type="checkbox" id="nx" style="width:auto"> ویژه Premium</label><label><input type="checkbox" id="nr" style="width:auto"> کمیاب</label><button class="p" id="add">افزودن</button></div>`;
 $$('[data-g]').forEach(b=>b.onclick=run(async()=>{const i=b.dataset.g;await api('admin/gifts','POST',{id:+i,price:+$('#p'+i).value,active:$('#a'+i).checked});toast('ذخیره شد')}));$('#add').onclick=run(async()=>{await api('admin/gifts','POST',{emoji:$('#ne').value,name:$('#nn').value,price:+$('#np').value,premium_only:$('#nx').checked,rare:$('#nr').checked});V.gifts()})},
async ai(){const a=await api('admin/ai');$('#m').innerHTML=`<div class="c"><div class="row"><label><input type="checkbox" id="en" ${a.enabled?'checked':''} style="width:auto"> NEXO AI فعال باشد</label></div><div class="row"><label style="min-width:130px">مدل پیش‌فرض</label><input id="md" dir="ltr" value="${esc(a.model)}"></div><div class="row"><label style="min-width:130px">سقف درخواست/ساعت</label><input id="rt" type="number" value="${a.rate}"></div><div class="row"><label style="min-width:130px">کلید OpenRouter</label><input id="ky" dir="ltr" placeholder="${esc(a.key||'تنظیم نشده')}"></div><label>مدل‌های قابل انتخاب (هر خط یک مدل)</label><textarea id="ml" rows="5" dir="ltr">${esc(a.models.join('\n'))}</textarea><div class="row"><button class="p" id="sv">ذخیره</button></div><p class="mu">کلید فقط روی سرور نگه‌داری می‌شود و هرگز به مرورگر ارسال نمی‌شود.</p></div><h3>مصرف توکن</h3>${tbl(['کاربر','درخواست','توکن'],a.usage.map(u=>[esc(u.username),nf(u.n),nf(u.tok)]))}`;
 $('#sv').onclick=run(async()=>{await api('admin/ai','POST',{enabled:$('#en').checked,model:$('#md').value,rate:+$('#rt').value,key:$('#ky').value,models:$('#ml').value.split('\n')});toast('ذخیره شد');V.ai()})},
async sys(){const s=await api('admin/system'),st=s.settings;$('#m').innerHTML=`<div class="c"><h3>تنظیمات عمومی</h3><div class="row"><label><input type="checkbox" id="ro" ${st.reg_open?'checked':''} style="width:auto"> ثبت‌نام باز است</label></div><div class="row"><label style="min-width:120px">هزینه تبلیغ (Stars)</label><input id="ac" type="number" value="${st.ad_cost}"></div>
 <div class="row">${Object.entries(st.plans).map(([k,p])=>`<label>${k}: روز <input data-pd="${k}" type="number" value="${p[0]}" style="width:80px"> قیمت <input data-pp="${k}" type="number" value="${p[1]}" style="width:90px"></label>`).join('')}</div><label>متن قوانین</label><textarea id="ru" rows="6">${esc(st.rules)}</textarea><div class="row"><button class="p" id="sv">ذخیره</button>${lv()>=3?'<button id="bk">پشتیبان‌گیری دیتابیس</button>':''}</div>${s.backups.map(b=>lv()>=3?`<div><a style="color:var(--ac)" href="/api/admin/backup/${encodeURIComponent(b)}">${esc(b)}</a></div>`:'').join('')}</div>
 <h3>فعالیت مدیران و رویدادهای امنیتی</h3>${tbl(['زمان','نوع','فرد','عملیات','جزئیات'],s.audit.map(a=>[fd(a.created),a.kind,esc(a.username||'—'),esc(a.action),esc(a.detail)]))}<h3>گزارش خطاها</h3>${tbl(['زمان','مسیر','خطا'],s.errors.map(e=>[fd(e.created),esc(e.path),`<pre style="margin:0;max-width:600px;overflow:auto">${esc(e.err)}</pre>`]))}`;
 $('#sv').onclick=run(async()=>{const plans={};$$('[data-pd]').forEach(i=>{const k=i.dataset.pd;plans[k]=[+i.value,+$(`[data-pp=${k}]`).value]});await api('admin/system','POST',{reg_open:$('#ro').checked,ad_cost:+$('#ac').value,rules:$('#ru').value,plans});toast('ذخیره شد')});
 const bk=$('#bk');if(bk)bk.onclick=run(async()=>{await api('admin/system','POST',{op:'backup'});toast('پشتیبان ساخته شد');V.sys()})}};
async function userModal(id){const d=await api('admin/users/'+id),u=d.user;
 modal(`<h3>${esc(u.nick)} <span class="mu">@${esc(u.username)} · #${u.id}</span></h3><p class="mu">نقش: ${u.role} · Stars: ${nf(u.stars)} · پیام‌ها: ${nf(d.messages)} · Premium تا: ${fd(u.premium_until)} · عضویت: ${fd(u.created)}<br>تلفن: ${esc(u.phone||'—')} · بیو: ${esc(u.bio||'—')}</p>
 <div class="row">${u.banned?'<button data-o="unban" class="p">رفع مسدودی</button>':'<button data-o="ban" class="d">مسدودسازی</button>'}${lv()>=2?'<button data-o="premium">اعطای Premium</button><button data-o="revoke">لغو Premium</button><button data-o="stars">افزودن/کسر Stars</button><button data-o="delete" class="d">حذف حساب</button>':''}${lv()>=3?'<button data-o="role">تغییر نقش</button>':''}</div>
 <h4>تراکنش‌های Stars</h4>${tbl(['مقدار','دلیل','مدیر','زمان'],d.tx.map(t=>[nf(t.delta),esc(t.reason),esc(t.admin||'—'),fd(t.created)]))}`,(e,c)=>$$('[data-o]',e).forEach(b=>b.onclick=run(async()=>{const op=b.dataset.o,body={op};
  if(op==='premium'){const v=await ask('تعداد روز؟','30');if(v===null)return;body.days=+v}if(op==='stars'){const v=await ask('مقدار (منفی = کسر)','100');if(v===null)return;body.amount=+v}if(op==='role'){const v=await ask('نقش: user | mod | admin','mod');if(v===null)return;body.role=v}
  if(op!=='unban'){const r=await ask('دلیل (الزامی و در گزارش ثبت می‌شود)','دلیل');if(!r)return toast('ثبت دلیل الزامی است');body.reason=r}
  await api('admin/users/'+id,'POST',body);toast('انجام شد');c();V.users().catch(()=>{})})))}
boot();
</script></body></html>'''


if SIO:
    pass

if __name__ == '__main__':
    threading.Thread(target=purge, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    print('\n  NEXO اجرا شد → http://127.0.0.1:%d   (مدیر اصلی: اولین کاربری که ثبت‌نام کند)' % port)
    print('  پنل مدیریت → http://127.0.0.1:%d/admin' % port)
    print('  Real-time: %s\n' % ('WebSocket (Flask-SocketIO)' if SIO else 'HTTP sync (برای WebSocket: pip install flask-socketio simple-websocket)'))
    if SIO: sio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
    else: app.run(host='0.0.0.0', port=port, threaded=True)
