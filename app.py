"""QuickBazaar - an OLX-style classifieds platform (Flask + SQLite) with Gmail OTP and a built-in chatbot."""
import os, re, sqlite3, json, uuid, smtplib, ssl, hmac, hashlib, secrets, time, threading, urllib.request, base64
from email.message import EmailMessage
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort, send_from_directory, Response)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
CLOUDFLARE = os.environ.get("CLOUDFLARE_WORKERS", "").lower() in ("1", "true", "yes")

class D1Row(dict):
    """Small sqlite3.Row-compatible mapping for the existing Flask templates/routes."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)
    def __iter__(self):
        return iter(self.values())

class D1Cursor:
    def __init__(self, result):
        self.result = result or {}
        self.rows = [D1Row(r) for r in (self.result.get("results") or [])]
        meta = self.result.get("meta") or {}
        self.rowcount = int(meta.get("changes") or 0)
        self.lastrowid = meta.get("last_row_id") or 0
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return list(self.rows)
    def __iter__(self):
        return iter(self.rows)

class D1DB:
    """Synchronous compatibility wrapper around Cloudflare's async D1 binding."""
    def __init__(self, binding):
        self.binding = binding
    def execute(self, sql, params=()):
        from pyodide.ffi import run_sync
        stmt = self.binding.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        return D1Cursor(run_sync(stmt.run()))
    def commit(self):
        # D1 is auto-commit; this keeps the existing Flask code unchanged.
        return None
    def close(self):
        return None

def cloud_env():
    return request.environ.get("workers.env") if CLOUDFLARE else None


def load_env(path):
    """Tiny .env loader (no extra dependency). Real environment variables always win."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1); k = k.strip(); v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'": v = v[1:-1]
                if k and k not in os.environ: os.environ[k] = v
    except FileNotFoundError:
        pass

load_env(os.path.join(BASE, ".env"))


def get_secret_key():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key and key not in ("replace-with-a-long-random-secret", "change-me-in-production"):
        return key
    if CLOUDFLARE:
        raise RuntimeError("Set SECRET_KEY as a Cloudflare Worker secret before deploying.")
    path = os.path.join(BASE, ".secret_key")
    try:
        with open(path) as fh:
            stored = fh.read().strip()
        if stored:
            return stored
    except FileNotFoundError:
        pass
    stored = secrets.token_hex(32)
    try:
        with open(path, "w") as fh:
            fh.write(stored)
    except OSError:
        pass
    return stored
app = Flask(__name__, static_folder=None if CLOUDFLARE else "static")
app.config.update(SECRET_KEY=get_secret_key(),
                  MAX_CONTENT_LENGTH=5 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes"))
DB = os.path.join(BASE, "quickbazaar.db")
ADMIN_EMAIL = os.environ.get("NOTIFY_EMAIL", "quickbazaarofficials@gmail.com")
UPLOADS = os.path.join(BASE, "uploads")
ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}
CATEGORIES = ["Mobiles", "Vehicles", "Property", "Electronics", "Furniture", "Fashion", "Bikes", "Jobs", "Services", "Other"]

# OTP rules
OTP_TTL = 600            # seconds an OTP stays valid
OTP_COOLDOWN = 30        # seconds before another OTP can be requested
OTP_MAX_ATTEMPTS = 5     # wrong guesses before the OTP is thrown away

# country code -> (name, required digits in national number)
COUNTRIES = {"+91": ("India", 10), "+1": ("USA / Canada", 10), "+44": ("United Kingdom", 10), "+61": ("Australia", 9),
             "+971": ("UAE", 9), "+966": ("Saudi Arabia", 9), "+65": ("Singapore", 8), "+92": ("Pakistan", 10),
             "+880": ("Bangladesh", 10), "+94": ("Sri Lanka", 9), "+977": ("Nepal", 10), "+49": ("Germany", 10)}
GMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@gmail\.com$")

def valid_phone(code, num):
    d = re.sub(r"[\s-]", "", num or "")
    if code not in COUNTRIES or not d.isdigit() or len(d) != COUNTRIES[code][1]: return None
    if code == "+91" and d[0] not in "6789": return None
    return f"{code} {d}"

def check_signup(f):
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,49}", f.get("name", "").strip()): return "Enter your full name (letters only, 2–50 characters)."
    if not GMAIL.match(f.get("email", "").strip()): return "Use a valid Gmail address ending in @gmail.com."
    if not valid_phone(f.get("cc"), f.get("phone")):
        n = COUNTRIES.get(f.get("cc"), ("", 0)); return f"Enter a valid {n[1]}-digit mobile number for {n[0] or 'your country'}" + (" (India numbers start with 6–9)." if f.get("cc") == "+91" else ".")
    pw = f.get("password", "")
    if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw): return "Password needs 8+ characters with at least one letter and one number."
    if pw != f.get("confirm"): return "Passwords don't match."
    if not f.get("terms"): return "Please accept the Terms & Conditions and Privacy Policy."

def validate_ad(f):
    if not 5 <= len(f.get("title", "").strip()) <= 80: return "Title must be 5–80 characters."
    if not 10 <= len(f.get("description", "").strip()) <= 2000: return "Description must be 10–2000 characters."
    if f.get("category") not in CATEGORIES: return "Choose a category."
    if not re.fullmatch(r"\d{1,9}", f.get("price", "")): return "Enter the price as a whole number (digits only)."
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,39}", f.get("city", "").strip()): return "Enter a valid city name."

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT, pw TEXT, verified INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS ads(id INTEGER PRIMARY KEY, user_id INT, title TEXT, description TEXT, price INT,
  category TEXT, city TEXT, image TEXT, status TEXT DEFAULT 'active', views INT DEFAULT 0, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS favorites(user_id INT, ad_id INT, PRIMARY KEY(user_id, ad_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, ad_id INT, sender_id INT, receiver_id INT, body TEXT,
  created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ad_images(id INTEGER PRIMARY KEY, ad_id INT, filename TEXT);
CREATE TABLE IF NOT EXISTS stored_images(id TEXT PRIMARY KEY, data TEXT NOT NULL, content_type TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS interests(id INTEGER PRIMARY KEY, ad_id INT, buyer_id INT, status TEXT DEFAULT 'pending', UNIQUE(ad_id, buyer_id));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, seller_id INT, reviewer_id INT, ad_id INT, rating INT, comment TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(seller_id, reviewer_id));
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, ad_id INT, user_id INT, reason TEXT);
CREATE TABLE IF NOT EXISTS otp_codes(id INTEGER PRIMARY KEY, email TEXT, purpose TEXT, code TEXT, expires_at REAL, attempts INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INT, kind TEXT, body TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, read INT DEFAULT 0);
"""

def db():
    if "db" not in g:
        if CLOUDFLARE:
            env = cloud_env()
            if env is None or not hasattr(env, "DB"):
                raise RuntimeError("Cloudflare D1 binding DB is not configured.")
            g.db = D1DB(env.DB)
        else:
            g.db = sqlite3.connect(DB)
            g.db.row_factory = sqlite3.Row
    return g.db
