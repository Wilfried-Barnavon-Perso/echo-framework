"""
================================================================================
MODULE : ECHO AUTH MANAGER
VERSION : 5.239.2.1
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-11-11
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.2.1: Politique anti-bruteforce paramétrable (délai inter-essais 0/5/10/20s universel, quota d'essais et verrouillage temporisé ou définitif pour les comptes non-enrôlés) et affichage temps réel sur login.html.
# 5.237.0.1: Correction de la boucle de redirection 302 (/ <-> /login), résolution SSOT resolve_safe_next vers ui.{ECHO_DOMAIN} alignée sur la CSP form-action de BunkerWeb, et propagation de next dans /enroll et /recovery.
# 5.236.2.1: Durcissement SELECT * dans resolve_session_state pour garantir la tolérance aux colonnes SQLite dynamiques.
# 5.236.1.1: Politique Dual-Tier des sessions SSO (sans confiance 1-24h vs terminal de confiance 168-8760h/éternel), colonnes SQLite is_trusted/expires_at, factorisation _render_login et resolve_session_state, invalidation cache par mtime et endpoint /api/session/status.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import os
import sqlite3
import httpx
import pyotp
import base64
import secrets
import qrcode
import io
import json
import time
from urllib.parse import urlparse
from fastapi import FastAPI, Request, Form, HTTPException, status, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.hash import argon2
from typing import Optional

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DB_PATH = os.environ.get("AUTH_DB_PATH", "/data/auth.db")
SECRET_KEY = os.environ.get("AUTH_SECRET_KEY", secrets.token_hex(32))
# Récupération du domaine parent pour le cookie SSO (ex: votre-domaine.public)
ECHO_DOMAIN = os.environ.get("ECHO_DOMAIN", "localhost")
SETTINGS_PATH = os.environ.get("AUTH_SETTINGS_PATH", "/app/auth-data/auth-settings.json")

UNTRUSTED_VALID_HOURS = (1, 2, 4, 8, 12, 24)
TRUSTED_VALID_HOURS = (168, 720, 2160, 4380, 8760, 0)
COOKIE_ETERNAL_MAX_AGE = 315360000  # 10 ans (persistance navigateur pour session éternelle de confiance)

LOGIN_RETRY_DELAY_VALID_SEC = (0, 5, 10, 20)
UNENROLLED_LOCKOUT_VALID_MIN = (-1, 1, 3, 10, 60, 180, 1440)
_ip_last_attempt: dict[str, int] = {}

_settings_cache = {}
_settings_cache_time = 0
_settings_mtime = 0

def get_auth_settings():
    global _settings_cache, _settings_cache_time, _settings_mtime
    if not os.path.exists(SETTINGS_PATH): 
        return {}
    try:
        current_mtime = os.path.getmtime(SETTINGS_PATH)
    except OSError:
        current_mtime = 0
    if _settings_cache and current_mtime == _settings_mtime and (time.time() - _settings_cache_time < 60):
        return _settings_cache
        
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f: 
            _settings_cache = json.load(f)
            _settings_cache_time = time.time()
            _settings_mtime = current_mtime
            return _settings_cache
    except Exception: 
        return {}

app = FastAPI(title="ECHO Auth Server", description="SSO & MFA IdP for ECHO Framework")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Les sessions sont gérées dans SQLite (table sessions) pour permettre à l'admin-manager
# de lister, révoquer ou tracer les connexions actives (cookies).

# ==============================================================================
# DATABASE LOGIC
# ==============================================================================
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                name TEXT,
                pass_hash TEXT,
                totp_secret TEXT,
                security_question TEXT,
                security_answer_hash TEXT,
                must_enroll INTEGER DEFAULT 1,
                temp_pass_expires INTEGER DEFAULT 0,
                last_enrollment INTEGER DEFAULT 0,
                failed_attempts INTEGER DEFAULT 0,
                last_attempt_at INTEGER DEFAULT 0,
                locked_until INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                email TEXT,
                created_at INTEGER,
                is_trusted INTEGER DEFAULT 0,
                expires_at INTEGER
            )
        """)
        for col_name, col_type in [
            ("temp_pass_expires", "INTEGER DEFAULT 0"),
            ("last_enrollment", "INTEGER DEFAULT 0"),
            ("failed_attempts", "INTEGER DEFAULT 0"),
            ("last_attempt_at", "INTEGER DEFAULT 0"),
            ("locked_until", "INTEGER DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        
        # Migrations pour sessions
        for col_name, col_type in [("ip_address", "TEXT"), ("os", "TEXT"), ("browser", "TEXT"), ("device", "TEXT"), ("is_trusted", "INTEGER DEFAULT 0"), ("expires_at", "INTEGER")]:
            try:
                cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
                
        conn.commit()

init_db()

def get_user(email: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        return cursor.fetchone()

def update_user_enrollment(email: str, pass_hash: str, totp_secret: str, security_question: str, security_answer_hash: str, name: str = None):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        current_time = int(time.time())
        cursor.execute("""
            UPDATE users 
            SET pass_hash = ?, totp_secret = ?, security_question = ?, security_answer_hash = ?, must_enroll = 0, last_enrollment = ?, name = ?, failed_attempts = 0, locked_until = 0
            WHERE email = ?
        """, (pass_hash, totp_secret, security_question, security_answer_hash, current_time, name, email))
        conn.commit()

def update_user_password(email: str, pass_hash: str):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET pass_hash = ? WHERE email = ?", (pass_hash, email))
        conn.commit()

# ==============================================================================
# CRYPTOGRAPHY LOGIC
# ==============================================================================
def hash_password(password: str) -> str:
    return argon2.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return argon2.verify(password, hashed)
    except Exception:
        return False

def generate_totp_secret() -> str:
    return pyotp.random_base32()

def verify_totp(secret: str, code: str) -> bool:
    settings = get_auth_settings()
    valid_window = int(settings.get("totp_tolerance", 1))
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=valid_window)

def generate_qr_base64(secret: str, email: str) -> str:
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=email, issuer_name="ECHO Auth")
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{img_b64}"

# ==============================================================================
# LOGIN SECURITY & GUARD UTILS
# ==============================================================================
def _extract_client_ip(request: Optional[Request]) -> str:
    """Extrait l'IP cliente réelle en tenant compte des reverse-proxies (BunkerWeb)."""
    if not request:
        return ""
    ip = request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.client.host if request.client else ""))
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    return ip or ""

