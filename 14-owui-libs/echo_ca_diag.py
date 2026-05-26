"""
title: ECHO AGY Diagnostic
author: Wilfried BARNAVON
version: 2.2
description: Outil de diagnostic API cloudcode-pa.googleapis.com.
             Lance depuis le repertoire utilisateur ECHO (contenant identity.db).
             Teste systematiquement : auth, provisioning, modeles, generate, stream.

Usage :
    cd /app/backend/data/users/{user_id}/
    python /app/backend/echo_libs/echo_ca_diag.py
    python /app/backend/echo_libs/echo_ca_diag.py --section10-models gemini-pro-agent,gemini-3.1-pro-low
"""

import sys
import os
import sqlite3
import json
import time
import asyncio
import argparse

# ---------------------------------------------------------------------------
# Path ECHO libs
# ---------------------------------------------------------------------------
sys.path.insert(0, "/app/backend/echo_libs")

SEP  = "=" * 70
SEP2 = "-" * 70

# ---------------------------------------------------------------------------
# CLI args  (doit etre avant toute logique qui lit des variables globales)
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="ECHO AGY Diagnostic")
parser.add_argument("--aistudio-key", default=None,
                    help="Cle AI Studio pour tester generativelanguage.googleapis.com")
parser.add_argument("--section10-models", default=None,
                    help="Modeles supplementaires a tester en section 10, comma-separated")
parser.add_argument("--test-models", default=None,
                    help="Alias pour --section10-models (compat)")
CLI_ARGS, _ = parser.parse_known_args()

def hdr(title):
    print(f"\n{SEP}\n  {title}\n{SEP}")

def ok(msg):  print(f"  ✅ {msg}")
def ko(msg):  print(f"  ❌ {msg}")
def inf(msg): print(f"  ℹ️  {msg}")
def raw(msg): print(f"  >> {msg}")

# ---------------------------------------------------------------------------
# 1. Import constantes ECHO (avec fallbacks)
# ---------------------------------------------------------------------------
hdr("1. Import echo_constants")
try:
    from echo_constants import (
        AGY_BASE_URL, GOOGLE_API_BASE_URL,
        ANTIGRAVITY_OAUTH_CLIENT_ID,
        ANTIGRAVITY_OAUTH_CLIENT_SECRET,
        ANTIGRAVITY_DESKTOP_CLIENT_ID,
        ANTIGRAVITY_DESKTOP_CLIENT_SECRET,
        GOOGLE_OAUTH_TOKEN_URL,
        ECHO_AGY_USER_AGENT,
        ECHO_CLIENT_METADATA,
        MODEL_PRO, MODEL_FLASH, MODEL_LITE,
        AUTH_DATA_PROJECT_ID,
        AUTH_METHOD_KEY_PRIMARY,
    )
    ok("echo_constants importe")
    inf(f"AGY_BASE_URL               = {AGY_BASE_URL}")
    inf(f"ECHO_AGY_UA                = {ECHO_AGY_USER_AGENT}")
    inf(f"ANTIGRAVITY_CLIENT_ID (LS) = {ANTIGRAVITY_OAUTH_CLIENT_ID[:30]}...")
    inf(f"ANTIGRAVITY_CLIENT_ID (DT) = {ANTIGRAVITY_DESKTOP_CLIENT_ID[:30]}...")
    inf(f"MODEL_PRO   = {MODEL_PRO}")
    inf(f"MODEL_FLASH = {MODEL_FLASH}")
    inf(f"MODEL_LITE  = {MODEL_LITE}")
except Exception as e:
    ko(f"echo_constants indisponible : {e}")
    inf("Utilisation des valeurs de fallback (RE 2026-05-23)")
    AGY_BASE_URL              = "https://cloudcode-pa.googleapis.com/v1internal"
    ECHO_AGY_USER_AGENT       = (
        "antigravity/2.1.0 (language_server; os_type=Windows; "
        "os_version=10.0.26100; arch=x64)"
    )
    ANTIGRAVITY_OAUTH_CLIENT_ID       = ""
    ANTIGRAVITY_OAUTH_CLIENT_SECRET   = ""
    ANTIGRAVITY_DESKTOP_CLIENT_ID     = ""
    ANTIGRAVITY_DESKTOP_CLIENT_SECRET = ""
    GOOGLE_OAUTH_TOKEN_URL            = "https://oauth2.googleapis.com/token"
    GOOGLE_API_BASE_URL               = "https://generativelanguage.googleapis.com/v1beta"
    MODEL_PRO   = "gemini-2.5-pro"
    MODEL_FLASH = "gemini-3-flash"
    MODEL_LITE  = "gemini-3.1-flash-lite"
    AUTH_DATA_PROJECT_ID = "project_id"
    AUTH_METHOD_KEY_PRIMARY = "google_api_key"
    ECHO_CLIENT_METADATA = {
        "ideType":    "IDE_UNSPECIFIED",
        "platform":   "PLATFORM_UNSPECIFIED",
        "pluginType": "GEMINI",
    }

# ---------------------------------------------------------------------------
# 2. Lecture identity.db (dossier courant = repertoire utilisateur ECHO)
# ---------------------------------------------------------------------------
hdr("2. Lecture identity.db")

DB_PATH = os.path.join(os.getcwd(), "identity.db")
inf(f"CWD         = {os.getcwd()}")
inf(f"identity.db = {DB_PATH}")

if not os.path.exists(DB_PATH):
    ko(f"identity.db introuvable ! Lance le script depuis le dossier utilisateur ECHO.")
    sys.exit(1)

ok(f"identity.db trouve ({os.path.getsize(DB_PATH)} octets)")

def db_get(key):
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            row = conn.execute(
                "SELECT value FROM auth_data WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        return None

def db_set(key, value):
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, int(time.time()))
            )
            conn.commit()
    except Exception as e:
        ko(f"Erreur ecriture DB : {e}")

# Dump de toutes les cles (masquage des valeurs sensibles)
print()
inf("Cles presentes dans identity.db :")
try:
    with sqlite3.connect(DB_PATH, timeout=5) as conn:
        rows = conn.execute("SELECT key, length(value), updated_at FROM auth_data").fetchall()
        for k, vlen, ts in rows:
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"
            print(f"     {k:<45} len={vlen:<6} updated={ts_str}")