@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    if CLOUDFLARE:
        return
    os.makedirs(UPLOADS, exist_ok=True)
    with sqlite3.connect(DB) as c:
        c.executescript(SCHEMA)
        if "views" not in [r[1] for r in c.execute("PRAGMA table_info(ads)")]: c.execute("ALTER TABLE ads ADD COLUMN views INT DEFAULT 0")
        cols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
        if "verified" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN verified INT DEFAULT 0"); c.execute("UPDATE users SET verified=1")
        if "profile_pic" not in cols: c.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT")
        if "sent_at" not in [r[1] for r in c.execute("PRAGMA table_info(otp_codes)")]: c.execute("ALTER TABLE otp_codes ADD COLUMN sent_at REAL DEFAULT 0")
        c.execute("CREATE INDEX IF NOT EXISTS idx_otp_lookup ON otp_codes(email, purpose)")
def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "uid" not in session:
            flash("Log in to continue.", "err"); return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w

ADMIN_EMAILS = {x.strip().lower() for x in os.environ.get("ADMIN_EMAILS", ADMIN_EMAIL).split(",") if x.strip()}

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if "uid" not in session:
            flash("Log in to continue.", "err")
            return redirect(url_for("login", next=request.path))
        u = db().execute("SELECT email FROM users WHERE id=?", (session["uid"],)).fetchone()
        if not u or (u["email"] or "").lower() not in ADMIN_EMAILS:
            abort(403)
        return f(*a, **k)
    return w
@app.context_processor
def inject():
    u = None
    if "uid" in session:
        u = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    return dict(me=u, categories=CATEGORIES, countries=COUNTRIES, support_email=ADMIN_EMAIL, replay_splash=False, is_admin=bool(u and (u["email"] or "").lower() in ADMIN_EMAILS))

@app.before_request
def drop_stale_session():
    if "uid" in session and not db().execute("SELECT 1 FROM users WHERE id=?", (session["uid"],)).fetchone(): session.clear()

@app.template_filter("inr")
def inr(v): return f"₹{int(v or 0):,}"

def safe_next(nxt):
    """Only allow local, single-slash paths (blocks //evil.com and /\\evil.com open redirects)."""
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and "\n" not in nxt and "\r" not in nxt: return nxt
    return "/"

if CLOUDFLARE:
    @app.route("/static/<path:filename>", endpoint="static")
    def cloud_static(filename):
        from pyodide.ffi import run_sync
        env = cloud_env()
        if env is None or not hasattr(env, "ASSETS"):
            abort(404)
        asset_response = run_sync(env.ASSETS.fetch(f"https://assets.local/static/{filename}"))
        body = run_sync(asset_response.bytes())
        return Response(body, status=asset_response.status, headers=asset_response.headers)
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def friendly_error(e):
    code = getattr(e, "code", 500)
    msgs = {400: ("Something looks off", "That request couldn't be processed."),
            403: ("Not allowed", "You don't have access to this page. If you're a buyer, the seller must accept your request first."),
            404: ("Page not found", "The page or listing you're looking for doesn't exist or was removed."),
            413: ("File too large", "Uploads can be up to 5 MB in total. Please choose smaller photos.")}
    title, text = msgs.get(code, ("Error", "Something went wrong."))
    if request.path.startswith("/api/"): return jsonify(ok=False, error=text), code
    return render_template("error.html", code=code, title=title, text=text), code

# ---------- Browse ----------
@app.route("/")
def index():
    q = request.args.get("q", "").strip(); cat = request.args.get("category", "")
    city = request.args.get("city", "").strip(); lo = request.args.get("min", type=int)
    hi = request.args.get("max", type=int); sort = request.args.get("sort", "new")
    page = max(request.args.get("page", 1, type=int), 1)
    sql, args = " FROM ads WHERE status='active'", []
    if q: sql += " AND (title LIKE ? OR description LIKE ?)"; args += [f"%{q}%"] * 2
    if cat: sql += " AND category=?"; args.append(cat)
    if city: sql += " AND city LIKE ?"; args.append(f"%{city}%")
    if lo is not None: sql += " AND price>=?"; args.append(lo)
    if hi is not None: sql += " AND price<=?"; args.append(hi)
    order = {"new": "created DESC", "low": "price ASC", "high": "price DESC"}.get(sort, "created DESC")
    total = db().execute("SELECT COUNT(*)" + sql, args).fetchone()[0]
    ads = db().execute(f"SELECT *{sql} ORDER BY {order} LIMIT 12 OFFSET ?", args + [(page - 1) * 12]).fetchall()
    favs = set()
    if "uid" in session:
        favs = {r[0] for r in db().execute("SELECT ad_id FROM favorites WHERE user_id=?", (session["uid"],))}
    return render_template("index.html", ads=ads, total=total, page=page, pages=(total + 11) // 12, favs=favs)

@app.route("/ad/<int:ad_id>")
def ad(ad_id):
    a = db().execute("SELECT ads.*, users.name seller, users.phone FROM ads JOIN users ON users.id=ads.user_id WHERE ads.id=?", (ad_id,)).fetchone()
    if not a: abort(404)
    more = db().execute("SELECT * FROM ads WHERE category=? AND id!=? AND status='active' LIMIT 4", (a["category"], ad_id)).fetchall()
    if session.get("uid") != a["user_id"]: db().execute("UPDATE ads SET views=views+1 WHERE id=?", (ad_id,)); db().commit()
    rv = db().execute("SELECT reviews.*, users.name who FROM reviews JOIN users ON users.id=reviewer_id WHERE seller_id=? ORDER BY reviews.id DESC", (a["user_id"],)).fetchall()
    rating = round(sum(r["rating"] for r in rv) / len(rv), 1) if rv else None
    imgs = [r[0] for r in db().execute("SELECT filename FROM ad_images WHERE ad_id=?", (ad_id,))] or ([a["image"]] if a["image"] else [])
    interest = phone = None
    if "uid" in session:
        interest = db().execute("SELECT * FROM interests WHERE ad_id=? AND buyer_id=?", (ad_id, session["uid"])).fetchone()
        if session["uid"] == a["user_id"] or (interest and interest["status"] == "accepted"): phone = a["phone"]
    return render_template("ad.html", a=a, more=more, imgs=imgs, interest=interest, phone=phone, reviews=rv[:5], rating=rating, nrev=len(rv),
        can_review=bool(interest and interest["status"] == "accepted" and not any(r["reviewer_id"] == session["uid"] for r in rv)))

@app.route("/uploads/<name>")
def uploaded(name):
    if CLOUDFLARE:
        row = db().execute("SELECT data,content_type FROM stored_images WHERE id=?", (name,)).fetchone()
        if not row:
            abort(404)
        try:
            data = base64.b64decode(row["data"])
        except Exception:
            abort(404)
        return Response(data, mimetype=row["content_type"])
    return send_from_directory(UPLOADS, name)

# ---------- Email + OTP helpers ----------
_hits, _hits_lock = {}, threading.Lock()

def throttle(key, limit, window):
    """Tiny in-memory rate limiter. Returns False when `key` exceeded `limit` hits in `window` seconds."""
    now = time.time()
    with _hits_lock:
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 3600]: _hits.pop(k, None)
        q = [t for t in _hits.get(key, []) if now - t < window]
        if len(q) >= limit: _hits[key] = q; return False
        q.append(now); _hits[key] = q; return True

def client_ip(): return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()