def _format_duration_fr(seconds: int) -> str:
    """Formate une durée en secondes de manière lisible (ex: X min Ys, Xh Ym, Xs)."""
    if seconds <= 0:
        return "0s"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d}j {h}h" if h > 0 else f"{d}j"
    if h > 0:
        return f"{h}h {m}m" if m > 0 else f"{h}h"
    if m > 0:
        return f"{m}min {s}s" if s > 0 else f"{m}min"
    return f"{s}s"

def _format_sanction_label(lockout_min: int) -> str:
    """Retourne le libellé textuel de la sanction associée à un verrouillage."""
    mapping = {
        -1: "Verrouillage définitif (attente réinit. MFA par l'Admin)",
        1: "Temporisation de 1 minute",
        3: "Temporisation de 3 minutes",
        10: "Temporisation de 10 minutes",
        60: "Temporisation de 1 heure",
        180: "Temporisation de 3 heures",
        1440: "Temporisation de 24 heures"
    }
    if lockout_min in mapping:
        return mapping[lockout_min]
    if lockout_min < 0:
        return "Verrouillage définitif (attente réinit. MFA par l'Admin)"
    return f"Temporisation de {lockout_min} minutes"

def get_security_policy(settings: dict) -> tuple[int, int, int, str]:
    """Retourne (retry_delay_sec, max_attempts, lockout_min, sanction_label) avec clamping strict."""
    try:
        retry_delay = int(settings.get("login_retry_delay_sec", 5))
    except (ValueError, TypeError):
        retry_delay = 5
    if retry_delay not in LOGIN_RETRY_DELAY_VALID_SEC:
        retry_delay = 5

    try:
        max_attempts = int(settings.get("unenrolled_max_attempts", 5))
    except (ValueError, TypeError):
        max_attempts = 5
    if max_attempts < 1:
        max_attempts = 1
    elif max_attempts > 20:
        max_attempts = 20

    try:
        lockout_min = int(settings.get("unenrolled_lockout_min", 10))
    except (ValueError, TypeError):
        lockout_min = 10
    if lockout_min not in UNENROLLED_LOCKOUT_VALID_MIN:
        lockout_min = 10

    sanction_label = _format_sanction_label(lockout_min)
    return retry_delay, max_attempts, lockout_min, sanction_label