except Exception as e:
    ko(f"Impossible de lire les cles : {e}")

refresh_token  = db_get("google_oauth2_refresh_token")
access_token   = db_get("google_oauth2_access_token")
project_id     = db_get(AUTH_DATA_PROJECT_ID)
last_refresh   = float(db_get("google_oauth2_last_refresh") or 0)

print()
inf(f"refresh_token  : {'PRESENT (' + str(len(refresh_token)) + ' chars)' if refresh_token else 'ABSENT'}")
inf(f"access_token   : {'PRESENT (' + str(len(access_token)) + ' chars)' if access_token else 'ABSENT'}")
inf(f"project_id     : {project_id or 'ABSENT'}")
inf(f"last_refresh   : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_refresh)) if last_refresh else 'JAMAIS'}")

if not refresh_token:
    ko("Pas de refresh_token — authentification incomplète. Abandon.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 3. Refresh du token si necessaire (> 55 min ou absent)
# ---------------------------------------------------------------------------
hdr("3. Refresh OAuth2 access token")

TOKEN_TTL = 3300  # 55 min

async def refresh_oauth_token(rt: str, client_id: str, client_secret: str, label: str) -> str | None:
    import httpx
    payload = {
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": rt,
        "grant_type":    "refresh_token",
    }
    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        resp = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
        inf(f"[{label}] Token refresh HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token")
            if token:
                db_set("google_oauth2_access_token", token)
                db_set("google_oauth2_last_refresh", str(time.time()))
                ok(f"[{label}] Token refreshed ({len(token)} chars)")
                return token
            else:
                ko(f"[{label}] Pas d'access_token dans la reponse : {data}")
        else:
            ko(f"[{label}] Erreur : {resp.status_code} — {resp.text[:200]}")
    return None

token_age = time.time() - last_refresh
inf(f"Age du token : {token_age:.0f}s (TTL={TOKEN_TTL}s)")

if not access_token or token_age > TOKEN_TTL:
    inf("Refresh necessaire — test avec les 2 clients (Desktop puis LS) :")
    # Test 1 : client Desktop 1071006060591 — c'est lui qui a emis le token via PKCE
    access_token = asyncio.run(refresh_oauth_token(
        refresh_token, ANTIGRAVITY_DESKTOP_CLIENT_ID, ANTIGRAVITY_DESKTOP_CLIENT_SECRET,
        "Desktop 1071006060591"
    ))
    if not access_token:
        # Test 2 : client LS 884354919052 — pour comparaison / diagnostic
        inf("Desktop echoue — essai client LS (884354919052) :")
        access_token = asyncio.run(refresh_oauth_token(
            refresh_token, ANTIGRAVITY_OAUTH_CLIENT_ID, ANTIGRAVITY_OAUTH_CLIENT_SECRET,
            "LS 884354919052"
        ))
else:
    ok(f"Token valide ({TOKEN_TTL - token_age:.0f}s restantes)")

if not access_token:
    ko("Aucun access token disponible. Abandon.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 4. Headers communs
# ---------------------------------------------------------------------------
def make_headers(project: str = None) -> dict:
    h = {
        "Authorization":    f"Bearer {access_token}",
        "Content-Type":     "application/json",
        "User-Agent":       ECHO_AGY_USER_AGENT,
        "x-goog-api-client": "antigravity/2.1.0",
    }
    # Ne pas ajouter x-goog-user-project pour la generation (cause 403)
    if project:
        h["x-goog-user-project"] = project
    return h

# ---------------------------------------------------------------------------
# 5. Test loadCodeAssist (provisioning)
# ---------------------------------------------------------------------------
hdr("5. Test loadCodeAssist (provisioning)")

async def test_load_agy():
    import httpx
    url = f"{AGY_BASE_URL}:loadCodeAssist"
    # Payload identique a la production echo_auth.py:_provision_google_account()
    # ECHO_CLIENT_METADATA = {ideType: IDE_UNSPECIFIED, platform: PLATFORM_UNSPECIFIED, pluginType: GEMINI}
    payload = {
        "cloudaicompanionProject": project_id,
        "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id},
        "mode": "HEALTH_CHECK",
    }
    inf(f"URL      : {url}")
    inf(f"Project  : {project_id}")
    inf(f"Metadata : {ECHO_CLIENT_METADATA}")
    # Ne PAS passer project_id ici — x-goog-user-project cause 403 (API non activée dans ce projet)
    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        resp = await client.post(url, json=payload, headers=make_headers())
        inf(f"HTTP     : {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            ok("loadCodeAssist OK")
            returned_project = data.get("cloudaicompanionProject") or data.get("project")
            if returned_project:
                inf(f"Project retourne : {returned_project}")
                if returned_project != project_id:
                    inf(f"⚠️  DIFF avec project_id en DB : {project_id}")
            inf(f"Reponse (truncated) : {str(data)[:400]}")
            return data
        else:
            ko(f"loadCodeAssist FAILED : {resp.text[:400]}")
            return None

asyncio.run(test_load_agy())

# ---------------------------------------------------------------------------
# 6. Test fetchAvailableModels
# ---------------------------------------------------------------------------
hdr("6. Test fetchAvailableModels")

AVAILABLE_MODELS = []

async def test_fetch_models():
    import httpx
    url = f"{AGY_BASE_URL}:fetchAvailableModels"
    # metadata invalide pour fetchAvailableModels (400 sinon) — confirmé par diagnostic
    payload = {"project": project_id}
    inf(f"URL : {url}")
    inf(f"Payload : {payload}")
    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        resp = await client.post(url, json=payload, headers=make_headers())
        inf(f"HTTP : {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            ok("fetchAvailableModels OK")
            models = data.get("models") or data.get("availableModels") or []
            if models:
                # models est un dict {model_id: {...}} ou une liste
                if isinstance(models, dict):
                    model_ids = list(models.keys())
                else:
                    model_ids = [
                        m if isinstance(m, str) else m.get("id") or m.get("name") or str(m)
                        for m in models
                    ]
                inf(f"Modeles disponibles ({len(model_ids)}) :")
                for name in model_ids:
                    AVAILABLE_MODELS.append(name)
                    entry = models[name] if isinstance(models, dict) else {}
                    disp = entry.get("displayName", "")
                    prov = entry.get("apiProvider", "")
                    print(f"       - {name:<40} {disp} [{prov}]")
            else:
                inf(f"Reponse brute (aucun champ 'models') : {str(data)[:300]}")
            return data
        else:
            ko(f"fetchAvailableModels FAILED : {resp.text[:400]}")
            return None

asyncio.run(test_fetch_models())

# ---------------------------------------------------------------------------
# 6b. Exploration v1internal:listExperiments
# ---------------------------------------------------------------------------
hdr("6b. Exploration v1internal:listExperiments")

async def test_list_experiments():
    import httpx
    import json as _json
    url = f"{AGY_BASE_URL}:listExperiments"
    inf(f"URL : {url}")

    # Variantes de payload a tester — on ne connait pas le schema, on explore
    variants = {
        # A : payload minimal (meme format que fetchAvailableModels)
        "A - minimal {project}": {
            "project": project_id
        },
        # B : avec cloudaicompanionProject (meme format que loadCodeAssist)
        "B - cloudaicompanionProject": {
            "cloudaicompanionProject": project_id
        },
        # C : avec metadata (meme format que loadCodeAssist)
        "C - avec metadata": {
            "cloudaicompanionProject": project_id,
            "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id},
        },
        # D : payload vide
        "D - vide {}": {},
        # E : avec request_type
        "E - avec request_type CHAT": {
            "project": project_id,
            "request_type": "DOMAIN_STREAMING_CHAT",
        },
        # F : avec model filtre
        "F - avec model_filter": {
            "project": project_id,
            "model_filter": "gemini",
        },
        # G : avec page_size
        "G - avec page_size": {
            "project": project_id,
            "page_size": 100,
        },
    }

    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        for label, payload in variants.items():
            try:
                resp = await client.post(
                    url, json=payload, headers=make_headers()
                )
                status = resp.status_code
                icon = "OK" if status == 200 else "KO"
                print(f"\n  {icon} [{status}] {label}")
                if status == 200:
                    try:
                        data = resp.json()
                        # Afficher la reponse brute complete
                        inf(f"Reponse JSON :")
                        print(_json.dumps(data, indent=4, ensure_ascii=False)[:3000])
                        # Extraire les experiments si presents
                        for key in ("experiments", "experiment", "models",
                                    "model_experiments", "availableExperiments",
                                    "items", "results"):
                            if key in data:
                                inf(f"Champ '{key}' trouve ({len(data[key])} entrees) :")
                                for item in data[key][:20]:
                                    print(f"       {item}")
                    except Exception as je:
                        inf(f"Reponse brute (non-JSON) : {resp.text[:1000]}")
                else:
                    print(f"       {resp.text[:300]}")
            except Exception as e:
                print(f"  ERR {label} : {e}")

asyncio.run(test_list_experiments())

# ---------------------------------------------------------------------------
# 6c. Capacites detaillees des modeles (supports_raw_thinking, minThinkingBudget)
# ---------------------------------------------------------------------------
hdr("6c. Capacites detaillees des modeles CA")

async def test_model_capabilities():
    import httpx
    import json as _json
    # Essayer plusieurs variantes de l'endpoint pour obtenir les details du modele
    # La reponse de fetchAvailableModels pourrait avoir un champ detail
    url = f"{AGY_BASE_URL}:fetchAvailableModels"
    # Tester avec des params supplementaires pour obtenir les details
    variants = {
        "A - standard (avec details=true)": {
            "project": project_id,
            "include_details": True
        },
        "B - avec view=FULL": {
            "project": project_id,
            "view": "FULL"
        },
        "C - avec capabilities=true": {
            "project": project_id,
            "include_capabilities": True
        },
    }
    # D'abord: afficher le premier modele disponible en detail complet
    inf("Fetch standard (section 6) - detail complet des premiers modeles :")
    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        resp = await client.post(url, json={"project": project_id}, headers=make_headers())
        if resp.status_code == 200:
            data = resp.json()
            models_raw = data.get("models") or {}
            # Afficher la structure complete pour les modeles cles
            for target_id in ["gemini-pro-agent", "gemini-3-flash-agent",
                               "gemini-3.5-flash-low", "gemini-3.1-pro-low",
                               "gemini-3.1-flash-lite"]:
                if target_id in models_raw:
                    entry = models_raw[target_id]
                    print(f"\n  Modele [{target_id}]:")
                    print(_json.dumps(entry, indent=6, ensure_ascii=False))

        # Tester les variantes pour des details supplementaires
        for label, payload in variants.items():
            resp2 = await client.post(url, json=payload, headers=make_headers())
            status = resp2.status_code
            icon = "OK" if status == 200 else "KO"
            print(f"\n  {icon} [{status}] fetchAvailableModels — {label}")
            if status == 200:
                data2 = resp2.json()
                # Chercher les champs de capacite dans la reponse
                models2 = data2.get("models") or {}
                for m_id, m_data in list(models2.items())[:3]:
                    cap_fields = {k: v for k, v in m_data.items()
                                  if any(kw in k.lower() for kw in
                                         ["thinking", "budget", "raw", "supports",
                                          "capability", "level", "provider", "vertex"])}
                    if cap_fields:
                        print(f"    [{m_id}] cap fields: {cap_fields}")
            else:
                print(f"    {resp2.text[:200]}")

asyncio.run(test_model_capabilities())

# ---------------------------------------------------------------------------
# 6d. Découverte "Enable AI Credit Overages" (AGY-IDE toggle)
#     Objectif : identifier le champ/endpoint qui active l'utilisation des
#     crédits en fallback quand le quota modèle est épuisé.
#     Stratégie : tester des variantes de loadCodeAssist + endpoints settings.
# ---------------------------------------------------------------------------
hdr("6d. Découverte Credit Overages (AGY-IDE feature)")

async def test_credit_overages():
    import httpx
    import json as _json

    async with httpx.AsyncClient(http2=True, timeout=20) as client:

        # --- A. loadCodeAssist : variantes du payload avec credit overages ---
        inf("A. Variantes loadCodeAssist avec credit overage flags :")
        base_payload = {
            "cloudaicompanionProject": project_id,
            "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id},
            "mode": "HEALTH_CHECK",
        }
        variants_lca = {
            "enableAiCreditOverages":   {**base_payload, "enableAiCreditOverages": True},
            "enableCreditOverages":      {**base_payload, "enableCreditOverages": True},
            "allowCreditUsage":          {**base_payload, "allowCreditUsage": True},
            "enableOverages":            {**base_payload, "enableOverages": True},
            "creditOverageEnabled":      {**base_payload, "creditOverageEnabled": True},
            "useCreditFallback":         {**base_payload, "useCreditFallback": True},
        }
        url_lca = f"{AGY_BASE_URL}:loadCodeAssist"
        for flag_name, payload in variants_lca.items():
            resp = await client.post(url_lca, json=payload, headers=make_headers())
            data = {}
            try: data = resp.json()
            except: pass
            # Chercher dans la réponse des champs credit/overage
            credit_fields = {k: v for k, v in data.items()
                             if any(kw in k.lower() for kw in
                                    ["credit", "overage", "billing", "tier", "paid", "allow"])}
            paid = data.get("paidTier") or data.get("currentTier") or {}
            avail = paid.get("availableCredits", []) if isinstance(paid, dict) else []
            total = sum(int(c.get("creditAmount", 0)) for c in avail if c.get("creditAmount"))
            status_icon = "OK" if resp.status_code == 200 else "KO"
            print(f"  {status_icon} [{resp.status_code}] flag='{flag_name}' | credits={total} | champs: {list(credit_fields.keys())}")
            if credit_fields:
                print(f"       {_json.dumps(credit_fields, indent=6, ensure_ascii=False)[:400]}")

        # --- B. retrieveUserQuota : réponse complète brute ---
        inf("\nB. retrieveUserQuota — réponse JSON complète :")
        url_q = f"{AGY_BASE_URL}:retrieveUserQuota"
        resp_q = await client.post(url_q, json={"project": project_id}, headers=make_headers())
        if resp_q.status_code == 200:
            print(_json.dumps(resp_q.json(), indent=4, ensure_ascii=False)[:3000])
        else:
            ko(f"retrieveUserQuota FAILED [{resp_q.status_code}]: {resp_q.text[:300]}")

        # --- C. Endpoints settings potentiels ---
        inf("\nC. Endpoints settings (updateUserSettings, setUserPreferences...) :")
        settings_endpoints = [
            f"{AGY_BASE_URL}:updateUserSettings",
            f"{AGY_BASE_URL}:getUserSettings",
            f"{AGY_BASE_URL}:updateSettings",
            f"{AGY_BASE_URL}:getSettings",
            f"{AGY_BASE_URL}:setUserPreferences",
            f"{AGY_BASE_URL}:getUserPreferences",
        ]
        for ep in settings_endpoints:
            try:
                r = await client.post(ep,
                    json={"project": project_id, "enableAiCreditOverages": True},
                    headers=make_headers(), timeout=8)
                print(f"  [{r.status_code}] {ep.split(':')[-1]}")
                if r.status_code not in (404, 405):
                    try: print(f"       {_json.dumps(r.json(), indent=4)[:400]}")
                    except: print(f"       {r.text[:200]}")
            except Exception as e:
                print(f"  ERR {ep.split(':')[-1]} : {e}")

asyncio.run(test_credit_overages())

ECHO_MODELS = [MODEL_PRO, MODEL_FLASH, MODEL_LITE]
RE_MODELS   = [
    # Noms du binaire RE (sans -preview) — tests precedents
    "gemini-3.1-pro",
    "gemini-3.1-flash-lite",
    "gemini-3-flash",
    # Noms du RE document (section fetchAvailableModels)
    "gemini-3.5-flash-preview",   # RE doc — a tester !
    "gemini-2.5-flash-lite",      # RE doc — a tester !
    # Fallbacks stables connus
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    # === CANDIDATS AI STUDIO : valides sur AI Studio, a valider sur Code Assist ===
    # Question : gemini-3.5-flash et gemini-3.1-pro-preview fonctionnent-ils directement
    # sur cloudcode-pa (Code Assist) ? Si oui, AGY_MODEL_MAP est inutile.
    "gemini-3.5-flash",           # AI Studio ✅ 200 — Code Assist : ???
    "gemini-3.1-pro-preview",     # AI Studio ⚠️ 429 — Code Assist : ???
]
# Deduplication ordre : ECHO d'abord, puis RE, puis modeles API
ALL_MODELS = list(dict.fromkeys(ECHO_MODELS + RE_MODELS))
# Ajouter les modeles remontés par fetchAvailableModels
# Filtrer les modeles internes (non-Gemini) : chat_*, tab_*, gpt-oss*, claude*, *-agent
IMPORTED_PREFIXES = ("chat_", "tab_", "gpt-", "claude")  # non-Gemini
for m in AVAILABLE_MODELS:
    if m not in ALL_MODELS and not any(m.startswith(p) for p in IMPORTED_PREFIXES):
        ALL_MODELS.append(m)

SIMPLE_PROMPT = {
    "role": "user",
    "parts": [{"text": "Reponds uniquement : OK"}]
}

GENERATE_RESULTS = {}  # model -> (status_code, ok/ko, snippet)

async def test_generate_content(model: str):
    import httpx
    url = f"{AGY_BASE_URL}:generateContent"
    # session_id DANS request (comme echo_utils.py) — pas a la racine
    request_body = {
        "contents": [SIMPLE_PROMPT],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
        "session_id": "diag-session",
    }
    payload = {
        "model":          model,
        "project":        project_id,
        "user_prompt_id": "diag-0000",
        "request":        request_body,
    }
    async with httpx.AsyncClient(http2=True, timeout=30) as client:
        resp = await client.post(url, json=payload, headers=make_headers())
        status = resp.status_code
        snippet = resp.text[:200]
        return status, snippet

print()
inf(f"Nombre de modeles a tester : {len(ALL_MODELS)}")
for model in ALL_MODELS:
    status, snippet = asyncio.run(test_generate_content(model))
    GENERATE_RESULTS[model] = (status, snippet)
    marker = "✅" if status == 200 else "❌"
    print(f"  {marker} [{status}] {model:<45} | {snippet[:80]}")

# ---------------------------------------------------------------------------
# 8. Test generateContent SANS session_id / user_prompt_id (isolation)
# ---------------------------------------------------------------------------
hdr("8. Test generateContent — Sans session_id/user_prompt_id (isolation payload)")

# Tester uniquement les modeles qui ont passe ou echoue de facon interessante
TEST_MODELS_SLIM = [m for m, (s, _) in GENERATE_RESULTS.items() if s != 200][:3]
if not TEST_MODELS_SLIM:
    TEST_MODELS_SLIM = list(GENERATE_RESULTS.keys())[:2]

async def test_generate_slim(model: str):
    import httpx
    url = f"{AGY_BASE_URL}:generateContent"
    # Payload minimal - sans session_id ni user_prompt_id
    payload = {
        "model":   model,
        "project": project_id,
        "request": {
            "contents": [SIMPLE_PROMPT],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
        },
    }
    async with httpx.AsyncClient(http2=True, timeout=30) as client:
        resp = await client.post(url, json=payload, headers=make_headers())
        return resp.status_code, resp.text[:200]

for model in TEST_MODELS_SLIM:
    status, snippet = asyncio.run(test_generate_slim(model))
    marker = "✅" if status == 200 else "❌"
    print(f"  {marker} [{status}] {model:<45} | {snippet[:80]}")

# ---------------------------------------------------------------------------
# 9. Test streamGenerateContent sur les modeles qui ont fonctionne en #7
# ---------------------------------------------------------------------------
hdr("9. Test streamGenerateContent (streaming SSE)")

STREAM_CANDIDATES = [m for m, (s, _) in GENERATE_RESULTS.items() if s == 200]
if not STREAM_CANDIDATES:
    inf("Aucun modele n'a passe le test generateContent — test stream sur les 2 premiers quand meme")
    STREAM_CANDIDATES = list(GENERATE_RESULTS.keys())[:2]

async def test_stream(model: str):
    import httpx
    url = f"{AGY_BASE_URL}:streamGenerateContent?alt=sse"
    payload = {
        "model":          model,
        "project":        project_id,
        "user_prompt_id": "diag-stream",
        "request": {
            "contents": [SIMPLE_PROMPT],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 20},
        },
    }
    chunks = []
    try:
        async with httpx.AsyncClient(http2=True, timeout=30) as client:
            async with client.stream("POST", url, json=payload, headers=make_headers()) as r:
                status = r.status_code
                if status != 200:
                    body = await r.aread()
                    return status, body.decode("utf-8", errors="replace")[:300]
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        chunks.append(line[5:].strip())
                        if len(chunks) >= 3:
                            break
                return 200, f"{len(chunks)} chunk(s) : {chunks[:2]}"
    except Exception as e:
        return -1, str(e)

for model in STREAM_CANDIDATES:
    status, result = asyncio.run(test_stream(model))
    marker = "✅" if status == 200 else "❌"
    print(f"  {marker} [{status}] {model:<45} | {result[:120]}")

# ---------------------------------------------------------------------------
# 9b. Test AI Studio (generativelanguage.googleapis.com) via la cle API en DB
# ---------------------------------------------------------------------------
hdr("9b. Test AI Studio (generativelanguage.googleapis.com)")

# Lire la cle AI Studio : arg CLI prioritaire, sinon DB
aistudio_key = CLI_ARGS.aistudio_key or db_get(AUTH_METHOD_KEY_PRIMARY)
if aistudio_key:
    src = "arg --aistudio-key" if CLI_ARGS.aistudio_key else "identity.db"
    ok(f"Cle AI Studio ({src}) : {len(aistudio_key)} chars, prefixe: {aistudio_key[:8]}...")
else:
    inf("Pas de cle AI Studio (ni --aistudio-key ni DB) — section ignoree")

AISTUDIO_RESULTS = {}  # model -> (status, snippet)

async def test_aistudio(model: str):
    import httpx
    # Meme liste de candidats que Code Assist
    url = f"{GOOGLE_API_BASE_URL}/models/{model}:generateContent?key={aistudio_key}"
    payload = {
        "contents": [SIMPLE_PROMPT],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
    }
    headers = {"Content-Type": "application/json", "User-Agent": ECHO_AGY_USER_AGENT}
    async with httpx.AsyncClient(http2=True, timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
        return resp.status_code, resp.text[:200]

if aistudio_key:
    # Candidats bases sur le selecteur AI Studio (mai 2026)
    AISTUDIO_CANDIDATES = [
        # ---- Modeles vus dans le selecteur AI Studio ----
        "gemini-3.5-flash",            # NEW - Gemini 3.5 Flash (sans -preview !)
        "gemini-3.1-flash-lite",       # NEW - Gemini 3.1 Flash Lite
        "gemini-3-flash-preview",      # Dec 2025 - main branch utilisait ce nom
        "gemini-3.1-pro-preview",      # Feb 2026 - PRO (429 sur free tier = existe)
        # ---- Variantes avec -preview ----
        "gemini-3.5-flash-preview",    # existe ? (404 au dernier test)
        "gemini-3.1-flash-lite-preview",# variante -preview du lite
        # ---- Aliases Code Assist (cross-check) ----
        "gemini-3-flash-agent",        # CA alias 3.5 Flash High
        "gemini-3.5-flash-low",        # CA alias 3.5 Flash Medium
        "gemini-pro-agent",            # CA alias Pro High
        "gemini-3-flash",              # CA alias (sans -preview)
        # ---- Stables connus ----
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    ]
    AISTUDIO_CANDIDATES = list(dict.fromkeys(AISTUDIO_CANDIDATES))  # dedup
    print()
    inf(f"Modeles AI Studio a tester : {len(AISTUDIO_CANDIDATES)}")
    for model in AISTUDIO_CANDIDATES:
        status, snippet = asyncio.run(test_aistudio(model))
        AISTUDIO_RESULTS[model] = (status, snippet)
        marker = "✅" if status == 200 else ("⚠️ " if status in (429, 503) else "❌")
        print(f"  {marker} [{status}] {model:<45} | {snippet[:80]}")
else:
    inf("Skipped (pas de cle AI Studio — relancer avec --aistudio-key KEY)")



async def check_token_info():
    import httpx
    url = f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={access_token}"
    async with httpx.AsyncClient(http2=True, timeout=10) as client:
        resp = await client.get(url)
        inf(f"HTTP : {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            ok("Token valide")
            scopes = data.get("scope", "")
            inf(f"Scopes : {scopes}")
            if "aicode" in scopes:
                ok("Scope 'aicode' PRESENT")
            else:
                ko("Scope 'aicode' ABSENT — critique pour Code Assist")
            if "cloud-platform" in scopes:
                ok("Scope 'cloud-platform' PRESENT")
            else:
                ko("Scope 'cloud-platform' ABSENT")
            inf(f"expires_in : {data.get('expires_in')}s")
            inf(f"email      : {data.get('email', 'N/A')}")
            inf(f"sub        : {data.get('sub', 'N/A')[:10]}...")
        else:
            ko(f"Token invalide ou expire : {resp.text[:200]}")

asyncio.run(check_token_info())

# ---------------------------------------------------------------------------
# 11. Synthese
# ---------------------------------------------------------------------------
hdr("11. SYNTHESE")

ok_models   = [m for m, (s, _) in GENERATE_RESULTS.items() if s == 200]
fail_models = [(m, s) for m, (s, _) in GENERATE_RESULTS.items() if s != 200]

if ok_models:
    ok(f"Code Assist — Modeles fonctionnels ({len(ok_models)}) :")
    for m in ok_models:
        print(f"       → {m}")
    echo_ok = [m for m in ok_models if m in ECHO_MODELS]
    if echo_ok:
        ok(f"Constantes ECHO valides pour Code Assist : {echo_ok}")
    else:
        ko(f"AUCUN modele ECHO constant ne fonctionne sur Code Assist !")
        ko(f"  MODEL_PRO   = {MODEL_PRO}")
        ko(f"  MODEL_FLASH = {MODEL_FLASH}")
        ko(f"  MODEL_LITE  = {MODEL_LITE}")
else:
    ko("Code Assist : AUCUN modele fonctionnel")

if AISTUDIO_RESULTS:
    ok_as = [m for m, (s, _) in AISTUDIO_RESULTS.items() if s == 200]
    ko_as = [(m, s) for m, (s, _) in AISTUDIO_RESULTS.items() if s not in (200, 503)]
    if ok_as:
        ok(f"AI Studio  — Modeles fonctionnels ({len(ok_as)}) :")
        for m in ok_as: print(f"       → {m}")
        echo_ok_as = [m for m in ok_as if m in ECHO_MODELS]
        if echo_ok_as:
            ok(f"Constantes ECHO valides pour AI Studio : {echo_ok_as}")
        else:
            ko(f"AUCUN modele ECHO constant ne fonctionne sur AI Studio !")

# Tableau croise
print()
print(f"  {'MODELE':<45} {'Code Assist':^12} {'AI Studio':^12}")
print(f"  {'-'*45} {'-'*12} {'-'*12}")
all_tested = list(dict.fromkeys(
    list(GENERATE_RESULTS.keys()) + list(AISTUDIO_RESULTS.keys())
))
for m in all_tested:
    ca = GENERATE_RESULTS.get(m)
    as_ = AISTUDIO_RESULTS.get(m)
    ca_s = f"{'✅' if ca and ca[0]==200 else ('⚠️ ' if ca and ca[0]==503 else '❌')} {ca[0] if ca else 'N/A'}"
    as_s = f"{'✅' if as_ and as_[0]==200 else ('⚠️ ' if as_ and as_[0]==503 else '❌')} {as_[0] if as_ else 'N/A'}"
    echo_marker = " ← ECHO" if m in ECHO_MODELS else ""
    print(f"  {m:<45} {ca_s:^12} {as_s:^12}{echo_marker}")

print(f"\n  project_id : {project_id}")
print(f"  Base URL AGY : {AGY_BASE_URL}")
print(f"  Base URL AS : {GOOGLE_API_BASE_URL}")

# ---------------------------------------------------------------------------
# 10. Diagnostic 400 : reproduction du payload pipe_engine (stream)
# ---------------------------------------------------------------------------
hdr("10. Diagnostic 400 — Payload pipe_engine (reproduction)")

inf("Version echo_utils en production :")
try:
    from echo_utils import EchoGeminiClient
    import echo_utils as _eu
    inf(f"  echo_utils version = {getattr(_eu, '__doc__', 'N/A')[:60]}")
    # Chercher la version dans le module
    import re as _re
    with open("/app/backend/echo_libs/echo_utils.py") as _f:
        _first = _f.read(500)
    _vm = _re.search(r"version:\s*([\d\.]+)", _first)
    inf(f"  echo_utils version (fichier) = {_vm.group(1) if _vm else 'introuvable'}")
    has_ca_map = hasattr(_eu, "AGY_MODEL_MAP") or "AGY_MODEL_MAP" in dir(_eu)
    inf(f"  AGY_MODEL_MAP dans echo_utils : {has_ca_map}")
    from echo_constants import AGY_MODEL_MAP
    inf(f"  AGY_MODEL_MAP = {AGY_MODEL_MAP}")
except Exception as e:
    ko(f"Import echo_utils : {e}")


# Test avec variantes progressives du payload pipe_engine
async def test_pipe_payloads():
    if not access_token or not project_id:
        ko("Pas de token/project disponible")
        return

    import httpx
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
        "User-Agent":    ECHO_AGY_USER_AGENT,
    }
    url = f"{AGY_BASE_URL}:streamGenerateContent?alt=sse"

    # Modeles de base + gemini-pro-agent (MODEL_PRO mapped) + modeles CLI
    base_models = [
        "gemini-3-flash",
        "gemini-3.1-flash-lite",
        "gemini-3-flash-agent",        # MODEL_FLASH CA mapped
        "gemini-3.5-flash-low",        # Flash MEDIUM (UI: 'Medium')
        "gemini-3.1-pro-low",          # Pro LOW
        "gemini-pro-agent",            # MODEL_PRO CA mapped
    ]
    extra_raw = CLI_ARGS.section10_models or CLI_ARGS.test_models or ""
    extra = [m.strip() for m in extra_raw.split(",") if m.strip()]
    section10_models = list(dict.fromkeys(base_models + extra))  # deduplique
    for test_model in section10_models:
        print(f"\n  --- Modele : {test_model} ---")

        payloads = {
            "A - Minimal": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "generationConfig": {"maxOutputTokens": 5},
                "session_id": "diag-test"
            },
            "B - + systemInstruction": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"maxOutputTokens": 5},
                "session_id": "diag-test"
            },
            # C : temperature seule (sans topP)
            "C - + temperature": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "maxOutputTokens": 5},
                "session_id": "diag-test"
            },
            # C-bis : PAYLOAD ECHO REEL (temperature + topP + maxOutputTokens, sans thinkingConfig)
            "C-bis - payload ECHO reel (temp+topP+max)": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "topP": 0.90, "maxOutputTokens": 65536},
                "session_id": "diag-test"
            },
            "D - + thinkingConfig complet": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {
                    "temperature": 1.0, "maxOutputTokens": 5,
                    "thinkingConfig": {"includeThoughts": True, "thinkingLevel": "HIGH"}
                },
                "session_id": "diag-test"
            },
            "D-bis - thinkingConfig sans includeThoughts": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {
                    "temperature": 1.0, "maxOutputTokens": 5,
                    "thinkingConfig": {"thinkingLevel": "HIGH"}
                },
                "session_id": "diag-test"
            },
            # D-ter : thinkingBudget (format v1internal natif)
            "D-ter - thinkingBudget v1internal": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {
                    "temperature": 1.0, "maxOutputTokens": 5,
                    "thinkingConfig": {"thinkingBudget": 1024}
                },
                "session_id": "diag-test"
            },
            "E - + tools + toolConfig (AUTO mode)": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "maxOutputTokens": 5},
                "tools": [{"function_declarations": [
                    {"name": "test_fn", "description": "test",
                     "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}}
                ]}],
                "toolConfig": {"function_calling_config": {"mode": "AUTO"}},
                "session_id": "diag-test"
            },
            # C-ter : ISOLATION topP (sans gros maxOutputTokens)
            "C-ter - topP seul (max=5)": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "topP": 0.90, "maxOutputTokens": 5},
                "session_id": "diag-test"
            },
            # C-quart : ISOLATION maxOutputTokens:65536 (sans topP)
            "C-quart - maxTokens:65536 seul (sans topP)": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "maxOutputTokens": 65536},
                "session_id": "diag-test"
            },
            # D-quart : thinkingLevel MEDIUM sur CA (existe pour Flash, pas Pro)
            "D-quart - thinkingLevel MEDIUM": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {
                    "temperature": 1.0, "maxOutputTokens": 5,
                    "thinkingConfig": {"thinkingLevel": "MEDIUM"}
                },
                "session_id": "diag-test"
            },
            # D-cinq : thinkingLevel LOW sur CA
            "D-cinq - thinkingLevel LOW": {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {
                    "temperature": 1.0, "maxOutputTokens": 5,
                    "thinkingConfig": {"thinkingLevel": "LOW"}
                },
                "session_id": "diag-test"
            },
            "F - contexte avec thoughtSignature (tour 2)": {
                "contents": [
                    {"role": "user", "parts": [{"text": "Bonjour"}]},
                    {"role": "model", "parts": [{"text": "Bonjour !", "thoughtSignature": "EoMBCoABAQw51scAAAAAAAAAAA=="}]},
                    {"role": "user", "parts": [{"text": "OK"}]}
                ],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "topP": 0.90, "maxOutputTokens": 5},
                "session_id": "diag-test"
            },
            # G : Contexte avec MAGIC_KEY (valeur non-base64 utilisee par ECHO comme placeholder)
            "G - thoughtSignature MAGIC_KEY (placeholder ECHO)": {
                "contents": [
                    {"role": "user", "parts": [{"text": "Bonjour"}]},
                    {"role": "model", "parts": [{"text": "Bonjour !", "thoughtSignature": "context_engineering_is_the_way_to_go"}]},
                    {"role": "user", "parts": [{"text": "OK"}]}
                ],
                "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
                "generationConfig": {"temperature": 1.0, "topP": 0.90, "maxOutputTokens": 5},
                "session_id": "diag-test"
            },
        }

        async with httpx.AsyncClient(http2=True, timeout=20) as client:
            for label, request_body in payloads.items():
                wrapped = {
                    "model": test_model,
                    "project": project_id,
                    "user_prompt_id": "agy-diag",
                    "request": request_body
                }
                try:
                    resp = await client.post(url, json=wrapped, headers=headers)
                    status = resp.status_code
                    snippet = resp.text[:150].replace("\n", " ")
                    icon = "OK" if status == 200 else "KO"
                    print(f"  {icon} [{status}] {label}")
                    if status != 200:
                        print(f"       {snippet}")
                except Exception as e:
                    print(f"  ERR {label} : {e}")