def email_configured():
    if CLOUDFLARE:
        return bool(os.environ.get("MAILER_URL", "").strip() and os.environ.get("MAILER_TOKEN", "").strip())
    return bool(os.environ.get("SMTP_PASS", "").strip())

def dev_otp_mode():
    return os.environ.get("OTP_DEV_MODE", "").lower() in ("1", "true", "yes")

def send_email(to, subject, body):
    if CLOUDFLARE:
        mailer_url = os.environ.get("MAILER_URL", "").strip()
        mailer_token = os.environ.get("MAILER_TOKEN", "").strip()
        if not mailer_url or not mailer_token:
            app.logger.error("Cloud mail gateway is not configured (MAILER_URL / MAILER_TOKEN missing).")
            return False
        try:
            from workers import fetch
            import json as _json
            from pyodide.ffi import run_sync
            response = run_sync(fetch(mailer_url, method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=_json.dumps({
                    "token": mailer_token,
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "from": "quickbazaarofficials@gmail.com",
                    "app": "QuickBazaar",
                })))
            detail = run_sync(response.text())[:500]
            if int(response.status) not in range(200, 300):
                app.logger.error("Mail gateway rejected email to %s: HTTP %s %s", to, response.status, detail)
                return False
            try:
                result = _json.loads(detail) if detail else {}
                if result.get("ok") is False:
                    app.logger.error("Mail gateway reported failure for %s: %s", to, detail)
                    return False
            except Exception:
                pass
            return True
        except Exception as e:
            app.logger.error("Cloud mail gateway request to %s failed: %s", to, e)
            return False

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER", "quickbazaarofficials@gmail.com")
    password = os.environ.get("SMTP_PASS", "").replace(" ", "")
    sender = os.environ.get("SMTP_FROM", user)
    if not password:
        app.logger.warning("SMTP_PASS is not configured; email to %s was not sent.", to)
        return False
    try:
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = f"QuickBazaar <{sender}>"
        m["To"] = to
        m.set_content(body)
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) as smtp:
                smtp.login(user, password); smtp.send_message(m)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls(context=ctx); smtp.login(user, password); smtp.send_message(m)
        return True
    except Exception as e:
        app.logger.error("Email to %s failed: %s", to, e)
        return False

def send_email_async(to, subject, body):
    if CLOUDFLARE:
        return send_email(to, subject, body)
    threading.Thread(target=send_email, args=(to, subject, body), daemon=True).start()
def otp_digest(email, purpose, code):
    return hmac.new(app.config["SECRET_KEY"].encode(), f"{email}|{purpose}|{code}".encode(), hashlib.sha256).hexdigest()

def latest_otp(email, purpose):
    return db().execute("SELECT * FROM otp_codes WHERE email=? AND purpose=? ORDER BY id DESC LIMIT 1", (email, purpose)).fetchone()

def resend_wait(email, purpose):
    row = latest_otp(email, purpose)
    if not row or not row["sent_at"]: return 0
    elapsed = time.time() - row["sent_at"]
    return int(OTP_COOLDOWN - elapsed) + 1 if elapsed < OTP_COOLDOWN else 0

def issue_otp(email, purpose):
    """Create + email a fresh 6-digit OTP. Returns a dict: ok, message, retry_after, dev."""
    wait = resend_wait(email, purpose)
    if wait: return dict(ok=False, code="cooldown", retry_after=wait, message=f"Please wait {wait}s before requesting another OTP.")
    code = f"{secrets.randbelow(1000000):06d}"; now = time.time(); d = db()
    d.execute("DELETE FROM otp_codes WHERE email=? AND purpose=?", (email, purpose))
    d.execute("INSERT INTO otp_codes(email,purpose,code,expires_at,attempts,sent_at) VALUES(?,?,?,?,0,?)", (email, purpose, otp_digest(email, purpose, code), now + OTP_TTL, now)); d.commit()
    subject = "QuickBazaar email verification OTP" if purpose == "verify" else "QuickBazaar password reset OTP"
    body = (f"Your QuickBazaar OTP is {code}.\n\nIt is valid for {OTP_TTL // 60} minutes. If you did not request this, you can ignore this email.\n"
            "Never share this OTP with anyone - QuickBazaar staff will never ask for it.")
    if email_configured():
        if not send_email(email, subject, body):
            d.execute("DELETE FROM otp_codes WHERE email=? AND purpose=?", (email, purpose)); d.commit()
            return dict(ok=False, code="email_failed", retry_after=0, message="We couldn't send the email just now. Please check the address and try again in a moment.")
        return dict(ok=True, retry_after=OTP_COOLDOWN, dev=False, message=f"OTP sent to {email}.")
    if dev_otp_mode():
        app.logger.warning("[OTP_DEV_MODE] %s OTP for %s is %s", purpose, email, code)
        return dict(ok=True, retry_after=OTP_COOLDOWN, dev=True, message="Email isn't configured, so the OTP was printed in the server console (developer mode).")
    d.execute("DELETE FROM otp_codes WHERE email=? AND purpose=?", (email, purpose)); d.commit()
    return dict(ok=False, code="not_configured", retry_after=0, message="Email delivery isn't configured. Set SMTP_PASS (Gmail App Password) in .env and restart the app.")

def check_otp(email, purpose, code, consume=True):
    """Validate an OTP. Wrong guesses are counted; after OTP_MAX_ATTEMPTS the OTP is discarded. Returns (ok, message)."""
    code = re.sub(r"\D", "", code or "")
    row = latest_otp(email, purpose); d = db()
    if not row: return False, "No active OTP. Please request a new one."
    if row["expires_at"] < time.time():
        d.execute("DELETE FROM otp_codes WHERE id=?", (row["id"],)); d.commit(); return False, "This OTP has expired. Please request a new one."
    if len(code) != 6: return False, "Enter the 6-digit OTP."
    if not hmac.compare_digest(row["code"], otp_digest(email, purpose, code)):
        left = OTP_MAX_ATTEMPTS - (row["attempts"] or 0) - 1
        if left <= 0:
            d.execute("DELETE FROM otp_codes WHERE id=?", (row["id"],)); d.commit(); return False, "Too many wrong attempts. Please request a new OTP."
        d.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?", (row["id"],)); d.commit()
        return False, f"Incorrect OTP. {left} attempt{'s' if left != 1 else ''} left."
    if consume: d.execute("DELETE FROM otp_codes WHERE id=?", (row["id"],)); d.commit()
    return True, ""

def norm_email(v): return (v or "").lower().strip()
def bad_password(pw):
    if len(pw or "") < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw): return "Password needs 8+ characters with at least one letter and one number."

def otp_left(email, purpose):
    row = latest_otp(email, purpose)
    return max(0, int(row["expires_at"] - time.time())) if row else 0
app.jinja_env.globals["otp_left"] = otp_left

def set_notice(res): session["otp_notice"] = {"ok": res["ok"], "message": res["message"], "dev": res.get("dev", False)}