def check_login_guard(user, client_ip: str, now: int, settings: dict) -> dict:
    """Vérifie si la tentative de connexion est bloquée par un délai ou un verrouillage."""
    retry_delay, max_attempts, lockout_min, _ = get_security_policy(settings)

    if user is None:
        last_ts = _ip_last_attempt.get(client_ip, 0)
        if retry_delay > 0 and (now - last_ts) < retry_delay:
            rem = retry_delay - (now - last_ts)
            return {
                "blocked": True,
                "error": f"Veuillez patienter {rem}s avant un nouvel essai.",
                "cooldown_sec": rem,
                "lockout_sec": 0,
                "attempts_remaining": None
            }
        return {"blocked": False}

    user_keys = user.keys() if hasattr(user, "keys") else ()
    failed_att = int(user["failed_attempts"] or 0) if "failed_attempts" in user_keys else 0

    if user["must_enroll"] == 1:
        locked_until = int(user["locked_until"] or 0) if "locked_until" in user_keys else 0
        if locked_until == -1:
            return {
                "blocked": True,
                "error": "Compte verrouillé définitivement. Contactez l'administrateur pour réinitialiser le MFA.",
                "cooldown_sec": 0,
                "lockout_sec": -1,
                "attempts_remaining": 0
            }
        if locked_until > now:
            rem_lock = locked_until - now
            return {
                "blocked": True,
                "error": f"Compte temporairement verrouillé. Réessayez dans {_format_duration_fr(rem_lock)}.",
                "cooldown_sec": 0,
                "lockout_sec": rem_lock,
                "attempts_remaining": 0
            }
        if locked_until > 0 and locked_until <= now:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE email = ?", (user["email"],))
                conn.commit()
            failed_att = 0

    # Vérification délai inter-essais retry_delay > 0 (pour must_enroll == 1 ET must_enroll == 0)
    last_att = int(user["last_attempt_at"] or 0) if "last_attempt_at" in user_keys else 0
    if retry_delay > 0 and last_att > 0 and (now - last_att) < retry_delay:
        rem_cd = retry_delay - (now - last_att)
        att_rem = max(0, max_attempts - failed_att) if user["must_enroll"] == 1 else None
        return {
            "blocked": True,
            "error": f"Veuillez patienter {rem_cd}s entre chaque essai.",
            "cooldown_sec": rem_cd,
            "lockout_sec": 0,
            "attempts_remaining": att_rem
        }

    return {"blocked": False}