asyncio.run(test_pipe_payloads())

# ---------------------------------------------------------------------------
# 11. Test response_mime_type sur CA (H1 — BLOQUANT)
# ---------------------------------------------------------------------------
# call_distillation() envoie response_mime_type:"application/json" dans generationConfig
# pour les appels is_json=True (extraction mémoire, cognition, grounding, navigation).
# Ce champ n'a jamais ete teste sur CA (v1internal).
# Distinct de get_gemini_mime() dans echo_constants : celui-ci gere les MIME des fichiers
# en INPUT (uploads). response_mime_type ici controle le FORMAT de la reponse (output).
#
# Consequences si rejet (400) :
#   - call_distillation avec is_json=True → swallowed par le except → retourne {}
#   - Memoire, agents cognitifs, navigation : defaillance silencieuse sur CA
#
# Decision conditionnelle :
#   200 → aucun changement dans build_ca_generation_config
#   400 → ajouter "response_mime_type" à CA_EXCLUDED_GEN_CONF_FIELDS dans echo_protocol.py
# ---------------------------------------------------------------------------
hdr("11. Test response_mime_type sur CA (H1 - bloquant)")

SECTION11_MODELS = ["gemini-3.1-flash-lite", "gemini-3-flash-agent", "gemini-pro-agent"]