# ---------- Auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    f = request.form
    if request.method == "POST":
        err = check_signup(f); email = norm_email(f.get("email"))
        if not err and not throttle("reg:" + client_ip(), 10, 600): err = "Too many sign-up attempts. Please wait a few minutes."
        if not err:
            phone = valid_phone(f["cc"], f["phone"]); pwh = generate_password_hash(f["password"]); d = db()
            old = d.execute("SELECT id, verified FROM users WHERE email=?", (email,)).fetchone()
            if old and old["verified"]: err = "That email is already registered. Try logging in."
            elif old:   # abandoned, never-verified sign-up: let the owner finish it
                d.execute("UPDATE users SET name=?,phone=?,pw=? WHERE id=?", (f["name"].strip(), phone, pwh, old["id"])); d.commit()
            else:
                d.execute("INSERT INTO users(name,email,phone,pw) VALUES(?,?,?,?)", (f["name"].strip(), email, phone, pwh)); d.commit()
        if err: return render_template("auth.html", mode="register", f=f, error=err)
        set_notice(issue_otp(email, "verify"))
        return redirect(url_for("verify_otp", email=email))
    return render_template("auth.html", mode="register", f={})

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = norm_email(request.form.get("email")); pw = request.form.get("password", "")
        if not throttle(f"login:{client_ip()}:{email}", 8, 300):
            return render_template("auth.html", mode="login", f=request.form, error="Too many attempts. Please wait a few minutes and try again.")
        u = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u and check_password_hash(u["pw"], pw):
            if not u["verified"]:
                set_notice(issue_otp(u["email"], "verify")); return redirect(url_for("verify_otp", email=u["email"]))
            session.clear(); session["uid"] = u["id"]
            return redirect(safe_next(request.args.get("next", "/")))
        return render_template("auth.html", mode="login", f=request.form, error="Email or password is incorrect.")
    return render_template("auth.html", mode="login", f={})

@app.route("/verify", methods=["GET", "POST"])
def verify_otp():
    email = norm_email(request.values.get("email"))
    u = db().execute("SELECT verified FROM users WHERE email=?", (email,)).fetchone() if GMAIL.match(email) else None
    if not u: flash("We couldn't find that sign-up. Please create your account again.", "err"); return redirect(url_for("register"))
    if u["verified"]: flash("This email is already verified. Please log in.", "ok"); return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        ok, error = check_otp(email, "verify", request.form.get("code"))
        if ok:
            db().execute("UPDATE users SET verified=1 WHERE email=?", (email,)); db().commit()
            flash("Email verified successfully. You can log in now.", "ok"); return redirect(url_for("login"))
    return render_template("verify_sent.html", email=email, error=error, notice=session.pop("otp_notice", None), wait=resend_wait(email, "verify"), ttl=OTP_TTL)

@app.route("/resend", methods=["POST"])
def resend():   # no-JS fallback for "send a new OTP"
    email = norm_email(request.form.get("email"))
    u = db().execute("SELECT verified FROM users WHERE email=?", (email,)).fetchone()
    if u and not u["verified"] and throttle("otp:" + client_ip(), 10, 600): set_notice(issue_otp(email, "verify"))
    return redirect(url_for("verify_otp", email=email))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":     # no-JS fallback; the page normally calls /api/otp/send in real time
        email = norm_email(request.form.get("email"))
        if not GMAIL.match(email): return render_template("forgot_password.html", error="Enter a valid Gmail address ending in @gmail.com.", email=email)
        if db().execute("SELECT 1 FROM users WHERE email=? AND verified=1", (email,)).fetchone() and throttle("otp:" + client_ip(), 10, 600): set_notice(issue_otp(email, "reset"))
        return redirect(url_for("reset_password", email=email))
    return render_template("forgot_password.html", email=norm_email(request.args.get("email")))

def reset_token_ok(email):
    t = session.get("reset_ok") or {}
    return t.get("email") == email and t.get("exp", 0) > time.time()

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = norm_email(request.values.get("email"))
    if not GMAIL.match(email): return redirect(url_for("forgot_password"))
    ctx = dict(email=email, ttl=OTP_TTL)
    if request.method == "POST":
        pw = request.form.get("password", ""); confirm = request.form.get("confirm", "")
        err = bad_password(pw) or (None if pw == confirm else "Passwords don't match.")
        if not err and not reset_token_ok(email):          # OTP wasn't verified live (no-JS path): check it now
            ok, msg = check_otp(email, "reset", request.form.get("code"))
            if not ok: err = msg
        if err: return render_template("reset_password.html", error=err, verified=reset_token_ok(email), wait=resend_wait(email, "reset"), notice=None, **ctx)
        db().execute("UPDATE users SET pw=? WHERE email=?", (generate_password_hash(pw), email))
        db().execute("DELETE FROM otp_codes WHERE email=? AND purpose='reset'", (email,)); db().commit()
        session.pop("reset_ok", None)
        flash("Password reset successfully. Please log in with your new password.", "ok"); return redirect(url_for("login"))
    return render_template("reset_password.html", error=None, verified=reset_token_ok(email), wait=resend_wait(email, "reset"), notice=session.pop("otp_notice", None), **ctx)

# ----- Real-time (AJAX) OTP endpoints -----
def api_body():
    j = request.get_json(silent=True) or {}
    return norm_email(j.get("email")), (j.get("purpose") or "").strip(), str(j.get("code") or "")

@app.route("/api/otp/send", methods=["POST"])
def api_otp_send():
    email, purpose, _ = api_body()
    if purpose not in ("verify", "reset") or not GMAIL.match(email): return jsonify(ok=False, message="Enter a valid Gmail address ending in @gmail.com."), 400
    if not throttle("otp:" + client_ip(), 10, 600): return jsonify(ok=False, message="Too many OTP requests. Please wait a few minutes.", retry_after=60), 429
    u = db().execute("SELECT verified FROM users WHERE email=?", (email,)).fetchone()
    eligible = bool(u) and (bool(u["verified"]) if purpose == "reset" else not u["verified"])
    if not eligible:   # same answer either way, so this can't be used to discover which Gmail addresses have accounts
        return jsonify(ok=True, retry_after=OTP_COOLDOWN, ttl=OTP_TTL, message=f"If {email} has a QuickBazaar account, an OTP is on its way.")
    res = issue_otp(email, purpose)
    return jsonify({**res, "ttl": OTP_TTL}), (200 if res["ok"] else (429 if res.get("code") == "cooldown" else 503 if res.get("code") in ("email_failed", "not_configured") else 400))

@app.route("/api/otp/verify", methods=["POST"])
def api_otp_verify():
    email, purpose, code = api_body()
    if purpose not in ("verify", "reset") or not GMAIL.match(email): return jsonify(ok=False, message="Invalid request."), 400
    if not throttle(f"otpv:{client_ip()}:{email}", 30, 600): return jsonify(ok=False, message="Too many attempts. Please wait a few minutes."), 429
    ok, msg = check_otp(email, purpose, code)
    if not ok: return jsonify(ok=False, message=msg), 400
    if purpose == "verify":
        db().execute("UPDATE users SET verified=1 WHERE email=?", (email,)); db().commit()
        flash("Email verified successfully. You can log in now.", "ok")
        return jsonify(ok=True, message="Email verified!", redirect=url_for("login"))
    session["reset_ok"] = {"email": email, "exp": time.time() + OTP_TTL}
    return jsonify(ok=True, message="OTP verified. Choose a new password.")