def record_login_attempt(user, client_ip: str, is_success: bool, now: int, settings: dict) -> dict:
    """Enregistre le résultat d'une tentative et met à jour les sanctions éventuelles."""
    retry_delay, max_attempts, lockout_min, sanction_label = get_security_policy(settings)

    if user is None:
        if len(_ip_last_attempt) > 2048:
            _ip_last_attempt.clear()
        _ip_last_attempt[client_ip] = now
        return {
            "error": "Identifiants incorrects.",
            "cooldown_sec": retry_delay,
            "lockout_sec": 0,
            "attempts_remaining": None
        }

    email = user["email"]
    user_keys = user.keys() if hasattr(user, "keys") else ()

    if is_success:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = 0, last_attempt_at = ? WHERE email = ?",
                (now, email)
            )
            conn.commit()
        return {"cooldown_sec": 0, "lockout_sec": 0, "attempts_remaining": None}

    if user["must_enroll"] == 0:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET last_attempt_at = ? WHERE email = ?",
                (now, email)
            )
            conn.commit()
        return {"cooldown_sec": retry_delay, "lockout_sec": 0, "attempts_remaining": None}

    # must_enroll == 1
    cur_failed = int(user["failed_attempts"] or 0) if "failed_attempts" in user_keys else 0
    cur_locked = int(user["locked_until"] or 0) if "locked_until" in user_keys else 0
    if cur_locked > 0 and cur_locked <= now:
        cur_failed = 0

    new_failed = cur_failed + 1
    if new_failed >= max_attempts:
        if lockout_min == -1:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE users SET failed_attempts = ?, last_attempt_at = ?, locked_until = -1 WHERE email = ?",
                    (new_failed, now, email)
                )
                conn.commit()
            return {
                "error": f"Identifiants incorrects ({max_attempts}/{max_attempts}). Compte verrouillé définitivement en attente de réinitialisation MFA par l'Admin.",
                "cooldown_sec": 0,
                "lockout_sec": -1,
                "attempts_remaining": 0
            }
        else:
            lock_sec = lockout_min * 60
            locked_until_ts = now + lock_sec
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE users SET failed_attempts = ?, last_attempt_at = ?, locked_until = ? WHERE email = ?",
                    (new_failed, now, locked_until_ts, email)
                )
                conn.commit()
            return {
                "error": f"Identifiants incorrects ({max_attempts}/{max_attempts}). Compte verrouillé ({sanction_label}).",
                "cooldown_sec": 0,
                "lockout_sec": lock_sec,
                "attempts_remaining": 0
            }
    else:
        rem_tries = max_attempts - new_failed
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET failed_attempts = ?, last_attempt_at = ?, locked_until = 0 WHERE email = ?",
                (new_failed, now, email)
            )
            conn.commit()
        return {
            "error": f"Identifiants incorrects ({rem_tries} essai(s) restant(s) avant {sanction_label.lower()}).",
            "cooldown_sec": retry_delay,
            "lockout_sec": 0,
            "attempts_remaining": rem_tries
        }

# ==============================================================================
# SESSION UTILS
# ==============================================================================
def create_session(email: str, request: Request = None, is_trusted: bool = False, expires_at: Optional[int] = None) -> str:
    session_id = secrets.token_urlsafe(32)
    ip_address = ""
    os_str = ""
    browser_str = ""
    device_str = ""
    now = int(time.time())
    
    if request:
        ip_address = _extract_client_ip(request)
            
        ua_string = request.headers.get("User-Agent", "")
        if ua_string:
            try:
                from user_agents import parse
                user_agent = parse(ua_string)
                os_str = f"{user_agent.os.family} {user_agent.os.version_string}".strip()
                browser_str = f"{user_agent.browser.family} {user_agent.browser.version_string}".strip()
                device_str = user_agent.device.family
                if device_str == "Other":
                    if user_agent.is_pc: device_str = "PC"
                    elif user_agent.is_tablet: device_str = "Tablet"
                    elif user_agent.is_mobile: device_str = "Mobile"
            except ImportError:
                pass

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Purge opportuniste des sessions expirées
        try:
            cursor.execute("DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute(
                "INSERT INTO sessions (session_id, email, created_at, ip_address, os, browser, device, is_trusted, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                (session_id, email, now, ip_address, os_str, browser_str, device_str, 1 if is_trusted else 0, expires_at)
            )
        except sqlite3.OperationalError:
            cursor.execute(
                "INSERT INTO sessions (session_id, email, created_at) VALUES (?, ?, ?)", 
                (session_id, email, now)
            )
        conn.commit()
    return session_id

def delete_session(session_id: str):
    if session_id:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()

def resolve_session_state(echo_auth_session: Optional[str] = None) -> Optional[dict]:
    if not echo_auth_session:
        return None
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (echo_auth_session,))
        row = cursor.fetchone()
        
    if not row:
        return None
        
    now = int(time.time())
    is_trusted = bool(row["is_trusted"]) if ("is_trusted" in row.keys() and row["is_trusted"]) else False
    expires_at = row["expires_at"] if "expires_at" in row.keys() else None

    if expires_at is None and not is_trusted:
        expires_at = int(row["created_at"]) + 86400

    if expires_at is not None and now >= int(expires_at):
        delete_session(echo_auth_session)
        return None

    return {
        "session_id": row["session_id"],
        "email": row["email"],
        "created_at": int(row["created_at"]),
        "is_trusted": is_trusted,
        "expires_at": int(expires_at) if expires_at is not None else None,
        "time_remaining_sec": (int(expires_at) - now) if expires_at is not None else None
    }

