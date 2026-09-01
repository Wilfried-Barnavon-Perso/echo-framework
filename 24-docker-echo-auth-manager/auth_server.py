"""
================================================================================
MODULE : ECHO AUTH MANAGER
VERSION : 1.4 (Invalidation proactive session OWUI)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-09-01
================================================================================
"""
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

def get_auth_settings():
    if not os.path.exists(SETTINGS_PATH): return {}
    try:
        with open(SETTINGS_PATH, 'r') as f: return json.load(f)
    except: return {}

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
                last_enrollment INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                email TEXT,
                created_at INTEGER
            )
        """)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN temp_pass_expires INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN last_enrollment INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        # Migrations pour sessions
        for col in ["ip_address", "os", "browser", "device"]:
            try:
                cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
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
            SET pass_hash = ?, totp_secret = ?, security_question = ?, security_answer_hash = ?, must_enroll = 0, last_enrollment = ?, name = ?
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
# SESSION UTILS
# ==============================================================================
def create_session(email: str, request: Request = None) -> str:
    session_id = secrets.token_urlsafe(32)
    ip_address = ""
    os_str = ""
    browser_str = ""
    device_str = ""
    
    if request:
        ip_address = request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.client.host if request.client else ""))
        if ip_address and "," in ip_address:
            ip_address = ip_address.split(",")[0].strip()
            
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
        try:
            cursor.execute("INSERT INTO sessions (session_id, email, created_at, ip_address, os, browser, device) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                           (session_id, email, int(time.time()), ip_address, os_str, browser_str, device_str))
        except sqlite3.OperationalError:
            cursor.execute("INSERT INTO sessions (session_id, email, created_at) VALUES (?, ?, ?)", 
                           (session_id, email, int(time.time())))
        conn.commit()
    return session_id

def get_current_user_email(echo_auth_session: Optional[str] = Cookie(None)):
    if not echo_auth_session:
        return None
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT email, created_at FROM sessions WHERE session_id = ?", (echo_auth_session,))
        row = cursor.fetchone()
        
    if not row:
        return None
        
    settings = get_auth_settings()
    timeout_h = int(settings.get("session_timeout", 2160))
    
    if timeout_h > 0:
        if time.time() - row["created_at"] > timeout_h * 3600:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (echo_auth_session,))
                conn.commit()
            return None
            
    return row["email"]

def delete_session(session_id: str):
    if session_id:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()

# ==============================================================================
# FORWARD-AUTH ENDPOINT (BunkerWeb)
# ==============================================================================
def is_safe_url(url: str) -> bool:
    """Vérifie si l'URL de redirection (next) est sécurisée (Open Redirect Protection).
    Validation basée exclusivement sur la variable ECHO_DOMAIN."""
    if not url:
        return False
    if url.startswith("/"):
        return True
    
    parsed = urlparse(url)
    
    if not parsed.hostname:
        return False
        
    return (
        parsed.hostname == "localhost" or 
        parsed.hostname == ECHO_DOMAIN or 
        parsed.hostname.endswith(f".{ECHO_DOMAIN}")
    )

@app.get("/logout")
async def logout_sso(request: Request, next: str = "/", echo_auth_session: Optional[str] = Cookie(None)):
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
        
    safe_next = next if is_safe_url(next) else "/"
    from urllib.parse import quote
    redirect_url = f"/login?next={quote(safe_next)}" if safe_next != "/" else "/login"
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
async def verify_auth(request: Request, echo_auth_session: Optional[str] = Cookie(None)):
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
    raise HTTPException(status_code=403, detail="Forbidden")

# ==============================================================================
# PAGES UI & LOGIQUE METIER
# ==============================================================================
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, echo_auth_session: Optional[str] = Cookie(None), next: str = "/"):
    safe_next = next if is_safe_url(next) else "/"
    email = get_current_user_email(echo_auth_session)
    if email:
        return RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "next": safe_next})

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...), totp: Optional[str] = Form(None), next: str = Form("/")) :
    safe_next = next if is_safe_url(next) else "/"
    user = get_user(email)
    if not user:
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Identifiants incorrects.", "next": safe_next})
    
    if not verify_password(password, user["pass_hash"]):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Identifiants incorrects.", "next": safe_next})

    if user["must_enroll"] == 1:
        # Vérification de l'expiration du mot de passe temporaire
        if user["temp_pass_expires"] and user["temp_pass_expires"] > 0:
            if time.time() > user["temp_pass_expires"]:
                return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Mot de passe temporaire expiré. Contactez l'administrateur.", "next": safe_next})
        # Redirection vers l'enrôlement
        totp_secret = generate_totp_secret()
        qr_b64 = generate_qr_base64(totp_secret, email)
        
        settings = get_auth_settings()
        return templates.TemplateResponse(request=request, name="enroll.html", context={
            "request": request, 
            "email": email, 
            "totp_secret": totp_secret,
            "qr_code_b64": qr_b64,
            "pwd_min_length": settings.get("pwd_min_length", 12),
            "pwd_require_upper": settings.get("pwd_require_upper", True),
            "pwd_require_lower": settings.get("pwd_require_lower", True),
            "pwd_require_digit": settings.get("pwd_require_digit", True),
            "pwd_require_special": settings.get("pwd_require_special", True)
        })

    # Utilisateur normal, vérification MFA
    if not totp or not verify_totp(user["totp_secret"], totp):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Code MFA invalide.", "next": safe_next})

    # Succès
    session_id = create_session(email, request)
    response = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    
    settings = get_auth_settings()
    session_timeout_h = int(settings.get("session_timeout", 2160))
    max_age = session_timeout_h * 3600 if session_timeout_h > 0 else None
    
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
    name: Optional[str] = Form(None)
):
    user = get_user(email)
    if not user or user["must_enroll"] == 0:
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Requête invalide."})

    settings = get_auth_settings()
    # Revalidation des paramètres de template pour le renvoi d'erreur
    ctx = {
        "request": request, "email": email, "totp_secret": totp_secret, 
        "qr_code_b64": generate_qr_base64(totp_secret, email),
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
    
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "success": "Enrôlement réussi. Vous pouvez vous connecter."})

@app.get("/recovery", response_class=HTMLResponse)
async def recovery_page(request: Request):
    return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request})

@app.post("/recovery/step1", response_class=HTMLResponse)
async def recovery_step1(request: Request, email: str = Form(...)):
    user = get_user(email)
    if not user or user["must_enroll"] == 1:
        # Fausse sécurité pour éviter l'énumération
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "error": "Compte introuvable ou non éligible."})
    
    return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "email": email, "question": user["security_question"]})

@app.post("/recovery/step2", response_class=HTMLResponse)
async def recovery_step2(
    request: Request, 
    email: str = Form(...), 
    security_answer: str = Form(...), 
    totp_code: str = Form(...), 
    new_password: str = Form(...)
):
    user = get_user(email)
    if not user:
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "error": "Compte introuvable."})

    # Validation Answer
    if not verify_password(security_answer.lower().strip(), user["security_answer_hash"]):
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "email": email, "question": user["security_question"], "error": "Réponse de sécurité incorrecte."})

    # Validation MFA
    if not verify_totp(user["totp_secret"], totp_code):
        return templates.TemplateResponse(request=request, name="recovery.html", context={"request": request, "email": email, "question": user["security_question"], "error": "Code MFA invalide."})

    # Succès
    pass_hash = hash_password(new_password)
    update_user_password(email, pass_hash)
    
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "success": "Mot de passe réinitialisé avec succès."})

# Route racine de fallback (évite les 404 si safe_next retombe sur "/")
@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("auth_server:app", host="0.0.0.0", port=8000, reload=True)