@app.route("/api/check-email")
def api_check_email():
    email = norm_email(request.args.get("email"))
    if not GMAIL.match(email): return jsonify(valid=False, available=False, message="Use a valid Gmail address ending in @gmail.com.")
    if not throttle("chk:" + client_ip(), 40, 60): return jsonify(valid=True, available=True, message="")
    u = db().execute("SELECT verified FROM users WHERE email=?", (email,)).fetchone()
    taken = bool(u and u["verified"])
    return jsonify(valid=True, available=not taken, message="That Gmail is already registered. Try logging in." if taken else "Looks good.")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    if request.method == "POST":
        name = request.form.get("name", "").strip(); email = norm_email(request.form.get("email")); phone = valid_phone(request.form.get("cc"), request.form.get("phone"))
        def fail(msg): return render_template("profile.html", u=u, error=msg)
        if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,49}", name): return fail("Enter a valid full name.")
        if not GMAIL.match(email): return fail("Use a valid Gmail address ending in @gmail.com.")
        if not phone: return fail("Enter a valid mobile number.")
        if db().execute("SELECT id FROM users WHERE email=? AND id!=?", (email, u["id"])).fetchone(): return fail("That Gmail address is already in use.")
        pic = u["profile_pic"]
        f = request.files.get("profile_pic")
        if f and f.filename:
            n = save_image(f)
            if not n: return fail("Profile picture must be JPG, JPEG, PNG, WEBP or GIF.")
            pic = n
        newpw = request.form.get("password", ""); cp = request.form.get("confirm", "")
        if newpw:
            err = bad_password(newpw) or (None if newpw == cp else "New passwords don't match.")
            if err: return fail(err)
        d = db(); d.execute("UPDATE users SET name=?,phone=?,profile_pic=? WHERE id=?", (name, phone, pic, u["id"]))
        if newpw: d.execute("UPDATE users SET pw=? WHERE id=?", (generate_password_hash(newpw), u["id"]))
        if email != u["email"]:   # a new Gmail must be proven with an OTP before it can be used to log in
            d.execute("UPDATE users SET email=?, verified=0 WHERE id=?", (email, u["id"])); d.commit()
            session.clear(); set_notice(issue_otp(email, "verify"))
            flash("Profile saved. Please verify your new Gmail address with the OTP we just sent.", "ok")
            return redirect(url_for("verify_otp", email=email))
        d.commit(); flash("Profile updated successfully.", "ok"); return redirect(url_for("profile"))
    return render_template("profile.html", u=u)

# ---------- Admin ----------
@app.route("/admin")
@admin_required
def admin():
    d = db()
    stats = {
        "users": d.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "verified": d.execute("SELECT COUNT(*) FROM users WHERE verified=1").fetchone()[0],
        "ads": d.execute("SELECT COUNT(*) FROM ads").fetchone()[0],
        "active_ads": d.execute("SELECT COUNT(*) FROM ads WHERE status='active'").fetchone()[0],
        "reports": d.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
        "pending_requests": d.execute("SELECT COUNT(*) FROM interests WHERE status='pending'").fetchone()[0],
    }
    users = d.execute("SELECT id,name,email,phone,verified FROM users ORDER BY id DESC LIMIT 100").fetchall()
    ads = d.execute("""SELECT ads.*, users.name seller, users.email seller_email
                       FROM ads JOIN users ON users.id=ads.user_id
                       ORDER BY ads.id DESC LIMIT 100""").fetchall()
    reports = d.execute("""SELECT reports.*, ads.title, users.name reporter
                           FROM reports JOIN ads ON ads.id=reports.ad_id
                           JOIN users ON users.id=reports.user_id
                           ORDER BY reports.id DESC LIMIT 100""").fetchall()
    return render_template("admin.html", stats=stats, users=users, ads=ads, reports=reports)

@app.route("/admin/ad/<int:ad_id>/<action>", methods=["POST"])
@admin_required
def admin_ad_action(ad_id, action):
    d = db()
    if action == "remove":
        d.execute("UPDATE ads SET status='removed' WHERE id=?", (ad_id,))
    elif action == "restore":
        d.execute("UPDATE ads SET status='active' WHERE id=?", (ad_id,))
    elif action == "delete":
        if not d.execute("SELECT 1 FROM ads WHERE id=?", (ad_id,)).fetchone():
            abort(404)
        for table in ("ad_images", "favorites", "interests", "reviews", "reports", "messages"):
            d.execute(f"DELETE FROM {table} WHERE ad_id=?", (ad_id,))
        d.execute("DELETE FROM ads WHERE id=?", (ad_id,))
    else:
        abort(404)
    d.commit()
    flash("Admin action completed.", "ok")
    return redirect(url_for("admin"))

@app.route("/admin/report/<int:report_id>/resolve", methods=["POST"])
@admin_required
def admin_resolve_report(report_id):
    d = db()
    d.execute("DELETE FROM reports WHERE id=?", (report_id,))
    d.commit()
    flash("Report resolved.", "ok")
    return redirect(url_for("admin"))
@app.route("/logout")
def logout(): session.clear(); return redirect("/")

# ---------- "I'm interested" requests (phone shared only after seller accepts) ----------
@app.route("/interest/<int:ad_id>", methods=["POST"])
@login_required
def interest(ad_id):
    d = db()
    a = d.execute("SELECT ads.user_id, ads.title, ads.status, u.email seller_email FROM ads JOIN users u ON u.id=ads.user_id WHERE ads.id=?", (ad_id,)).fetchone() or abort(404)
    if a["user_id"] == session["uid"]: abort(400)
    if a["status"] != "active": flash("This item is no longer available.", "err"); return redirect(url_for("ad", ad_id=ad_id))
    buyer = d.execute("SELECT name FROM users WHERE id=?", (session["uid"],)).fetchone()
    cur = d.execute("INSERT OR IGNORE INTO interests(ad_id,buyer_id) VALUES(?,?)", (ad_id, session["uid"]))
    if cur.rowcount == 0: d.commit(); flash("You've already sent a request for this ad.", "ok"); return redirect(url_for("ad", ad_id=ad_id))
    d.execute("INSERT INTO notifications(user_id,kind,body) VALUES(?,?,?)", (a["user_id"], "interest", f"{buyer['name']} requested to contact you about {a['title']}"))
    d.commit()
    send_email_async(a["seller_email"], "New QuickBazaar buyer request", f"{buyer['name']} is interested in your ad: {a['title']}. Log in to QuickBazaar and open Requests to accept or decline the request.")
    send_email_async(ADMIN_EMAIL, "QuickBazaar request notification", f"Buyer {buyer['name']} sent a seller-contact request for ad '{a['title']}'.")
    flash("Request sent. The seller has been notified by email.", "ok"); return redirect(url_for("ad", ad_id=ad_id))

@app.route("/requests")
@login_required
def requests_page():
    rows = db().execute("""SELECT i.id, i.status, ads.id ad_id, ads.title, users.id buyer_id, users.name buyer FROM interests i
        JOIN ads ON ads.id=i.ad_id JOIN users ON users.id=i.buyer_id WHERE ads.user_id=? ORDER BY i.id DESC""", (session["uid"],)).fetchall()
    return render_template("requests.html", rows=rows)