def get_current_user_email(echo_auth_session: Optional[str] = Cookie(None)):
    state = resolve_session_state(echo_auth_session)
    return state["email"] if state else None

# ==============================================================================
# FORWARD-AUTH ENDPOINT (BunkerWeb)
# ==============================================================================
def get_default_redirect_url() -> str:
    """Retourne l'URL applicative cible par défaut (Open WebUI)."""
    domain = ECHO_DOMAIN.strip().lower() if ECHO_DOMAIN else "localhost"
    if domain != "localhost":
        return f"https://ui.{domain}/"
    return "http://localhost:3000/"

def is_safe_url(url: str) -> bool:
    """Vérifie si l'URL de redirection (next) est sécurisée (Open Redirect Protection).
    Alignée strictement sur les origines autorisées par la CSP form-action de BunkerWeb
    (ui.*, am.*, my.*) et exclut tout chemin relatif ou auto-référence vers auth.*."""
    if not url or url.startswith("/"):
        return False
    
    parsed = urlparse(url)
    
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
        
    host = parsed.hostname.lower()
    domain = ECHO_DOMAIN.strip().lower() if ECHO_DOMAIN else "localhost"
    if domain == "localhost":
        return host in ("localhost", "ui.localhost", "am.localhost", "my.localhost")
    return host in (f"ui.{domain}", f"am.{domain}", f"my.{domain}")

def resolve_safe_next(url: Optional[str]) -> str:
    """Résout l'URL de redirection post-authentification ou replie vers ui.{ECHO_DOMAIN}."""
    if url and is_safe_url(url):
        return url
    return get_default_redirect_url()

@app.get("/logout")
async def logout_sso(next: Optional[str] = None, echo_auth_session: Optional[str] = Cookie(None)):
    """Endpoint de déconnexion globale du SSO."""
    if echo_auth_session:
        # 1. Invalider la session interne d'Open WebUI pour éviter les collisions (Erreur 500)
        # Transmet le cookie session au backend OWUI pour forcer la révocation DB
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(
                    "http://echo-open-webui:8080/api/v1/auths/signout",
                    headers={"Cookie": f"echo_auth_session={echo_auth_session}"}
                )
        except Exception:
            pass  # Non bloquant : on continue la déconnexion SSO même si OWUI est injoignable
            
        # 2. Supprimer la session SSO
        delete_session(echo_auth_session)
        
    safe_next = resolve_safe_next(next)
    from urllib.parse import quote
    redirect_url = f"/login?next={quote(safe_next)}"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    
    cookie_domain = f".{os.environ.get('ECHO_DOMAIN', 'localhost')}" if os.environ.get('ECHO_DOMAIN', 'localhost') != "localhost" else None
    response.delete_cookie(
        key="echo_auth_session",
        domain=cookie_domain,
        httponly=True,
        secure=True,
        samesite="none"
    )
    return response

@app.api_route("/api/verify", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def verify_auth(echo_auth_session: Optional[str] = Cookie(None)):
    """Endpoint de Forward-Auth appelé par BunkerWeb."""
    if not echo_auth_session:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    email = get_current_user_email(echo_auth_session)
    if email:
        user = get_user(email)
        # Utilisateur authentifié : On injecte l'entête
        response = Response(status_code=200)
        response.headers["X-Webui-User"] = email
        if user and "name" in user.keys() and user["name"]:
            response.headers["X-Webui-Name"] = user["name"]
        else:
            response.headers["X-Webui-Name"] = email.split('@')[0]
        response.headers["X-Echo-Sso-Secret"] = os.environ.get("ECHO_SSO_SECRET", "")
        return response
    
    # Non authentifié : Session expiré ou utilisateur révoqué
    raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/api/session/status")
def session_status(echo_auth_session: Optional[str] = Cookie(None)):
    """Renvoie les métadonnées d'expiration et de compte à rebours de la session active."""
    state = resolve_session_state(echo_auth_session)
    if not state:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "authenticated": True,
        "email": state["email"],
        "is_trusted": state["is_trusted"],
        "created_at": state["created_at"],
        "expires_at": state["expires_at"],
        "time_remaining_sec": state["time_remaining_sec"]
    }

