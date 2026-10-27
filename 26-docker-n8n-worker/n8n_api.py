# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO N8N WORKER API
VERSION : 5.231.20.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.20.1: Verrou asynchrone _N8N_AUTH_LOCK sur get_or_create_n8n_api_key, isolation de N8N_USER_FOLDER par execution_id avec purge finally, et préfixage propriétaire [ECHO:<user_id>] des démons.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 2.18: Rate-Limit Healthcheck
# 2.17: Fix : Ajout d'un filtre de logs (RateLimitHealthCheckFilter) limitant l'affichage des requêtes /health à 1/5min.
# 2.16: Fix : Écriture systématique d'un deployment_report.json dans chat_dir après chaque appel /deploy (succès, warning, erreur). Alimente le Download Broker pour émission d'AEC au modèle. Fix : Ajout de "settings": {} si absent du payload (requis par baseWorkflowShape). Fix : Timeout explicite sur delete_workflow (15s) et prune_workflows (30s). Refactor : Import subprocess supprimé (inutilisé), random remonté en tête.
import os
import json
import uuid
import random
import shutil
import asyncio
from pathlib import Path
from typing import Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, BackgroundTasks
import sys
import httpx
import time
import logging

class RateLimitHealthCheckFilter(logging.Filter):
    def __init__(self, rate_limit_seconds=300):
        super().__init__()
        self.rate_limit_seconds = rate_limit_seconds
        self.last_logged = 0

    def filter(self, record):
        if hasattr(record, 'args') and isinstance(record.args, tuple) and len(record.args) >= 3:
            if record.args[2] in ('/health', '/health/'):
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        try:
            msg = record.getMessage()
            if "GET /health" in msg:
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        except Exception:
            pass
        return True

logging.getLogger("uvicorn.access").addFilter(RateLimitHealthCheckFilter())

active_executions: Dict[str, list[asyncio.subprocess.Process]] = {}
N8N_MASTER_API_KEY: str | None = None
_N8N_AUTH_LOCK = asyncio.Lock()
N8N_URL = os.environ.get("N8N_URL", "http://127.0.0.1:5678")

app = FastAPI(title="ECHO N8N Worker API", description="Interface REST pour orchestration N8N")

N8N_AUTH_DEBUG_LOGS: list[str] = []