@app.route("/requests/<int:rid>/<action>", methods=["POST"])
@login_required
def request_action(rid, action):
    if action not in ("accept", "decline"): abort(400)
    row = db().execute("SELECT i.*,a.title,buyer.email buyer_email,seller.name seller_name FROM interests i JOIN ads a ON a.id=i.ad_id JOIN users buyer ON buyer.id=i.buyer_id JOIN users seller ON seller.id=a.user_id WHERE i.id=? AND a.user_id=?", (rid, session["uid"])).fetchone()
    if not row: abort(404)
    status = "accepted" if action == "accept" else "declined"
    db().execute("UPDATE interests SET status=? WHERE id=?", (status, rid)); db().commit()
    msg = f"Your request for '{row['title']}' was {status} by {row['seller_name']}."
    send_email_async(row["buyer_email"], f"QuickBazaar request {status}", msg)
    send_email_async(ADMIN_EMAIL, f"QuickBazaar request {status}", msg)
    flash(f"Request {status}. The buyer has been notified by email.", "ok"); return redirect(url_for("requests_page"))

@app.route("/review/<int:ad_id>", methods=["POST"])
@login_required
def review(ad_id):
    i = db().execute("SELECT a.user_id FROM interests i JOIN ads a ON a.id=i.ad_id WHERE i.ad_id=? AND i.buyer_id=? AND i.status='accepted'", (ad_id, session["uid"])).fetchone()
    rating = request.form.get("rating", type=int)
    if not i or rating not in range(1, 6): abort(400)
    db().execute("INSERT OR IGNORE INTO reviews(seller_id,reviewer_id,ad_id,rating,comment) VALUES(?,?,?,?,?)", (i["user_id"], session["uid"], ad_id, rating, request.form.get("comment", "")[:300]))
    db().commit(); flash("Thanks for your review.", "ok"); return redirect(url_for("ad", ad_id=ad_id))

@app.route("/privacy")
def privacy(): return render_template("privacy.html")

@app.route("/terms")
def terms(): return render_template("terms.html")

# ---------- Post / manage ads ----------
# Cloudflare D1 has a 2 MB maximum row/string size. To keep the no-R2
# deployment reliable, individual uploaded images are capped below that limit.
CLOUD_IMAGE_MAX_BYTES = 1_350_000

def save_image(f):
    ext = (f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "")
    if ext not in ALLOWED:
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    content_type = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp","gif":"image/gif"}.get(ext, "application/octet-stream")
    if CLOUDFLARE:
        data = f.read()
        if len(data) > CLOUD_IMAGE_MAX_BYTES:
            raise ValueError("Each image must be 1.35 MB or smaller on the Cloudflare no-R2 plan. Please resize/compress the photo and try again.")
        encoded = base64.b64encode(data).decode("ascii")
        db().execute(
            "INSERT INTO stored_images(id,data,content_type) VALUES(?,?,?)",
            (name, encoded, content_type)
        )
        return name
    os.makedirs(UPLOADS, exist_ok=True)
    f.save(os.path.join(UPLOADS, secure_filename(name)))
    return name

def save_images(ad_id, files):
    first = None
    for f in [x for x in files if x and x.filename][:6]:
        try:
            n = save_image(f)
        except ValueError as e:
            flash(str(e), "err")
            continue
        if n:
            db().execute("INSERT INTO ad_images(ad_id,filename) VALUES(?,?)", (ad_id, n)); first = first or n
    return first

@app.route("/post", methods=["GET", "POST"])
@login_required
def post():
    if request.method == "POST":
        f = request.form
        err = validate_ad(f)
        if err: flash(err, "err"); return render_template("post.html", a=f)
        cur = db().execute("INSERT INTO ads(user_id,title,description,price,category,city,image) VALUES(?,?,?,?,?,?,?)",
                     (session["uid"], f["title"].strip(), f["description"].strip(), int(f["price"] or 0),
                      f["category"], f["city"].strip(), None))
        cover = save_images(cur.lastrowid, request.files.getlist("images"))
        if cover: db().execute("UPDATE ads SET image=? WHERE id=?", (cover, cur.lastrowid))
        db().commit(); flash("Your ad is live.", "ok"); return redirect(url_for("my_ads"))
    return render_template("post.html", a=None)

@app.route("/ad/<int:ad_id>/edit", methods=["GET", "POST"])
@login_required
def edit(ad_id):
    a = db().execute("SELECT * FROM ads WHERE id=? AND user_id=?", (ad_id, session["uid"])).fetchone() or abort(404)
    if request.method == "POST":
        f = request.form; err = validate_ad(f)
        if err: flash(err, "err"); return redirect(request.url)
        img = a["image"] or save_images(ad_id, request.files.getlist("images"))
        if a["image"]: save_images(ad_id, request.files.getlist("images"))
        db().execute("UPDATE ads SET title=?,description=?,price=?,category=?,city=?,image=? WHERE id=?",
                     (f["title"], f["description"], int(f["price"] or 0), f["category"], f["city"], img, ad_id))
        db().commit(); flash("Ad updated.", "ok"); return redirect(url_for("my_ads"))
    return render_template("post.html", a=a)

@app.route("/ad/<int:ad_id>/<action>", methods=["POST"])
@login_required
def ad_action(ad_id, action):
    d = db(); uid = session["uid"]
    if action not in ("delete", "sold", "report"): abort(404)
    if not d.execute("SELECT 1 FROM ads WHERE id=?", (ad_id,)).fetchone(): abort(404)
    if action == "delete":
        if d.execute("DELETE FROM ads WHERE id=? AND user_id=?", (ad_id, uid)).rowcount:
            for t in ("ad_images", "favorites", "interests"): d.execute(f"DELETE FROM {t} WHERE ad_id=?", (ad_id,))
            d.commit(); flash("Ad deleted.", "ok")
        return redirect(url_for("my_ads"))
    if action == "sold":
        d.execute("UPDATE ads SET status='sold' WHERE id=? AND user_id=?", (ad_id, uid)); d.commit(); flash("Marked as sold.", "ok"); return redirect(url_for("my_ads"))
    d.execute("INSERT INTO reports(ad_id,user_id,reason) VALUES(?,?,?)", (ad_id, uid, request.form.get("reason", "")[:300])); d.commit()
    flash("Thanks - we'll review this ad.", "ok"); return redirect(url_for("ad", ad_id=ad_id))

@app.route("/my-ads")
@login_required
def my_ads():
    ads = db().execute("SELECT * FROM ads WHERE user_id=? ORDER BY created DESC", (session["uid"],)).fetchall()
    return render_template("my_ads.html", ads=ads)

@app.route("/fav/<int:ad_id>", methods=["POST"])
@login_required
def fav(ad_id):
    d = db()
    if not d.execute("SELECT 1 FROM ads WHERE id=?", (ad_id,)).fetchone(): abort(404)
    ex = d.execute("SELECT 1 FROM favorites WHERE user_id=? AND ad_id=?", (session["uid"], ad_id)).fetchone()
    if ex: d.execute("DELETE FROM favorites WHERE user_id=? AND ad_id=?", (session["uid"], ad_id))
    else: d.execute("INSERT INTO favorites VALUES(?,?)", (session["uid"], ad_id))
    d.commit(); return jsonify(saved=not ex)