async def test_response_mime_type():
    import httpx
    import json as _json
    url = f"{AGY_BASE_URL}:streamGenerateContent?alt=sse"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
        "User-Agent":    ECHO_AGY_USER_AGENT,
    }
    if not access_token or not project_id:
        ko("Pas de token/project — section ignoree")
        return

    variants = {
        # H1a : scenario exact de call_distillation(is_json=True)
        "H1a - response_mime_type:application/json": {
            "contents": [{"role": "user", "parts": [{"text": 'Reponds uniquement: {"ok":true}'}]}],
            "generationConfig": {
                "temperature": 0.0, "maxOutputTokens": 30,
                "response_mime_type": "application/json"
            },
            "session_id": "diag-mime"
        },
        # H1b : variante text/plain
        "H1b - response_mime_type:text/plain": {
            "contents": [{"role": "user", "parts": [{"text": "Reponds uniquement: OK"}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10,
                                  "response_mime_type": "text/plain"},
            "session_id": "diag-mime"
        },
        # H1c : baseline sans response_mime_type (controle)
        "H1c - baseline (sans response_mime_type)": {
            "contents": [{"role": "user", "parts": [{"text": "Reponds uniquement: OK"}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
            "session_id": "diag-mime"
        },
    }

    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        for test_model in SECTION11_MODELS:
            print(f"\n  --- Modele : {test_model} ---")
            for label, request_body in variants.items():
                wrapped = {
                    "model": test_model, "project": project_id,
                    "user_prompt_id": "agy-diag-mime", "request": request_body
                }
                try:
                    async with client.stream("POST", url, json=wrapped, headers=headers) as r:
                        status = r.status_code
                        icon = "OK" if status == 200 else "KO"
                        if status == 200:
                            # Lire le premier chunk SSE pour verifier le format de reponse
                            chunk_parts = None
                            async for line in r.aiter_lines():
                                if line.startswith("data:"):
                                    try:
                                        parsed = _json.loads(line[5:].strip())
                                        chunk_parts = (parsed.get("response", {})
                                                            .get("candidates", [{}])[0]
                                                            .get("content", {})
                                                            .get("parts", []))
                                    except: pass
                                    break
                            print(f"  {icon} [{status}] {label}")
                            if chunk_parts:
                                print(f"         → parts[0]: {chunk_parts[0]}")
                        else:
                            body = await r.aread()
                            snippet = body.decode("utf-8", errors="replace")[:150].replace("\n", " ")
                            print(f"  {icon} [{status}] {label}")
                            print(f"       {snippet}")
                except Exception as e:
                    print(f"  ERR {label}: {e}")

asyncio.run(test_response_mime_type())

# ---------------------------------------------------------------------------
# 12. Test includeThoughts=True sur CA (H3 — informatif)
# ---------------------------------------------------------------------------
# Objectif : determiner le mecanisme d'arrivee des pensees sur CA.
# L'utilisateur voit des pensees dans AGY-IDE qui utilise CA.
# ECHO utilise split_thought_process() qui cherche <think>...</think> en texte.
# Les modeles CA retournent des thoughtSignature opaques — pas de texte lisible.
# Question : includeThoughts=True genere-t-il des parts avec "thought":true sur CA ?
# Si oui → ECHO pourrait exploiter le texte de pensee brut sur CA.
# Si non → seul thoughtSignature opaque (comportement actuel ECHO correct).
# ---------------------------------------------------------------------------
hdr("12. Test includeThoughts=True sur CA (H3 - informatif)")

SECTION12_MODELS = ["gemini-3-flash-agent", "gemini-pro-agent"]

async def test_include_thoughts():
    import httpx
    import json as _json
    url = f"{AGY_BASE_URL}:streamGenerateContent?alt=sse"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
        "User-Agent":    ECHO_AGY_USER_AGENT,
    }
    if not access_token or not project_id:
        ko("Pas de token/project — section ignoree")
        return

    request_body = {
        "contents": [{"role": "user", "parts": [{"text": "Explique en 1 phrase pourquoi le ciel est bleu."}]}],
        "systemInstruction": {"parts": [{"text": "Tu es ECHO."}]},
        "generationConfig": {
            "temperature": 1.0, "maxOutputTokens": 60,
            "thinkingConfig": {"includeThoughts": True, "thinkingLevel": "HIGH"}
        },
        "session_id": "diag-thoughts"
    }

    async with httpx.AsyncClient(http2=True, timeout=30) as client:
        for test_model in SECTION12_MODELS:
            print(f"\n  --- Modele : {test_model} ---")
            wrapped = {
                "model": test_model, "project": project_id,
                "user_prompt_id": "agy-diag-thoughts", "request": request_body
            }
            chunks_seen = 0
            try:
                async with client.stream("POST", url, json=wrapped, headers=headers) as r:
                    status = r.status_code
                    inf(f"HTTP {status}")
                    if status != 200:
                        body = await r.aread()
                        ko(body.decode("utf-8", errors="replace")[:200])
                        continue
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            data = _json.loads(line[5:].strip())
                            parts = (data.get("response", {})
                                         .get("candidates", [{}])[0]
                                         .get("content", {})
                                         .get("parts", []))
                            for i, p in enumerate(parts):
                                has_thought_flag = p.get("thought", False)
                                has_sig          = "thoughtSignature" in p
                                has_text         = "text" in p
                                text_preview     = p.get("text", "")[:60] if has_text else ""
                                sig_preview      = p.get("thoughtSignature", "")[:30] if has_sig else ""
                                print(f"    [chunk {chunks_seen} part {i}] thought={has_thought_flag} | sig={has_sig} | text={has_text}")
                                if has_text:  print(f"      text: '{text_preview}'")
                                if has_sig:   print(f"      sig:  '{sig_preview}...'")
                        except: pass
                        chunks_seen += 1
                        if chunks_seen >= 5:
                            break
            except Exception as e:
                ko(str(e))

asyncio.run(test_include_thoughts())

print()
print("=" * 70)
print("  Diagnostic termine.")
print("=" * 70)