async def get_or_create_n8n_api_key() -> str | None:
    global N8N_MASTER_API_KEY
    if N8N_MASTER_API_KEY:
        return N8N_MASTER_API_KEY

    async with _N8N_AUTH_LOCK:
        if N8N_MASTER_API_KEY:
            N8N_AUTH_DEBUG_LOGS.append("Utilisation de la clé en cache.")
            return N8N_MASTER_API_KEY
        N8N_AUTH_DEBUG_LOGS.clear()

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Augmentation à 30 tentatives (150 secondes) car le boot initial de N8N avec migrations SQLite est très lent
            for i in range(30):
                N8N_AUTH_DEBUG_LOGS.append(f"--- Tentative {i+1} ---")
                try:
                    # 1. Login to get Auth Cookie (L'Owner est créé par ENV)
                    cookie = None
                    try:
                        admin_pwd = os.environ.get("N8N_INSTANCE_OWNER_PASSWORD", "ECHO_System_123!")
                        login_resp = await client.post(f"{N8N_URL}/rest/login", json={
                            "emailOrLdapLoginId": "system@echo.local",
                            "password": admin_pwd
                        })
                        N8N_AUTH_DEBUG_LOGS.append(f"Login HTTP {login_resp.status_code} : {login_resp.text[:100]}")
                        cookie = login_resp.cookies.get("n8n-auth")
                    except Exception as ex2:
                        N8N_AUTH_DEBUG_LOGS.append(f"Login Exception : {ex2}")
                        await asyncio.sleep(5)
                        continue

                    if cookie:
                        # 3. Check for existing API keys
                        keys_resp = await client.get(f"{N8N_URL}/rest/api-keys", cookies={"n8n-auth": cookie})
                        N8N_AUTH_DEBUG_LOGS.append(f"Get Keys HTTP {keys_resp.status_code}")
                        if keys_resp.status_code == 200:
                            try:
                                json_resp = keys_resp.json()
                                if isinstance(json_resp, dict) and "data" in json_resp:
                                    keys_data = json_resp["data"]
                                elif isinstance(json_resp, list):
                                    keys_data = json_resp
                                else:
                                    keys_data = []
                                if isinstance(keys_data, dict) and "items" in keys_data:
                                    keys_data = keys_data["items"]

                                if isinstance(keys_data, list):
                                    for k in keys_data:
                                        if isinstance(k, dict) and "id" in k:
                                            await client.delete(f"{N8N_URL}/rest/api-keys/{k['id']}", cookies={"n8n-auth": cookie})
                                    if keys_data:
                                        N8N_AUTH_DEBUG_LOGS.append(f"Clés existantes purgées ({len(keys_data)}).")
                                    else:
                                        N8N_AUTH_DEBUG_LOGS.append("Aucune clé existante à purger.")
                                else:
                                    N8N_AUTH_DEBUG_LOGS.append(f"Format API keys non reconnu après unwrap (type={type(keys_data).__name__}), purge ignorée.")
                            except Exception as parse_e:
                                N8N_AUTH_DEBUG_LOGS.append(f"Erreur mineure ignorée lors de la purge: {parse_e}")

                        # 4. Create new API key with required scopes (N8N 2.x Zod validation)
                        expires_at = int(time.time()) + (10 * 365 * 24 * 60 * 60)  # 10 ans
                        scopes_resp = await client.get(f"{N8N_URL}/rest/api-keys/scopes", cookies={"n8n-auth": cookie})

                        if scopes_resp.status_code == 200:
                            scopes_json = scopes_resp.json()
                            if isinstance(scopes_json, dict) and "data" in scopes_json:
                                owner_scopes = scopes_json["data"]
                            elif isinstance(scopes_json, list):
                                owner_scopes = scopes_json
                            else:
                                owner_scopes = scopes_json
                            N8N_AUTH_DEBUG_LOGS.append(f"Récupération dynamique des scopes réussie ({len(owner_scopes)} scopes).")
                        else:
                            N8N_AUTH_DEBUG_LOGS.append(f"Échec GET /scopes: HTTP {scopes_resp.status_code}. Fallback minimaliste.")
                            owner_scopes = ["workflow:create", "workflow:read", "workflow:update", "workflow:delete", "workflow:list"]

                        N8N_AUTH_DEBUG_LOGS.append(f"Scopes envoyés : {owner_scopes}")
                        create_resp = await client.post(
                            f"{N8N_URL}/rest/api-keys",
                            json={
                                "label": "ECHO_MASTER_KEY",
                                "expiresAt": expires_at,
                                "scopes": owner_scopes
                            },
                            cookies={"n8n-auth": cookie}
                        )
                        N8N_AUTH_DEBUG_LOGS.append(f"Create Key HTTP {create_resp.status_code} : {create_resp.text[:200]}")
                        if create_resp.status_code in (200, 201):
                            resp_data = create_resp.json()
                            key_obj = resp_data.get("data", resp_data) if isinstance(resp_data, dict) else resp_data
                            N8N_MASTER_API_KEY = key_obj.get("rawApiKey") if isinstance(key_obj, dict) else None
                            if N8N_MASTER_API_KEY:
                                N8N_AUTH_DEBUG_LOGS.append(f"Clé API créée et secrète récupérée. Scopes retenus : {owner_scopes}")
                                return N8N_MASTER_API_KEY
                            else:
                                N8N_AUTH_DEBUG_LOGS.append(f"Clé créée mais extraction échouée (rawApiKey introuvable): {resp_data}")
                    else:
                        N8N_AUTH_DEBUG_LOGS.append("Aucun cookie n8n-auth retourné par le login. Nouvelle tentative dans 3s...")
                        await asyncio.sleep(3)
                        continue

                except Exception as e:
                    N8N_AUTH_DEBUG_LOGS.append(f"Exception globale boucle : {e}")
                    await asyncio.sleep(3)

    return None