@app.route("/favorites")
@login_required
def favorites():
    ads = db().execute("SELECT ads.* FROM ads JOIN favorites f ON f.ad_id=ads.id WHERE f.user_id=?", (session["uid"],)).fetchall()
    return render_template("my_ads.html", ads=ads, saved=True)

# ---------- Buyer <-> seller messaging ----------
@app.route("/inbox")
@login_required
def inbox():
    u = session["uid"]
    rows = db().execute("""SELECT m.ad_id, ads.title, CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END other,
        MAX(m.id) last_id FROM messages m JOIN ads ON ads.id=m.ad_id WHERE m.sender_id=? OR m.receiver_id=?
        GROUP BY m.ad_id, other ORDER BY last_id DESC""", (u, u, u)).fetchall()
    out = []
    for r in rows:
        o = db().execute("SELECT name FROM users WHERE id=?", (r["other"],)).fetchone()
        last = db().execute("SELECT body FROM messages WHERE id=?", (r["last_id"],)).fetchone()
        out.append(dict(ad_id=r["ad_id"], title=r["title"], other=r["other"], name=o["name"], last=last["body"]))
    return render_template("inbox.html", threads=out)

@app.route("/chat/<int:ad_id>/<int:other>", methods=["GET", "POST"])
@login_required
def chat(ad_id, other):
    u = session["uid"]; a = db().execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone() or abort(404)
    if other == u: abort(400)
    def accepted(buyer): return db().execute("SELECT 1 FROM interests WHERE ad_id=? AND buyer_id=? AND status='accepted'", (ad_id, buyer)).fetchone()
    if u == a["user_id"]:
        if not accepted(other): abort(403)          # seller may only chat with buyers they accepted
    else:
        if other != a["user_id"] or not accepted(u): abort(403)   # buyer may only chat with this ad's seller, after acceptance
    if request.method == "POST":
        body = request.form.get("body", "").strip()[:1000]
        if body:
            db().execute("INSERT INTO messages(ad_id,sender_id,receiver_id,body) VALUES(?,?,?,?)", (ad_id, u, other, body)); db().commit()
        return redirect(request.url)
    msgs = db().execute("""SELECT * FROM messages WHERE ad_id=? AND ((sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?))
                           ORDER BY id""", (ad_id, u, other, other, u)).fetchall()
    o = db().execute("SELECT name FROM users WHERE id=?", (other,)).fetchone() or abort(404)
    return render_template("chat.html", a=a, msgs=msgs, other=o)

# ---------- Chatbot ----------
MENU = ["Find an item", "Sell something", "Safety tips", "Contact a seller"]
SUPPORT = ADMIN_EMAIL

def R(text, chips=None, ads=None, links=None, wait=None):
    session["bot"] = {"wait": wait}
    return {"text": text, "chips": MENU if chips is None else chips, "ads": ads or [], "links": links or []}

def bot_reply(text):
    t = re.sub(r"\s+", " ", text.lower().strip()); wait = (session.get("bot") or {}).get("wait")
    name = ""
    if "uid" in session:
        u = db().execute("SELECT name FROM users WHERE id=?", (session["uid"],)).fetchone()
        name = " " + u["name"].split()[0] if u else ""
    has = lambda p: re.search(p, t)
    if has(r"\b(otp|cvv|pin|card number|aadhaar|aadhar)\b") or (has(r"\bpassword\b") and has(r"\b(is|my password)\b") and not has(r"forgot|reset|change")):
        return R("For your safety, please don't share OTPs, PINs, card details or passwords here or with anyone. QuickBazaar will never ask for them.", ["Safety tips"])
    if has(r"^(hi+|hello+|hey+|hii+|hola|namaste|good (morning|afternoon|evening))\b"):
        return R(f"Hi{name}! 👋 How can I help you today?")
    if has(r"how are you|how r u"): return R("I'm doing great, thanks for asking! 😊 What can I help you with?")
    if has(r"^(thanks|thank you|thx|ty|ok thanks)\b"): return R("You're welcome! Is there anything else I can help with?", ["Find an item", "Sell something", "No, that's all"])
    if has(r"^(bye|goodbye|see you|no, that's all|no thanks)"): return R("Happy to help! Have a great day. 👋", [])
    if has(r"who are you|your name|are you (a )?(bot|human|real)"): return R("I'm Bazaar Buddy, QuickBazaar's virtual assistant. I'm a bot, not a person, so I can help with finding items, selling, safety and account questions.")
    if has(r"\b(human|agent|complain|grievance|helpdesk|customer care|support)\b"):
        return R(f"I'm a bot, so I can't resolve account disputes or complaints. Please email {SUPPORT} with your registered email and the ad link, and the team will follow up.", ["Safety tips"])
    if has(r"privacy|my data|personal data|policy"): return R("I can show you our Privacy Policy right here. It explains what information QuickBazaar collects, how it is used, and how seller contact privacy works.", links=[{"label": "🔒 Open Privacy Policy", "url": "/privacy"}])
    if has(r"terms|conditions|rules|prohibited|allowed to (sell|post)"): return R("Sure — I can open the Terms & Conditions here in the QuickBazaar popup. They cover accounts, listings, seller contact requests, safety and prohibited content.", links=[{"label": "📜 Open Terms & Conditions", "url": "/terms"}])
    if has(r"(legal advice|lawyer|sue |loan|invest|medical|medicine|tax advice)"): return R("I'm not able to give legal, financial or medical advice. For those, please consult a qualified professional.")
    if has(r"phone number|contact (the )?seller|call (the )?seller|interested|reach (the )?seller|\bchat\b|(give|share|send).*(number|phone)|contact a seller"):
        return R("Yep — seller numbers stay private. Open the item you like and tap “I'm interested”. Your request goes to the seller by email and in Requests. If they accept, the seller's number becomes visible for that ad and chat opens in your Inbox.", ["Find an item", "Safety tips"])
    if has(r"verif|confirmation (mail|email|link)|activation"): return R("After signing up, we'll send a 6-digit OTP to your Gmail. Enter it on the verification screen within 10 minutes. If it doesn't arrive, use “Send a new OTP” and check Spam.")
    if has(r"forgot|reset.*password"): return R("Forgot your password? Open the Forgot Password page, enter your Gmail, use the 6-digit OTP we email you, and choose a new password.", links=[{"label":"Forgot password","url":"/forgot-password"}])
    if has(r"photo|picture|image"): return R("Photo tips: use natural light, show every side and any damage, and add up to 6 photos. Clear photos get more replies.", ["Sell something", "Safety tips"])
    if has(r"\b(sell|selling|post (an |my )?ad|list my|upload)\b") and not has(r"\b(find|buy|looking)\b"):
        return R("Selling is easy: 1) Log in with your verified account 2) Tap “+ Sell” 3) Add title, price, city, description and photos 4) Publish. When a buyer sends “I'm interested”, review it under Requests. Mark the ad as Sold when done.", ["Photo tips", "Safety tips", "Find an item"])
    if has(r"safe|scam|fraud|payment|advance|trust|cheat"): return R("Stay safe: meet in a public place, inspect the item before paying, never pay in advance or share OTP/UPI PIN, and be wary of prices that look too good. Use “Report” on suspicious ads.", ["Contact a seller", "Sell something"], links=[{"label": "Terms & Conditions", "url": "/terms"}])
    if has(r"\b(fee|fees|charge|charges|commission|free)\b"): return R("Posting an ad is free on QuickBazaar. Never pay anyone who claims to be QuickBazaar staff and asks for money.")
    if has(r"categor"): return R("Categories: " + ", ".join(CATEGORIES) + ".")
    if has(r"\b(delete|remove|edit|mark.*sold)\b"): return R("Open “My ads” from the menu. There you can edit, mark as sold, or delete any of your listings.", ["Sell something"])
    # ----- conversational search (backed by the real ads table) -----
    return search_reply(t, wait, text)