# ==============================================================================
# PAGES UI & LOGIQUE METIER
# ==============================================================================
def _render_login(
    request: Request,
    next_url: Optional[str] = None,
    error: Optional[str] = None,
    success: Optional[str] = None,
    email_prefill: Optional[str] = None,
    cooldown_sec: int = 0,
    lockout_sec: int = 0,
    attempts_remaining: Optional[int] = None
):
    safe_next = resolve_safe_next(next_url)
    settings = get_auth_settings()
    untrusted_timeout = int(settings.get("untrusted_session_timeout", 24))
    if untrusted_timeout not in UNTRUSTED_VALID_HOURS:
        untrusted_timeout = 24
    trusted_timeout = int(settings.get("session_timeout", 2160))
    if trusted_timeout not in TRUSTED_VALID_HOURS:
        trusted_timeout = 2160

    retry_delay_sec, max_attempts, lockout_min, sanction_label = get_security_policy(settings)

    ctx = {
        "request": request,
        "next": safe_next,
        "untrusted_timeout": untrusted_timeout,
        "trusted_timeout": trusted_timeout,
        "email_prefill": email_prefill or "",
        "retry_delay_sec": retry_delay_sec,
        "cooldown_sec": cooldown_sec,
        "lockout_sec": lockout_sec,
        "attempts_remaining": attempts_remaining,
        "max_attempts": max_attempts,
        "sanction_label": sanction_label
    }
    if error:
        ctx["error"] = error
    if success:
        ctx["success"] = success
    return templates.TemplateResponse(request=request, name="login.html", context=ctx)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, echo_auth_session: Optional[str] = Cookie(None), next: Optional[str] = None):
    safe_next = resolve_safe_next(next)
    email = get_current_user_email(echo_auth_session)
    if email:
        return RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    return _render_login(request, next_url=safe_next)