class ExecuteRequest(BaseModel):
    user_id: str
    chat_id: str
    n8n_workflow_id: str
    workflow_json: str  # JSON format string
    sync: bool = False

class PruneRequest(BaseModel):
    active_ids: list[str]

@app.get("/system-info")
async def get_documentation():
    doc_path = Path("/app/backend/echo_libs/n8n_architecture.md")
    # Dans le contexte du worker, le fichier est copié avec le script
    if not doc_path.exists():
        doc_path = Path(__file__).parent / "n8n_architecture.md"
        
    doc_content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else "Documentation introuvable."
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "n8n", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        version = stdout.decode().strip()
    except Exception:
        version = "unknown"
        
    return {
        "version": version,
        "documentation": doc_content
    }

def replace_download_dir(data: Any, target_dir: str) -> Any:
    """Remplace de manière récursive la constante par le chemin cible."""
    if isinstance(data, dict):
        return {k: replace_download_dir(v, target_dir) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_download_dir(item, target_dir) for item in data]
    elif isinstance(data, str):
        return data.replace("__ECHO_DOWNLOAD_DIR__", target_dir)
    return data

async def _run_n8n_process(req: ExecuteRequest, target_dir: Path, tmp_file: Path, chat_dir: Path, execution_id: str = ""):
    process = None
    status = "error"
    stdout = ""
    stderr = ""
    exec_suffix = f"_{execution_id}" if execution_id else f"_{uuid.uuid4().hex[:8]}"
    isolated_user_folder = target_dir / f".n8n_isolated{exec_suffix}"
    try:
        # Exécution de N8N
        # On utilise asyncio.create_subprocess_exec pour ne pas bloquer l'event loop
        # On définit N8N_PORT et N8N_RUNNERS_BROKER_PORT aléatoires pour éviter tout conflit avec le démon
        env = os.environ.copy()
        env["N8N_PORT"] = str(random.randint(10000, 30000))
        env["N8N_RUNNERS_BROKER_PORT"] = str(random.randint(30001, 50000))
        env["N8N_USER_FOLDER"] = str(isolated_user_folder)
        
        import_proc = await asyncio.create_subprocess_exec(
            "npx", "n8n", "import:workflow", f"--input={str(tmp_file)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        import_stdout, import_stderr = await import_proc.communicate()
        if import_proc.returncode != 0:
            raise Exception(f"Import Workflow Failed: {import_stderr.decode('utf-8', errors='replace')} | STDOUT: {import_stdout.decode('utf-8', errors='replace')}")

        process = await asyncio.create_subprocess_exec(
            "npx", "n8n", "execute", "--id", req.n8n_workflow_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        
        if req.n8n_workflow_id not in active_executions:
            active_executions[req.n8n_workflow_id] = []
        active_executions[req.n8n_workflow_id].append(process)
        
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        
        if process.returncode == 0:
            status = "success"
        
        # Fusion des logs dans un unique fichier d'état
        report = {
            "workflow_id": req.n8n_workflow_id,
            "status": status,
            "stdout": stdout.strip(),
            "stderr": stderr.strip()
        }
        (target_dir / "execution_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        
    except Exception as e:
        status = "error"
        stdout = ""
        stderr = str(e)
        if target_dir.exists():
            report = {
                "workflow_id": req.n8n_workflow_id,
                "status": status,
                "stdout": "",
                "stderr": stderr.strip()
            }
            (target_dir / "execution_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        # Nettoyage
        if process and process.returncode is None:
            try:
                process.kill()
            except Exception:
                pass
        if process and req.n8n_workflow_id in active_executions and process in active_executions[req.n8n_workflow_id]:
            try:
                active_executions[req.n8n_workflow_id].remove(process)
            except ValueError:
                pass
        if tmp_file.exists():
            tmp_file.unlink(missing_ok=True)
        if isolated_user_folder.exists():
            shutil.rmtree(isolated_user_folder, ignore_errors=True)
            
    return status, stdout, stderr

@app.post("/execute")
async def execute_workflow(req: ExecuteRequest, background_tasks: BackgroundTasks):
    # Sécurisation des chemins
    base_downloads = Path("/home/node/.n8n-files")
    user_dir = base_downloads / req.user_id
    chat_dir = user_dir / req.chat_id
    # Identifiant unique d'exécution pour isoler les requêtes concurrentes sur un même workflow
    execution_id = uuid.uuid4().hex[:8]
    target_dir = chat_dir / "n8n" / req.n8n_workflow_id
    
    # Création du dossier éphémère
    target_dir.mkdir(parents=True, exist_ok=True)
    
    tmp_file = Path(f"/tmp/{req.n8n_workflow_id}_{execution_id}.json")
    
    try:
        # Parsing et remplacement du marqueur
        workflow_data = json.loads(req.workflow_json)
        workflow_data["id"] = req.n8n_workflow_id
        if "name" not in workflow_data:
            workflow_data["name"] = f"ECHO_Oneshot_{req.n8n_workflow_id}"
        workflow_data = replace_download_dir(workflow_data, str(target_dir))
        
        # Ecriture du fichier temporaire pour n8n
        tmp_file.write_text(json.dumps(workflow_data, ensure_ascii=False), encoding="utf-8")
        
        if req.sync:
            # Mode Synchrone : on attend la fin
            status, stdout, stderr = await _run_n8n_process(req, target_dir, tmp_file, chat_dir, execution_id=execution_id)
            return {
                "status": status,
                "stdout": stdout,
                "stderr": stderr
            }
        else:
            # Mode Asynchrone : Fire & Forget
            background_tasks.add_task(_run_n8n_process, req, target_dir, tmp_file, chat_dir, execution_id)
            return {
                "status": "started",
                "execution_id": execution_id,
                "message": "Workflow started in background. Check Download Broker for output files."
            }
            
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in workflow_json")
    except Exception as e:
        return {
            "status": "error",
            "stdout": "",
            "stderr": str(e)
        }

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/deploy")
async def deploy_workflow(req: ExecuteRequest):
    base_downloads = Path("/home/node/.n8n-files")
    chat_dir = base_downloads / req.user_id / req.chat_id
    target_dir = chat_dir / "n8n" / req.n8n_workflow_id
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        workflow_data = json.loads(req.workflow_json)
        workflow_data = replace_download_dir(workflow_data, str(target_dir))

        # Retirer "active" du payload (read-only dans CreateWorkflowDto N8N v2.x).
        workflow_data.pop("active", None)

        # Garantir la présence du champ "settings" (requis par baseWorkflowShape N8N v2.x).
        if "settings" not in workflow_data:
            workflow_data["settings"] = {}

        owner_prefix = f"[ECHO:{req.user_id}] "
        raw_name = workflow_data.get("name") or f"ECHO_Daemon_{req.n8n_workflow_id}"
        if not str(raw_name).startswith("[ECHO:"):
            workflow_data["name"] = f"{owner_prefix}{raw_name}"
        else:
            workflow_data["name"] = str(raw_name)

        api_key = await get_or_create_n8n_api_key()
        headers = {"X-N8N-API-KEY": api_key} if api_key else {}

        async with httpx.AsyncClient(timeout=30.0) as client:
            # ÉTAPE 1 : Création du workflow (état inactif par défaut côté N8N)
            resp = await client.post(
                f"{N8N_URL}/api/v1/workflows",
                json=workflow_data,
                headers=headers
            )

            if resp.status_code not in (200, 201):
                _write_deploy_report(chat_dir, req.n8n_workflow_id, "error",
                                     detail=resp.text)
                return {
                    "status": "error",
                    "detail": resp.text,
                    "auth_debug_logs": N8N_AUTH_DEBUG_LOGS
                }

            created = resp.json()
            workflow_id = created.get("id")
            if not workflow_id:
                _write_deploy_report(chat_dir, req.n8n_workflow_id, "error",
                                     detail="N8N n'a pas retourné d'ID après création.")
                return {
                    "status": "error",
                    "detail": "N8N n'a pas retourné d'ID de workflow après création.",
                    "auth_debug_logs": N8N_AUTH_DEBUG_LOGS
                }

            # ÉTAPE 2 : Activation via POST /api/v1/workflows/{id}/activate
            # Déprécié depuis 2026-07-23 mais alias fonctionnel de publishWorkflow sur v2.34.6.
            activate_resp = await client.post(
                f"{N8N_URL}/api/v1/workflows/{workflow_id}/activate",
                json={},
                headers=headers
            )

            if activate_resp.status_code in (200, 201):
                _write_deploy_report(chat_dir, req.n8n_workflow_id, "success",
                                     n8n_id=workflow_id)
                return {"status": "success", "n8n_id": workflow_id}

            # Workflow créé mais non activé : warning non fatal.
            warn_detail = (
                f"Workflow créé (id={workflow_id}) mais activation échouée "
                f"(HTTP {activate_resp.status_code}) : {activate_resp.text}"
            )
            _write_deploy_report(chat_dir, req.n8n_workflow_id, "warning",
                                 n8n_id=workflow_id, detail=warn_detail)
            return {
                "status": "warning",
                "n8n_id": workflow_id,
                "detail": warn_detail
            }

    except Exception as e:
        _write_deploy_report(chat_dir, req.n8n_workflow_id, "error",
                             detail=str(e))
        return {
            "status": "error",
            "detail": str(e),
            "auth_debug_logs": getattr(sys.modules[__name__], 'N8N_AUTH_DEBUG_LOGS', [])
        }


def _write_deploy_report(chat_dir: Path, echo_wf_id: str, status: str,
                         n8n_id: str = None, detail: str = None):
    """Écrit un rapport de déploiement dans chat_dir pour ingestion par le Download Broker.
    Convention : {fid}_{echo_wf_id}_deployment_report.json (compatible Download Broker)."""
    report = {"workflow_id": echo_wf_id, "status": status}
    if n8n_id:
        report["n8n_id"] = n8n_id
    if detail:
        report["detail"] = detail
    fid = uuid.uuid4().hex[:8]
    filename = f"{fid}_{echo_wf_id}_deployment_report.json"
    try:
        (chat_dir / filename).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # Pas de crash — le retour API reste informatif


@app.delete("/workflow/{workflow_id}")
async def delete_workflow(workflow_id: str):
    killed_count = 0
    if workflow_id in active_executions:
        for proc in active_executions[workflow_id]:
            try:
                proc.kill()
                killed_count += 1
            except Exception:
                pass
        active_executions[workflow_id].clear()
        
    api_deleted = False
    try:
        api_key = await get_or_create_n8n_api_key()
        headers = {"X-N8N-API-KEY": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(f"{N8N_URL}/api/v1/workflows/{workflow_id}", headers=headers)
            if resp.status_code in (200, 204):
                api_deleted = True
    except Exception:
        pass
            
    return {"status": "success", "killed_processes": killed_count, "api_deleted": api_deleted}

@app.post("/prune")
async def prune_workflows(req: PruneRequest):
    try:
        api_key = await get_or_create_n8n_api_key()
        headers = {"X-N8N-API-KEY": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{N8N_URL}/api/v1/workflows", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                for wf in data.get("data", []):
                    w_id = wf.get("id")
                    if req.active_ids and w_id not in req.active_ids:
                        await client.delete(f"{N8N_URL}/api/v1/workflows/{w_id}", headers=headers)
                    elif not req.active_ids:
                        await client.delete(f"{N8N_URL}/api/v1/workflows/{w_id}", headers=headers)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