SEARCH_STOP = {"find", "show", "me", "some", "something", "search", "for", "a", "an", "the", "any", "looking", "want", "buy", "ads", "ad", "need", "i", "am", "im",
               "item", "items", "to", "please", "plz", "pls", "can", "you", "have", "is", "there", "get", "give", "list", "my", "of", "and", "with", "cheap", "used",
               "second", "hand", "sale", "new", "under", "below", "above", "over", "less", "than", "more", "max", "min", "upto", "up", "in", "at", "from", "city", "budget", "rs", "inr"}
CAT_WORDS = {"phone": "Mobiles", "phones": "Mobiles", "mobile": "Mobiles", "mobiles": "Mobiles", "car": "Vehicles", "cars": "Vehicles", "vehicle": "Vehicles", "vehicles": "Vehicles",
             "bike": "Bikes", "bikes": "Bikes", "scooter": "Bikes", "scooters": "Bikes", "house": "Property", "flat": "Property", "flats": "Property", "property": "Property",
             "laptop": "Electronics", "laptops": "Electronics", "electronics": "Electronics", "furniture": "Furniture", "fashion": "Fashion", "job": "Jobs", "jobs": "Jobs",
             "service": "Services", "services": "Services"}
CATEGORY_ONLY = {"mobiles", "mobile", "phone", "phones", "vehicles", "vehicle", "bikes", "bike", "property", "electronics", "furniture", "fashion", "jobs", "job", "services", "service"}

def money(txt):
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(k|lakh|lakhs|lac|lacs)?", txt or "")
    if not m: return None
    n = float(m.group(1).replace(",", "") or 0); n *= {"k": 1000, "lakh": 100000, "lakhs": 100000, "lac": 100000, "lacs": 100000}.get((m.group(2) or ""), 1)
    return int(n)

def search_reply(t, wait, original):
    mx_m = re.search(r"(?:under|below|less than|max|upto|up to|within)\s*(?:rs\.?|inr|₹)?\s*([\d,.]+\s*(?:k|lakhs?|lacs?)?)", t)
    mn_m = re.search(r"(?:above|over|min|more than|at least)\s*(?:rs\.?|inr|₹)?\s*([\d,.]+\s*(?:k|lakhs?|lacs?)?)", t)
    mx = money(mx_m.group(1)) if mx_m else None; mn = money(mn_m.group(1)) if mn_m else None
    city_m = re.search(r"\b(?:in|at|near)\s+([a-z][a-z ]*?)(?=\s+(?:under|below|above|over|upto|up to|less|more|max|min|within)\b|$)", t)
    city = city_m.group(1).strip() if city_m else None
    core = re.sub(r"\b(?:under|below|less than|max|upto|up to|within|above|over|min|more than|at least)\b.*$", "", t)
    core = re.sub(r"\b(?:in|at|near)\s+[a-z ]+$", "", core)
    words = re.findall(r"[a-z0-9][a-z0-9+.-]*", core)
    cat = next((CAT_WORDS[w] for w in words if w in CAT_WORDS), None)
    cat = cat or next((c for c in CATEGORIES if c != "Other" and c.lower() in t), None)
    kws = [w for w in words if w not in SEARCH_STOP and w not in CATEGORY_ONLY]
    explicit = wait == "search" or re.search(r"\b(find|buy|looking|search|show|want|need|under|below|above|upto|cheap|used|second hand|for sale|any|available|have)\b", t) or cat or mx or mn
    if not explicit and (len(words) > 5 or not kws): return llm_fallback(original)       # not a product search
    if not (kws or cat or mx or mn or city):
        return R("Sure! What are you looking for? You can add a budget or city, like “laptop under 30000 in Pune”.", ["iPhone under 20000", "Bike in Hyderabad", "Sofa"], wait="search")
    def run(use_cat, joiner):
        sql, args = "SELECT * FROM ads WHERE status='active'", []
        if kws:
            parts = []
            for w in kws:
                w = w[:-1] if len(w) > 3 and w.endswith("s") else w          # sofas -> sofa
                parts.append("(title LIKE ? OR description LIKE ?)"); args += [f"%{w}%"] * 2
            sql += " AND (" + f" {joiner} ".join(parts) + ")"
        if use_cat and cat: sql += " AND category=?"; args.append(cat)
        if mx is not None: sql += " AND price<=?"; args.append(mx)
        if mn is not None: sql += " AND price>=?"; args.append(mn)
        if city: sql += " AND city LIKE ?"; args.append(f"%{city}%")
        return db().execute(sql + " ORDER BY created DESC LIMIT 5", args).fetchall()
    rows = run(True, "AND")
    if not rows and kws:   # no keyword match inside the category: look across all categories before giving up
        rows = run(False, "AND") or (run(False, "OR") if len(kws) > 1 else [])
    if rows: return R(f"Here's what I found ({len(rows)}):", ["Search again", "Safety tips"], ads=[dict(id=r["id"], title=r["title"], price=inr(r["price"]), city=r["city"]) for r in rows])
    if not explicit: return llm_fallback(original)
    return R("I couldn't find a matching ad right now. Try a broader keyword or a higher budget.", ["Search again", "Categories"], wait="search")

def llm_fallback(text):
    key = os.environ.get("ANTHROPIC_API_KEY")
    sorry = R("Sorry, I didn't quite get that. I can help with finding items, selling, safety, contacting sellers and account questions. Pick an option or rephrase.")
    if not key: return sorry
    try:
        body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 250,
            "system": ("You are Bazaar Buddy, the assistant for QuickBazaar, an online classifieds marketplace. Be brief, warm and accurate. "
                       "Only answer questions about using QuickBazaar or general safe buying/selling. Never invent policies, fees, features or contact details; "
                       f"if unsure, say so and point to {SUPPORT}. Never give legal, financial or medical advice. Never ask for or reveal personal data, passwords or OTPs. "
                       "Decline anything unrelated politely."),
            "messages": [{"role": "user", "content": text}]}).encode()
        if CLOUDFLARE:
            from workers import fetch
            from pyodide.ffi import run_sync
            resp = run_sync(fetch("https://api.anthropic.com/v1/messages", {
                "method": "POST",
                "headers": {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                "body": body,
            }))
            return R(run_sync(resp.json())["content"][0]["text"])
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return R(json.load(urllib.request.urlopen(req, timeout=15))["content"][0]["text"])
    except Exception: return sorry

@app.route("/api/chatbot", methods=["POST"])
def chatbot():
    msg = (request.get_json(silent=True) or {}).get("message", "")[:300].strip()
    return jsonify(bot_reply(msg) if msg else R("I'm listening. What would you like to know?"))

init_db()   # also runs under `flask run` / gunicorn, not only `python app.py`

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG", "1") == "1")