@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    totp: Optional[str] = Form(None),
    trusted_device: Optional[str] = Form(None),
    session_duration: Optional[int] = Form(None),
    next: Optional[str] = Form(None)
):
    safe_next = resolve_safe_next(next)
    now = int(time.time())
    settings = get_auth_settings()
    client_ip = _extract_client_ip(request)
    user = get_user(email)

    guard = check_login_guard(user, client_ip, now, settings)
    if guard.get("blocked"):
        return _render_login(
            request,
            next_url=safe_next,
            error=guard.get("error"),
            email_prefill=email,
            cooldown_sec=guard.get("cooldown_sec", 0),
            lockout_sec=guard.get("lockout_sec", 0),
            attempts_remaining=guard.get("attempts_remaining")
        )

    if not user or not verify_password(password, user["pass_hash"]):
        attempt_res = record_login_attempt(user, client_ip, False, now, settings)
        return _render_login(
            request,
            next_url=safe_next,
            error=attempt_res.get("error", "Identifiants incorrects."),
            email_prefill=email,
            cooldown_sec=attempt_res.get("cooldown_sec", 0),
            lockout_sec=attempt_res.get("lockout_sec", 0),
            attempts_remaining=attempt_res.get("attempts_remaining")
        )

    if user["must_enroll"] == 1:
        # Vérification de l'expiration du mot de passe temporaire
        if user["temp_pass_expires"] and user["temp_pass_expires"] > 0:
            if now > user["temp_pass_expires"]:
                return _render_login(request, next_url=safe_next, error="Mot de passe temporaire expiré. Contactez l'administrateur.", email_prefill=email)

        record_login_attempt(user, client_ip, True, now, settings)

        # Redirection vers l'enrôlement
        totp_secret = generate_totp_secret()
        qr_b64 = generate_qr_base64(totp_secret, email)
        
        return templates.TemplateResponse(request=request, name="enroll.html", context={
            "request": request, 
            "email": email, 
            "totp_secret": totp_secret,
            "qr_code_b64": qr_b64,
            "next": safe_next,
            "pwd_min_length": settings.get("pwd_min_length", 12),
            "pwd_require_upper": settings.get("pwd_require_upper", True),
            "pwd_require_lower": settings.get("pwd_require_lower", True),
            "pwd_require_digit": settings.get("pwd_require_digit", True),
            "pwd_require_special": settings.get("pwd_require_special", True)
        })

    # Utilisateur normal, vérification MFA
    if not totp or not verify_totp(user["totp_secret"], totp):
        attempt_res = record_login_attempt(user, client_ip, False, now, settings)
        return _render_login(
            request,
            next_url=safe_next,
            error="Code MFA invalide.",
            email_prefill=email,
            cooldown_sec=attempt_res.get("cooldown_sec", 0),
            lockout_sec=attempt_res.get("lockout_sec", 0),
            attempts_remaining=attempt_res.get("attempts_remaining")
        )

    # Succès
    record_login_attempt(user, client_ip, True, now, settings)
    is_trusted = str(trusted_device).lower() in ("1", "on", "true")

    if is_trusted:
        admin_max_h = int(settings.get("session_timeout", 2160))
        if admin_max_h not in TRUSTED_VALID_HOURS:
            admin_max_h = 2160
        req_h = session_duration if (session_duration is not None and session_duration in TRUSTED_VALID_HOURS) else admin_max_h
        if admin_max_h == 0:
            duration_h = req_h
        else:
            duration_h = admin_max_h if (req_h == 0 or req_h > admin_max_h) else req_h

        if duration_h == 0:
            expires_at = None
            max_age = COOKIE_ETERNAL_MAX_AGE
        else:
            max_age = duration_h * 3600
            expires_at = now + max_age
    else:
        untrusted_max_h = int(settings.get("untrusted_session_timeout", 24))
        if untrusted_max_h not in UNTRUSTED_VALID_HOURS:
            untrusted_max_h = 24
        req_h = session_duration if (session_duration is not None and session_duration in UNTRUSTED_VALID_HOURS) else untrusted_max_h
        duration_h = min(req_h, untrusted_max_h)
        max_age = duration_h * 3600
        expires_at = now + max_age

    session_id = create_session(email, request, is_trusted=is_trusted, expires_at=expires_at)
    
    # [FIX] User requested a delay to prevent SSO race conditions
    import asyncio
    await asyncio.sleep(1.5)
    
    response = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    
    # Configuration Cross-Domain pour auth.DOMAINE lisible par ui.DOMAINE
    cookie_domain = f".{ECHO_DOMAIN}" if ECHO_DOMAIN != "localhost" else None
    response.set_cookie(key="echo_auth_session", value=session_id, domain=cookie_domain, max_age=max_age, httponly=True, secure=True, samesite="none")
    return response

@app.post("/enroll", response_class=HTMLResponse)
async def enroll_post(
    request: Request, 
    email: str = Form(...), 
    totp_secret: str = Form(...), 
    new_password: str = Form(...), 
    totp_code: str = Form(...),
    security_question: str = Form(...),
    security_answer: str = Form(...),
    name: Optional[str] = Form(None),
    next: Optional[str] = Form(None)
):
    safe_next = resolve_safe_next(next)
    user = get_user(email)
    if not user or user["must_enroll"] == 0:
        return _render_login(request, next_url=safe_next, error="Requête invalide.")

    settings = get_auth_settings()
    # Revalidation des paramètres de template pour le renvoi d'erreur
    ctx = {
        "request": request, "email": email, "totp_secret": totp_secret, 
        "qr_code_b64": generate_qr_base64(totp_secret, email),
        "next": safe_next,
        "pwd_min_length": settings.get("pwd_min_length", 12),
        "pwd_require_upper": settings.get("pwd_require_upper", True),
        "pwd_require_lower": settings.get("pwd_require_lower", True),
        "pwd_require_digit": settings.get("pwd_require_digit", True),
        "pwd_require_special": settings.get("pwd_require_special", True)
    }

    if not verify_totp(totp_secret, totp_code):
        ctx["error"] = "Code MFA invalide. Veuillez réessayer."
        return templates.TemplateResponse(request=request, name="enroll.html", context=ctx)

    # Validation backend stricte du mot de passe
    if len(new_password) < int(ctx["pwd_min_length"]):
        ctx["error"] = f"Le mot de passe doit faire au moins {ctx['pwd_min_length']} caractères."
        return templates.TemplateResponse(request=request, name="enroll.html", context=ctx)
    if ctx["pwd_require_upper"] and not any(c.isupper() for c in new_password):
        ctx["error"] = "Le mot de passe doit contenir au moins une majuscule."
        return templates.TemplateResponse(request=request, name="enroll.html", context=ctx)
    if ctx["pwd_require_lower"] and not any(c.islower() for c in new_password):
        ctx["error"] = "Le mot de passe doit contenir au moins une minuscule."
        return templates.TemplateResponse(request=request, name="enroll.html", context=ctx)
    if ctx["pwd_require_digit"] and not any(c.isdigit() for c in new_password):
        ctx["error"] = "Le mot de passe doit contenir au moins un chiffre."
        return templates.TemplateResponse(request=request, name="enroll.html", context=ctx)
    if ctx["pwd_require_special"] and not any(not c.isalnum() for c in new_password):
        ctx["error"] = "Le mot de passe doit contenir au moins un caractère spécial."
        return templates.TemplateResponse(request=request, name="enroll.html", context=ctx)

    # Sauvegarde
    pass_hash = hash_password(new_password)
    ans_hash = hash_password(security_answer.lower().strip())
    update_user_enrollment(email, pass_hash, totp_secret, security_question, ans_hash, name)
    
    return _render_login(request, next_url=safe_next, success="Enrôlement réussi. Vous pouvez vous connecter.")

@app.get("/recovery", response_class=HTMLResponse)
async def recovery_page(request: Request, next: Optional[str] = None):
    safe_next = resolve_safe_next(next)
    return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "next": safe_next})

@app.post("/recovery/step1", response_class=HTMLResponse)
async def recovery_step1(request: Request, email: str = Form(...), next: Optional[str] = Form(None)):
    safe_next = resolve_safe_next(next)
    user = get_user(email)
    if not user or user["must_enroll"] == 1:
        # Fausse sécurité pour éviter l'énumération
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "next": safe_next, "error": "Compte introuvable ou non éligible."})
    
    return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "email": email, "question": user["security_question"], "next": safe_next})

@app.post("/recovery/step2", response_class=HTMLResponse)
async def recovery_step2(
    request: Request, 
    email: str = Form(...), 
    security_answer: str = Form(...), 
    totp_code: str = Form(...), 
    new_password: str = Form(...),
    next: Optional[str] = Form(None)
):
    safe_next = resolve_safe_next(next)
    user = get_user(email)
    if not user:
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "next": safe_next, "error": "Compte introuvable."})

    # Validation Answer
    if not verify_password(security_answer.lower().strip(), user["security_answer_hash"]):
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "email": email, "question": user["security_question"], "next": safe_next, "error": "Réponse de sécurité incorrecte."})

    # Validation MFA
    if not verify_totp(user["totp_secret"], totp_code):
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "email": email, "question": user["security_question"], "next": safe_next, "error": "Code MFA invalide."})

    # Succès
    pass_hash = hash_password(new_password)
    update_user_password(email, pass_hash)
    
    return _render_login(request, next_url=safe_next, success="Mot de passe réinitialisé avec succès.")

# Route racine de fallback (évite les 404 si safe_next retombe sur "/")
@app.get("/")
async def root_redirect(echo_auth_session: Optional[str] = Cookie(None)):
    if get_current_user_email(echo_auth_session):
        return RedirectResponse(url=get_default_redirect_url(), status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("auth_server:app", host="0.0.0.0", port=8000, reload=True)
