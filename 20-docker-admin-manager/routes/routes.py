# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - FLASK ROUTES
VERSION : 5.137
================================================================================
"""
import configparser
import datetime
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from flask import (  # pyright: ignore[reportMissingImports]
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename  # pyright: ignore[reportMissingImports]
from core.config import (
    BACKUP_DIR,
    DEFAULT_MAINT_CONFIG,
    DOCKER_AVAILABLE,
    ECHO_USERS_ROOT,
    HAS_PSUTIL,
    HOST_GATEWAY,
    MAINTENANCE_STATE,
    MCP_CACHE_DIR,
    N8N_DATA_DIR,
    OWUI_ADMIN_SECRET_PATH,
    OWUI_DATA_ROOT,
    RCLONE_CONF_DIR,
    RCLONE_CONF_PATH,
    SYSTEM_ADMIN_EMAIL,
    TARGET_CONTAINER,
    UPLOADS_DIR,
    WEBUI_DB_PATH,
    app,
    docker,
    paramiko,
    psutil,
)
from core.utils import (
    get_cpu_model_name,
    get_dir_stats,
    get_echo_version,
    human_size,
)
from core.settings_io import (
    get_auth_settings,
    get_rclone_remotes,
    load_maint_config,
    load_maint_history,
    load_settings,
    save_auth_settings,
    save_maint_config,
    save_maint_report,
    save_settings,
)
from core.security import (
    change_system_password,
    generate_complex_temp_password,
)
from services.memory_consolidation import consolidate_memories_for_user
from services.semantic_pruning import purge_user_artifacts, run_semantic_pruning
from services.maintenance_backup import (
    get_backup_list,
    perform_backup_task,
    update_system_schedules,
)
from templates.templates import HTML_DASHBOARD, HTML_LOGIN

@app.route('/api/task_status')
def get_task_status():
    return jsonify(MAINTENANCE_STATE)

@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    
    # Volume Total (Docs + Bases)
    vault_storage = get_dir_stats(ECHO_USERS_ROOT)
    
    # Compteur OWUI SQLite (Sessions Actives BDD)
    owui_sessions_count = 0
    try:
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True) as conn:
            owui_sessions_count = conn.execute("SELECT COUNT(id) FROM chat").fetchone()[0]
    except Exception: pass

    # Compteur de Sessions Réelles (Uniquement session.db à la racine de chaque dossier chat)
    real_session_count = len(glob.glob(os.path.join(ECHO_USERS_ROOT, "*", "chats", "*", "session.db")))

    # Pourcentage d'utilisation du volume Docker (echo-webui-data monté sur OWUI_DATA_ROOT)
    vault_disk_percent = 0
    if HAS_PSUTIL:
        try:
            vd = psutil.disk_usage(OWUI_DATA_ROOT)
            vault_disk_percent = vd.percent
        except Exception: pass

    # Taille totale du dossier de backups
    backup_stats = get_dir_stats(BACKUP_DIR)

    stats = {
        "uploads": get_dir_stats(UPLOADS_DIR),
        "vault": vault_storage,
        "vault_disk_percent": vault_disk_percent,
        "logs": get_dir_stats(os.path.join(OWUI_DATA_ROOT, "debug_logs")),
        "owui_sessions": owui_sessions_count,
        "real_sessions": real_session_count,
        "backup_size": backup_stats.get("size_fmt", "N/A")
    }
    return render_template_string(HTML_DASHBOARD, settings=load_settings(), 
                                storage_stats=stats, maint=load_maint_config(),
                                auth_settings=get_auth_settings(),
                                version=get_echo_version(), user=session.get('username'),
                                backups=get_backup_list(), history=load_maint_history(),
                                rclone_remotes=get_rclone_remotes(),
                                server_time_iso=datetime.datetime.now().isoformat())

@app.route('/api/maint/history')
def maint_history():
    if not session.get('logged_in'): return jsonify([]), 403
    return jsonify(load_maint_history())

@app.route('/', methods=['POST'])
def login():
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=request.form.get('username'), password=request.form.get('password'), timeout=5)
        ssh.close(); session['logged_in'] = True; session['username'] = request.form.get('username')
    except Exception: flash('Authentification échouée.', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    # Si la requête vient du WAF (BunkerWeb), on fait un logout SSO global
    if request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For"):
        domain = os.environ.get("ECHO_DOMAIN", "localhost")
        if domain != "localhost":
            return redirect(f"https://auth.{domain}/logout?next=https://am.{domain}/")
    # Sinon (accès direct LAN), on ramène simplement sur la mire SSH locale
    return redirect(url_for('index'))

@app.route('/api/user_stats')
def user_stats():
    if not session.get('logged_in'): return jsonify([])
    data = []
    try:
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True) as conn:
            users = conn.execute("SELECT id, name, email, role FROM user ORDER BY name ASC").fetchall()
            for u in users:
                count = conn.execute("SELECT COUNT(id) FROM chat WHERE user_id = ?", (u[0],)).fetchone()[0]
                data.append({"id": u[0], "name": u[1], "email": u[2], "role": u[3], "chat_count": count})
    except Exception: pass
    return jsonify(data)

@app.route('/api/admin/password')
def admin_password():
    if not session.get('logged_in'): return jsonify({}), 403
    try:
        if os.path.exists(OWUI_ADMIN_SECRET_PATH):
            with open(OWUI_ADMIN_SECRET_PATH, 'r') as f: return jsonify({"password": f.read().strip()})
    except Exception: pass
    return jsonify({"password": "N/A"})

@app.route('/api/backups')
def api_backups():
    if not session.get('logged_in'): return jsonify([]), 403
    return jsonify(get_backup_list())

@app.route('/download/<filename>')
def download_backup(filename):
    if not session.get('logged_in'): return redirect(url_for('index'))
    path = os.path.join(BACKUP_DIR, secure_filename(filename))
    if os.path.exists(path): return send_file(path, as_attachment=True)
    flash('Fichier introuvable.', 'danger')
    return redirect(url_for('index'))

@app.route('/api/stats')
def sys_stats():
    if not session.get('logged_in'): return jsonify({}), 403
    if not HAS_PSUTIL: return jsonify({})
    mem = psutil.virtual_memory(); disk = psutil.disk_usage(BACKUP_DIR)
    return jsonify({
        "cpu_percent": psutil.cpu_percent(), "cpu_count": os.cpu_count(),
        "cpu_model": get_cpu_model_name(), "cpu_load": [round(l, 2) for l in os.getloadavg()],
        "ram_percent": mem.percent, "ram_used": human_size(mem.used), "ram_total": human_size(mem.total),
        "disk_percent": disk.percent, "disk_used": human_size(disk.used), "disk_total": human_size(disk.total)
    })

@app.route('/api/containers')
def containers():
    if not session.get('logged_in'): return jsonify([]), 403
    if not DOCKER_AVAILABLE: return jsonify([])
    try:
        client = docker.from_env()
        conts = client.containers.list(all=True)
        stats_map = {}
        
        # Pre-fetch sizes using low-level API since inspect_container lacks 'size' param
        sizes = {}
        try:
            for c_raw in client.api.containers(all=True, size=True):
                sizes[c_raw.get('Id')] = c_raw.get('SizeRw', 0)
        except Exception:
            pass
        
        import concurrent.futures
        def get_stats(c):
            try:
                size_rw = sizes.get(c.id, 0)
                if c.status != 'running': return c.name, '0.00%', '0.00%', size_rw
                st = c.stats(stream=False)
                m = st.get('memory_stats', {})
                mem_perc = f"{(m.get('usage',0)/m.get('limit',1)*100):.2f}%" if m.get('limit') else '0.00%'
                cs = st.get('cpu_stats', {})
                pcs = st.get('precpu_stats', {})
                cd = cs.get('cpu_usage',{}).get('total_usage',0) - pcs.get('cpu_usage',{}).get('total_usage',0)
                sd = cs.get('system_cpu_usage',0) - pcs.get('system_cpu_usage',0)
                cpu_perc = f"{(cd/sd)*cs.get('online_cpus',1)*100:.2f}%" if sd>0 and cd>0 else '0.00%'
                return c.name, cpu_perc, mem_perc, size_rw
            except Exception: return c.name, '0.00%', '0.00%', sizes.get(c.id, 0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(get_stats, c) for c in conts]
            for f in concurrent.futures.as_completed(futures):
                name, cpu, ram, size_rw = f.result()
                stats_map[name] = {'cpu': cpu, 'ram': ram, 'size_rw': size_rw}

        volume_sizes = {}
        try:
            if hasattr(client.api, 'df'):
                df_data = client.api.df()
                for v in df_data.get('Volumes', []):
                    vol_name = v.get('Name')
                    vol_size = v.get('UsageData', {}).get('Size', 0)
                    if vol_name and vol_size >= 0:
                        volume_sizes[vol_name] = vol_size
        except Exception:
            pass

        data = []
        disk_total = psutil.disk_usage('/').total if HAS_PSUTIL else 50_000_000_000
        for c in conts:
            c_stats = stats_map.get(c.name, {'cpu': '0.00%', 'ram': '0.00%', 'size_rw': 0})
            size_rw = c_stats['size_rw']
            size_perc = (size_rw / disk_total * 100) if disk_total > 0 else 0
            started_at = c.attrs.get('State', {}).get('StartedAt', '')
            
            volumes_info = []
            total_volumes_bytes = 0
            for m in c.attrs.get('Mounts', []):
                m_type = m.get('Type')
                m_dest = m.get('Destination', '')
                if m_type == 'volume':
                    m_name = m.get('Name', '')
                    m_size = volume_sizes.get(m_name, 0)
                    total_volumes_bytes += m_size
                    m_size_str = human_size(m_size) if m_size > 0 else "< 1 KB"
                    m_size_perc = (m_size / disk_total * 100) if disk_total > 0 else 0
                    volumes_info.append({"dest": m_dest, "size_str": m_size_str, "size_perc": m_size_perc, "type": "volume"})
                elif m_type == 'bind':
                    volumes_info.append({"dest": m_dest, "size_str": "Bind Local", "size_perc": 0, "type": "bind"})

            data.append({
                "id": c.short_id,
                "name": c.name,
                "status": c.status.capitalize(),
                "cpu": c_stats['cpu'],
                "ram": c_stats['ram'],
                "size_bytes": size_rw,
                "size": human_size(size_rw) if size_rw else "N/A",
                "size_perc": size_perc,
                "started_at": started_at,
                "total_volumes_bytes": total_volumes_bytes,
                "volumes": volumes_info
            })
        return jsonify(data)
    except Exception as e:
        print(f"Containers error: {e}")
        return jsonify([])

@app.route('/action/<action>', methods=['POST'])
def handle_action(action):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if action == 'backup':
        if MAINTENANCE_STATE.get("is_running"):
            flash('Une opération de maintenance ou de sauvegarde est déjà en cours.', 'warning')
        else:
            threading.Thread(target=perform_backup_task, kwargs={'record_report': True}, daemon=True).start()
            flash('Sauvegarde complète lancée en arrière-plan.', 'info')
    elif action == 'pruning':
        if MAINTENANCE_STATE.get("is_running"):
            flash('Une opération de maintenance ou de sauvegarde est déjà en cours.', 'warning')
        else:
            threading.Thread(target=run_semantic_pruning, daemon=True).start()
            flash('Élagage sémantique lancé.', 'info')
    elif action == 'consolidate':
        if MAINTENANCE_STATE.get("is_running"):
            flash('Une opération est déjà en cours.', 'warning')
            return redirect(url_for('index'))
        def _run_consolidation():
            MAINTENANCE_STATE["is_running"] = True
            MAINTENANCE_STATE["task_type"] = "consolidate"
            MAINTENANCE_STATE["start_time"] = time.time()
            MAINTENANCE_STATE["percent"] = 5
            MAINTENANCE_STATE["status"] = "Consolidation Lvl1 → Lvl2 : Lecture des utilisateurs..."
            try:
                cfg = load_maint_config()
                total_promoted = 0
                total_deleted = 0
                total_users = 0
                if not os.path.exists(WEBUI_DB_PATH):
                    print("[ECHO-CONSOLIDATION] \u274c Base SSoT webui.db absente. Abandon."); return
                try:
                    conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=10.0)
                    valid_ids = [str(row[0]) for row in conn.execute("SELECT id FROM user").fetchall()]
                    conn.close()
                except Exception as e:
                    print(f"[ECHO-CONSOLIDATION] \u274c Lecture DB: {e}"); return
                if not valid_ids:
                    print("[ECHO-CONSOLIDATION] \u274c Table user vide. Abandon."); return
                n_users = max(1, len(valid_ids))
                for idx, uid in enumerate(valid_ids, 1):
                    MAINTENANCE_STATE["percent"] = round(10 + (idx / n_users) * 85, 1)
                    MAINTENANCE_STATE["status"] = f"Consolidation Lvl1 → Lvl2 : Utilisateur {idx}/{n_users}..."
                    result = consolidate_memories_for_user(uid, cfg)
                    promoted = result.get("points_promoted", 0)
                    if promoted > 0 or result.get("points_deleted", 0) > 0:
                        total_promoted += promoted
                        total_deleted += result.get("points_deleted", 0)
                        total_users += 1
                print(f"[ECHO-CONSOLIDATION] \u2705 Terminé : {total_promoted} clusters promus en lvl2.")
                
                report_html = f"""
                <div style='line-height: 1.4;'>
                    <b><i class='bi bi-diagram-3 text-warning'></i> Consolidation Lvl1 → Lvl2</b><br>
                    👤 <b>Utilisateurs traités</b> : {total_users}<br>
                    🎯 <b>Souvenirs Lvl2 (Promus)</b> : {total_promoted}<br>
                    🗑️ <b>Souvenirs Lvl1 (Purgés)</b> : {total_deleted}
                </div>
                """
                save_maint_report(report_html)
            finally:
                MAINTENANCE_STATE["is_running"] = False
                MAINTENANCE_STATE["task_type"] = ""
                MAINTENANCE_STATE["status"] = ""
                MAINTENANCE_STATE["percent"] = 0
                MAINTENANCE_STATE["start_time"] = None
        threading.Thread(target=_run_consolidation, daemon=True).start()
        flash('Consolidation des souvenirs Triviaux lancée.', 'info')
    elif action == 'docker_prune':
        def _docker_prune():
            freed_msg = ""
            if DOCKER_AVAILABLE:
                try:
                    client = docker.from_env()
                    total_reclaimed = 0
                    try:
                        res_img = client.api.prune_images(filters={'dangling': True})
                        if res_img and res_img.get('SpaceReclaimed'):
                            total_reclaimed += res_img['SpaceReclaimed']
                    except Exception as e:
                        print(f"[ECHO-PRUNE] Erreur API Docker prune_images: {e}")
                    try:
                        res_bld = client.api.prune_builds(all=True)
                        if res_bld and res_bld.get('SpaceReclaimed'):
                            total_reclaimed += res_bld['SpaceReclaimed']
                    except Exception as e:
                        print(f"[ECHO-PRUNE] Erreur API Docker prune_builds: {e}")
                    if total_reclaimed > 0:
                        freed_msg = f"Libéré: {total_reclaimed / 1024**2:.2f} MB"
                except Exception as e:
                    print(f"[ECHO-PRUNE] Erreur API Docker globale: {e}")
            try:
                subprocess.run(['apt-get', 'clean'], capture_output=True, timeout=30)
            except Exception:
                pass
            print(f"[ECHO-PRUNE] Terminé. {freed_msg}")
        threading.Thread(target=_docker_prune, daemon=True).start()
        flash('Purge du cache Docker et APT lancée.', 'info')
    elif action == 'restart':
        cid = request.form.get('container')
        if cid and DOCKER_AVAILABLE:
            try: docker.from_env().containers.get(cid).restart(); flash('Redémarré.', 'success')
            except Exception: pass
    elif action == 'delete_backup':
        f = request.form.get('filename')
        if f and os.path.exists(p := os.path.join(BACKUP_DIR, secure_filename(f))): os.remove(p); flash('Supprimé.', 'warning')
    elif action == 'restore':
        f = request.form.get('filename')
        if f and DOCKER_AVAILABLE:
            p = os.path.join(BACKUP_DIR, secure_filename(f))
            if not os.path.exists(p):
                flash('Erreur: Fichier de sauvegarde introuvable.', 'danger')
                return redirect(url_for('index'))
            stopped_containers = []
            try:
                client = docker.from_env()
                for c_name in (TARGET_CONTAINER, 'echo-qdrant', 'echo-n8n-worker', 'echo-mcp-broker'):
                    try:
                        c_obj = client.containers.get(c_name)
                        c_obj.stop()
                        stopped_containers.append(c_obj)
                    except Exception:
                        pass
                
                list_cmd = subprocess.run(['tar', '-tf', p], capture_output=True, text=True, check=True)
                files_list = list_cmd.stdout.split('\n')
                if any(f.startswith('app/backend/data') for f in files_list):
                    shutil.rmtree('/app/backend/data', ignore_errors=True)
                    os.makedirs('/app/backend/data', exist_ok=True)
                    shutil.rmtree('/qdrant/storage', ignore_errors=True)
                    os.makedirs('/qdrant/storage', exist_ok=True)
                    shutil.rmtree('/app/auth-data', ignore_errors=True)
                    os.makedirs('/app/auth-data', exist_ok=True)
                    if any(f.startswith('app/n8n-data') for f in files_list):
                        shutil.rmtree(N8N_DATA_DIR, ignore_errors=True)
                        os.makedirs(N8N_DATA_DIR, exist_ok=True)
                    if any(f.startswith('app/mcp_cache') for f in files_list):
                        shutil.rmtree(MCP_CACHE_DIR, ignore_errors=True)
                        os.makedirs(MCP_CACHE_DIR, exist_ok=True)
                    subprocess.run(['tar', '-xf', p, '-C', '/'], check=True)
                else:
                    shutil.rmtree(OWUI_DATA_ROOT, ignore_errors=True)
                    os.makedirs(OWUI_DATA_ROOT, exist_ok=True)
                    subprocess.run(['tar', '-xf', p, '-C', OWUI_DATA_ROOT], check=True)
                
                flash('Restauration terminée.', 'success')
            except Exception as e:
                flash(f'Erreur: {e}', 'danger')
            finally:
                for c_obj in stopped_containers:
                    try:
                        c_obj.start()
                    except Exception:
                        pass
    return redirect(url_for('index'))

@app.route('/action/user_role/<user_id>', methods=['POST'])
def update_user_role(user_id):
    if not session.get('logged_in'): return redirect(url_for('index'))
    new_role = request.form.get('new_role')
    if new_role not in ['admin', 'user']:
        flash('Rôle invalide.', 'danger')
        return redirect(url_for('index'))
        
    try:
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True) as conn:
            user = conn.execute("SELECT email, role FROM user WHERE id = ?", (user_id,)).fetchone()
            if not user:
                flash('Utilisateur introuvable.', 'danger')
                return redirect(url_for('index'))
                
            email, current_role = user[0], user[1]
            if email.endswith('@echo.local'):
                flash('Action interdite sur un compte système.', 'danger')
                return redirect(url_for('index'))
                
            if current_role == 'admin' and new_role == 'user':
                admins = conn.execute("SELECT email FROM user WHERE role = 'admin'").fetchall()
                human_admins = [a[0] for a in admins if not a[0].endswith('@echo.local')]
                if len(human_admins) <= 1 and email in human_admins:
                    flash('Action bloquée : Rétrogradation impossible du dernier administrateur humain.', 'danger')
                    return redirect(url_for('index'))
            
            conn.execute("UPDATE user SET role = ? WHERE id = ?", (new_role, user_id))
            conn.commit()
            flash(f"Rôle de {email} mis à jour en {new_role}.", "success")
    except Exception as e:
        flash(f'Erreur de mise à jour du rôle: {e}', 'danger')
        
    return redirect(url_for('index'))

@app.route('/action/auth_reset/<user_id>', methods=['POST'])
def auth_reset_user(user_id):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if not os.path.exists(WEBUI_DB_PATH):
        flash('Base SSoT Open WebUI injoignable. Action annulée.', 'danger')
        return redirect(url_for('index'))
    try:
        conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=10.0)
        user = conn.execute("SELECT name, email FROM user WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if user and (user[1].endswith('@echo.local') or user[0] in ['admin', 'install-stack']):
            flash('Action strictement interdite sur un compte système.', 'danger')
            return redirect(url_for('index'))
    except Exception as e:
        flash(f'Erreur de vérification SSoT: {e}', 'danger')
        return redirect(url_for('index'))

    safe_uid = secure_filename(user_id)
    db_path = os.path.join(ECHO_USERS_ROOT, safe_uid, 'identity.db')
    purged_any = False
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                conn.execute("DELETE FROM auth_data")
            purged_any = True
        except Exception as e:
            flash(f'Erreur lors de la purge identity.db: {e}', 'danger')
            return redirect(url_for('index'))

    # Purge synchrone du cache MCP persistant (MSAL tokens, etc.) pour cet utilisateur
    if os.path.exists(MCP_CACHE_DIR):
        try:
            for srv_entry in os.scandir(MCP_CACHE_DIR):
                if srv_entry.is_dir():
                    user_cache_dir = os.path.join(srv_entry.path, safe_uid)
                    if os.path.exists(user_cache_dir):
                        shutil.rmtree(user_cache_dir, ignore_errors=True)
                        purged_any = True
        except Exception as e:
            print(f"[ECHO-AUTH-RESET] Erreur purge MCP cache ({safe_uid}): {e}")

    if purged_any or os.path.exists(db_path):
        flash('Accès sécurisés (OAuth / API / Cache MCP) purgés pour cet utilisateur.', 'success')
    else:
        flash('Identité introuvable.', 'danger')
    return redirect(url_for('index'))

@app.route('/action/rclone/create', methods=['POST'])
def action_rclone_create():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        os.makedirs(RCLONE_CONF_DIR, exist_ok=True)
        remote_name = secure_filename(request.form.get('remote_name', '').strip())
        if not remote_name: raise ValueError("Nom de profil invalide.")
        provider = request.form.get('provider')
        if provider not in ('drive', 's3', 'webdav'):
            raise ValueError(f"Fournisseur '{provider}' non supporté.")

        cfg = configparser.ConfigParser(interpolation=None)
        if os.path.exists(RCLONE_CONF_PATH):
            cfg.read(RCLONE_CONF_PATH, encoding='utf-8')

        section = {'type': provider}

        if provider == 'drive':
            sa_file = request.files.get('drive_sa_file')
            raw_token = request.form.get('drive_token', '').strip()
            root_folder_id = request.form.get('drive_root_folder_id', '').strip()
            if sa_file and sa_file.filename:
                sf = os.path.join(RCLONE_CONF_DIR, f"{remote_name}_sa.json")
                sa_file.save(sf)
                section['service_account_file'] = sf
            elif raw_token:
                # Nettoyage des sauts de ligne (\r\n) injectés par le copier-coller PowerShell
                flat_token = re.sub(r'[\r\n]+', '', raw_token)
                start_idx = flat_token.find('{')
                end_idx = flat_token.rfind('}')
                if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
                    raise ValueError("Le Token OAuth collé ne contient pas un objet JSON valide ({...}).")
                try:
                    parsed_token = json.loads(flat_token[start_idx:end_idx + 1])
                except Exception as je:
                    raise ValueError(f"Token OAuth JSON malformé : {je}")
                section['token'] = json.dumps(parsed_token, separators=(',', ':'))
            else: raise ValueError("Fournir un Token ou un Service Account JSON.")
            if root_folder_id:
                section['root_folder_id'] = root_folder_id
            section['scope'] = 'drive'

        elif provider == 's3':
            section['provider'] = request.form.get('s3_provider', 'AWS')
            section['access_key_id'] = request.form.get('s3_access_key', '').strip()
            section['secret_access_key'] = request.form.get('s3_secret_key', '').strip()
            s3_reg = request.form.get('s3_region', '').strip()
            if s3_reg: section['region'] = s3_reg
            ep = request.form.get('s3_endpoint', '').strip()
            if ep: section['endpoint'] = ep
            section['env_auth'] = 'false'

        elif provider == 'webdav':
            section['url'] = request.form.get('webdav_url', '').strip()
            section['vendor'] = request.form.get('webdav_vendor', 'other')
            section['user'] = request.form.get('webdav_user', '').strip()
            raw_pass = request.form.get('webdav_pass', '')
            if raw_pass:
                obs = subprocess.run(['rclone', 'obscure', raw_pass], capture_output=True, text=True, timeout=10)
                if obs.returncode != 0:
                    raise ValueError(f"Erreur chiffrement mot de passe Rclone : {obs.stderr}")
                section['pass'] = obs.stdout.strip()

        cfg[remote_name] = section
        with open(RCLONE_CONF_PATH, 'w', encoding='utf-8') as f:
            cfg.write(f)
        sets = load_settings()
        if not sets.get('rclone_remote'):
            save_settings({'rclone_remote': f"{remote_name}:ECHO_Backups"})
        flash(f"Profil Rclone '{remote_name}' créé avec succès.", 'success')

    except subprocess.TimeoutExpired:
        flash("Erreur : Le chiffrement Rclone a expiré.", 'danger')
    except Exception as e: flash(f"Erreur : {e}", 'danger')
    return redirect(url_for('index'))

@app.route('/action/rclone/delete', methods=['POST'])
def action_rclone_delete():
    """Supprime un profil Rclone de la configuration."""
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        remote_name = request.form.get('remote_name', '').strip()
        if not remote_name: raise ValueError("Nom de profil invalide.")
        # Sécurité : interdire la suppression du profil actuellement utilisé
        sets = load_settings()
        active_remote = sets.get('rclone_remote', '')
        if active_remote and active_remote.startswith(f"{remote_name}:"):
            flash(f"Impossible : le profil '{remote_name}' est actuellement utilisé pour l'externalisation. Désactivez-le d'abord.", 'warning')
            return redirect(url_for('index'))
        # Suppression via rclone
        res = subprocess.run(
            ['rclone', '--config', RCLONE_CONF_PATH, 'config', 'delete', remote_name],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0:
            # Nettoyage du fichier Service Account JSON associé s'il existe
            sa_path = os.path.join(RCLONE_CONF_DIR, f"{remote_name}_sa.json")
            if os.path.exists(sa_path): os.remove(sa_path)
            flash(f"Profil Rclone '{remote_name}' supprimé.", 'success')
        else:
            flash(f"Erreur Rclone : {res.stderr}", 'danger')
    except Exception as e: flash(f"Erreur : {e}", 'danger')
    return redirect(url_for('index'))

@app.route('/settings', methods=['POST'])
def save_settings_route():
    if not session.get('logged_in'): return redirect(url_for('index'))
    c_mode = request.form.get('cleanup_mode', "count")
    c_val = max(1, int(request.form.get('cleanup_value', 5)))
    save_settings({
        "auto_backup": 'auto_backup' in request.form,
        "auto_cleanup": True,
        "interval_days": max(1, int(request.form.get('interval_days', 1))),
        "backup_time": request.form.get('backup_time', "03:00"),
        "cleanup_mode": c_mode,
        "cleanup_value": c_val
    })
    # Application immédiate de la politique de rétention sur les archives existantes
    try:
        files = sorted(glob.glob(f"{BACKUP_DIR}/echo_backup_*.tar.*"), key=os.path.getmtime)
        if c_mode == 'count':
            while len(files) > c_val:
                os.remove(files.pop(0))
        elif c_mode == 'days':
            cut = time.time() - (c_val * 86400)
            for f in files:
                if os.path.getmtime(f) < cut:
                    os.remove(f)
    except Exception:
        pass
    flash('Paramètres sauvegardes mis à jour.', 'success')
    return redirect(url_for('index'))

@app.route('/settings/external_backup', methods=['POST'])
def save_ext_backup_route():
    if not session.get('logged_in'): return redirect(url_for('index'))
    rm_name = request.form.get("rclone_remote_name", "").strip()
    rm_sub = request.form.get("rclone_subpath", "").strip()
    full_remote = f"{rm_name}:{rm_sub}" if rm_name else ""
    
    save_settings({
        "ext_mode": request.form.get("ext_mode", "local"),
        "sftp_host": request.form.get("sftp_host", ""),
        "sftp_port": request.form.get("sftp_port", "22"),
        "sftp_user": request.form.get("sftp_user", ""),
        "sftp_pass": request.form.get("sftp_pass", ""),
        "sftp_path": request.form.get("sftp_path", "/backups/echo"),
        "rclone_remote": full_remote,
        "ext_cleanup_mode": request.form.get("ext_cleanup_mode", "count"),
        "ext_cleanup_value": int(request.form.get("ext_cleanup_value", 5))
    })
    flash('Paramètres d\'externalisation mis à jour.', 'success')
    return redirect(url_for('index'))

@app.route('/settings/maintenance', methods=['POST'])
def update_maint():
    if not session.get('logged_in'): return redirect(url_for('index'))
    c = load_maint_config()
    c["cleanup_hour"] = request.form.get("cleanup_hour", "03:00")

    
    if "memory_ttl" not in c: c["memory_ttl"] = {}
    c["memory_ttl"]["lvl1"] = int(request.form.get("ttl_lvl1", 30))
    c["memory_ttl"]["lvl2"] = int(request.form.get("ttl_lvl2", 60))
    c["memory_ttl"]["lvl3"] = int(request.form.get("ttl_lvl3", 180))
    c["memory_ttl"]["lvl4"] = int(request.form.get("ttl_lvl4", 365))
    c["memory_ttl"]["lvl5"] = int(request.form.get("ttl_lvl5", 540))

    # Paramètres de consolidation (exposés dans l'UI)
    c.setdefault("consolidation", DEFAULT_MAINT_CONFIG["consolidation"].copy())
    c["consolidation"]["trigger_threshold"] = int(request.form.get("consol_threshold", 10))
    c["consolidation"]["min_cluster_size"]   = int(request.form.get("consol_min_cluster", 3))
    c["consolidation"]["similarity_threshold"] = float(request.form.get("consol_similarity", 0.75))
    
    c["purge_orphaned_chats"] = 'purge_orphaned_chats' in request.form
    c["purge_orphaned_users"] = 'purge_orphaned_users' in request.form

    ok = save_maint_config(c)
    update_system_schedules()
    if ok:
        if request.form.get("submit_action") == "run":
            threading.Thread(target=run_semantic_pruning).start()
            flash('Configuration sauvegardée et Élagage lancé.', 'info')
        else:
            flash('Cycle de vie et Mémoire mis à jour.', 'success')
    else:
        flash('Erreur : impossible d\'écrire la configuration.', 'danger')
    return redirect(url_for('index'))

@app.route('/action/security/passwd', methods=['POST'])
def passwd_change():
    if not session.get('logged_in'): return redirect(url_for('index'))
    u = session.get('username')
    new_p, conf_p = request.form.get('new_password'), request.form.get('confirm_password')
    if new_p != conf_p: flash('Mots de passe différents.', 'danger')
    else:
        ok, msg = change_system_password(u, request.form.get('current_password'), new_p)
        flash(f'✅ {msg}' if ok else f'❌ {msg}', 'success' if ok else 'danger')
    return redirect(url_for('index'))

@app.route('/api/auth/users')
def auth_users():
    if not session.get('logged_in'): return jsonify([])
    try:
        owui_names = {}
        try:
            if os.path.exists(WEBUI_DB_PATH):
                with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True) as conn2:
                    for r in conn2.execute("SELECT email, name FROM user").fetchall():
                        owui_names[r[0]] = r[1]
        except: pass
        
        with sqlite3.connect("/app/auth-data/auth.db", uri=True) as conn:
            try: conn.execute("ALTER TABLE users ADD COLUMN temp_pass_expires INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN last_enrollment INTEGER DEFAULT 0")
            except: pass
            users = conn.execute("SELECT email, must_enroll, temp_pass_expires, last_enrollment, name FROM users").fetchall()
            
            result = []
            for u in users:
                display_name = owui_names.get(u[0]) or u[4]
                result.append({"email": u[0], "status": "Pending" if u[1] else "Active", "expires": u[2], "last_enrollment": u[3], "name": display_name})
            return jsonify(result)
    except Exception: return jsonify([])

@app.route('/action/auth/create', methods=['POST'])
def auth_create_user():
    if not session.get('logged_in'): return redirect(url_for('index'))
    email = request.form.get('email', '').strip()
    name = request.form.get('name', '').strip()
    if not email or (email.endswith('@echo.local') and email != SYSTEM_ADMIN_EMAIL):
        flash("Email invalide ou réservé au système.", "danger")
        return redirect(url_for('index'))
    
    temp_pwd = generate_complex_temp_password()
    settings = get_auth_settings()
    expire_timestamp = int(time.time()) + (int(settings.get("temp_pass_hours", 24)) * 3600)
    
    try:
        from passlib.hash import argon2
        pass_hash = argon2.hash(temp_pwd)
        os.makedirs("/app/auth-data", exist_ok=True)
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, pass_hash TEXT, totp_secret TEXT, security_question TEXT, security_answer_hash TEXT, must_enroll INTEGER DEFAULT 1, temp_pass_expires INTEGER DEFAULT 0, last_enrollment INTEGER DEFAULT 0)")
            try: conn.execute("ALTER TABLE users ADD COLUMN temp_pass_expires INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN last_enrollment INTEGER DEFAULT 0")
            except: pass
            conn.execute("INSERT INTO users (email, name, pass_hash, must_enroll, temp_pass_expires) VALUES (?, ?, ?, 1, ?)", (email, name, pass_hash, expire_timestamp))
            conn.commit()
        expire_str = datetime.datetime.fromtimestamp(expire_timestamp).strftime('%d/%m/%Y à %H:%M')
        flash(f"Utilisateur {email} créé. Mot de passe temporaire (valable {settings.get('temp_pass_hours', 24)} heures, jusqu'au {expire_str}) : <b>{temp_pwd}</b>", "success")
    except sqlite3.IntegrityError:
        flash(f"L'utilisateur {email} existe déjà.", "warning")
    except Exception as e:
        flash(f"Erreur création Auth: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/action/auth/import', methods=['POST'])
def auth_import_users():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        from passlib.hash import argon2
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True) as conn:
            webui_users = conn.execute("SELECT email, name FROM user").fetchall()
        
        imported = 0
        already_exist = 0
        os.makedirs("/app/auth-data", exist_ok=True)
        settings = get_auth_settings()
        expire_timestamp = int(time.time()) + (int(settings.get("temp_pass_hours", 24)) * 3600)
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, pass_hash TEXT, totp_secret TEXT, security_question TEXT, security_answer_hash TEXT, must_enroll INTEGER DEFAULT 1, temp_pass_expires INTEGER DEFAULT 0, last_enrollment INTEGER DEFAULT 0)")
            try: conn.execute("ALTER TABLE users ADD COLUMN temp_pass_expires INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN last_enrollment INTEGER DEFAULT 0")
            except: pass
            for (email, name) in webui_users:
                if email.endswith('@echo.local'):
                    continue
                if conn.execute("SELECT email FROM users WHERE email=?", (email,)).fetchone():
                    if name:
                        conn.execute("UPDATE users SET name = ? WHERE email = ? AND (name IS NULL OR name = '' OR name != ?)", (name, email, name))
                    already_exist += 1
                else:
                    pass_hash = argon2.hash("ECHO_IMPORTED_PENDING_RESET")
                    if not name: name = email.split('@')[0]
                    conn.execute("INSERT INTO users (email, name, pass_hash, must_enroll, temp_pass_expires) VALUES (?, ?, ?, 1, ?)", (email, name, pass_hash, expire_timestamp))
                    imported += 1
            conn.commit()
            
        flash(f"{imported} utilisateurs importés de WebUI. {already_exist} existaient déjà. Faites un 'Reset MFA' (bouton ↺) pour générer un mot de passe temporaire.", "success")
    except Exception as e:
        flash(f"Erreur Importation: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/action/auth/import_admin', methods=['POST'])
def auth_import_admin():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        from passlib.hash import argon2
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True) as conn:
            user_exists = conn.execute("SELECT email FROM user WHERE email=?", (SYSTEM_ADMIN_EMAIL,)).fetchone()
        
        if not user_exists:
            flash(f"Le compte {SYSTEM_ADMIN_EMAIL} n'existe pas encore dans Open WebUI.", "warning")
            return redirect(url_for('index'))
            
        os.makedirs("/app/auth-data", exist_ok=True)
        settings = get_auth_settings()
        expire_timestamp = int(time.time()) + (int(settings.get("temp_pass_hours", 24)) * 3600)
        
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, pass_hash TEXT, totp_secret TEXT, security_question TEXT, security_answer_hash TEXT, must_enroll INTEGER DEFAULT 1, temp_pass_expires INTEGER DEFAULT 0, last_enrollment INTEGER DEFAULT 0)")
            try: conn.execute("ALTER TABLE users ADD COLUMN temp_pass_expires INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN last_enrollment INTEGER DEFAULT 0")
            except: pass
            
            if conn.execute("SELECT email FROM users WHERE email=?", (SYSTEM_ADMIN_EMAIL,)).fetchone():
                flash(f"Le compte {SYSTEM_ADMIN_EMAIL} existe déjà dans le SSO.", "info")
            else:
                temp_pwd = generate_complex_temp_password()
                pass_hash = argon2.hash(temp_pwd)
                conn.execute("INSERT INTO users (email, name, pass_hash, must_enroll, temp_pass_expires) VALUES (?, ?, ?, 1, ?)", (SYSTEM_ADMIN_EMAIL, "Administrateur Système", pass_hash, expire_timestamp))
                conn.commit()
                expire_str = datetime.datetime.fromtimestamp(expire_timestamp).strftime('%d/%m/%Y à %H:%M')
                flash(f"Compte {SYSTEM_ADMIN_EMAIL} importé avec succès ! Mot de passe temporaire (valable {settings.get('temp_pass_hours', 24)} heures, jusqu'au {expire_str}) : <b>{temp_pwd}</b>", "success")
                
    except Exception as e:
        flash(f"Erreur Importation Admin: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/action/auth/rename', methods=['POST'])
def auth_rename_user():
    if not session.get('logged_in'): return redirect(url_for('index'))
    email = request.form.get('email', '').strip()
    name = request.form.get('name', '').strip()
    if not email:
        return redirect(url_for('index'))
    try:
        # 1. Mise à jour dans le SSO
        with sqlite3.connect("/app/auth-data/auth.db", uri=True) as conn:
            conn.execute("UPDATE users SET name = ? WHERE email = ?", (name if name else None, email))
            
        # 2. Mise à jour synchrone dans Open WebUI
        try:
            if os.path.exists(WEBUI_DB_PATH):
                with sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True) as conn2:
                    conn2.execute("UPDATE user SET name = ? WHERE email = ?", (name if name else "", email))
        except Exception:
            pass
            
        flash(f"Nom de l'utilisateur {email} mis à jour dans le SSO et synchronisé.", "success")
    except Exception as e:
        flash(f"Erreur DB globale : {str(e)}", "danger")
    return redirect(url_for('index'))

@app.route('/action/auth/reset', methods=['POST'])
def auth_reset_mfa():
    if not session.get('logged_in'): return redirect(url_for('index'))
    email = request.form.get('email', '').strip()
    temp_pwd = generate_complex_temp_password()
    settings = get_auth_settings()
    expire_timestamp = int(time.time()) + (int(settings.get("temp_pass_hours", 24)) * 3600)
    try:
        from passlib.hash import argon2
        pass_hash = argon2.hash(temp_pwd)
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            try: conn.execute("ALTER TABLE users ADD COLUMN temp_pass_expires INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN last_enrollment INTEGER DEFAULT 0")
            except: pass
            conn.execute("UPDATE users SET pass_hash=?, totp_secret=NULL, security_question=NULL, security_answer_hash=NULL, must_enroll=1, temp_pass_expires=?, last_enrollment=0 WHERE email=?", (pass_hash, expire_timestamp, email))
            conn.execute("DELETE FROM sessions WHERE email=?", (email,))
            conn.commit()
        expire_str = datetime.datetime.fromtimestamp(expire_timestamp).strftime('%d/%m/%Y à %H:%M')
        flash(f"MFA aboli pour {email}. Nouveau mot de passe temporaire (valable {settings.get('temp_pass_hours', 24)} heures, jusqu'au {expire_str}) : <b>{temp_pwd}</b>", "warning")
    except Exception as e: flash(f"Erreur: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/action/auth/settings', methods=['POST'])
def auth_settings_save():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        settings = get_auth_settings()
        settings["pwd_min_length"] = int(request.form.get("pwd_min_length", 12))
        settings["pwd_require_upper"] = request.form.get("pwd_require_upper") == "on"
        settings["pwd_require_lower"] = request.form.get("pwd_require_lower") == "on"
        settings["pwd_require_digit"] = request.form.get("pwd_require_digit") == "on"
        settings["pwd_require_special"] = request.form.get("pwd_require_special") == "on"
        settings["temp_pass_hours"] = int(request.form.get("temp_pass_hours", 24))
        settings["totp_tolerance"] = int(request.form.get("totp_tolerance", 1))
        settings["session_timeout"] = int(request.form.get("session_timeout", 0))
        
        if save_auth_settings(settings):
            flash("Paramètres de sécurité SSO sauvegardés avec succès.", "success")
        else:
            flash("Erreur lors de la sauvegarde des paramètres.", "danger")
    except Exception as e:
        flash(f"Erreur de sauvegarde: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/action/auth/disable', methods=['POST'])
def auth_disable_user():
    if not session.get('logged_in'): return redirect(url_for('index'))
    email = request.form.get('email', '').strip()
    try:
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.execute("DELETE FROM users WHERE email=?", (email,))
            conn.execute("DELETE FROM sessions WHERE email=?", (email,))
            conn.commit()
        if email == SYSTEM_ADMIN_EMAIL:
            flash(f"Accès externe (SSO) désactivé pour {email}. Les données internes sont conservées. Rappel : Vous devez utiliser l'accès local de secours (Port 3001) pour réactiver ce compte.", "warning")
        else:
            flash(f"Accès externe (SSO) désactivé pour {email}. Les données internes sont conservées.", "warning")
    except Exception as e:
        flash(f"Erreur de désactivation: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/api/auth/sessions', methods=['GET'])
def api_auth_sessions():
    if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
    try:
        settings = get_auth_settings()
        timeout_h = int(settings.get("session_timeout", 2160))
        sessions_dict = {}
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT * FROM sessions"):
                s = dict(row)
                sid = s['session_id']
                if sid in sessions_dict:
                    existing_ips = sessions_dict[sid].get('ip_address') or ''
                    new_ip = s.get('ip_address') or ''
                    if new_ip and new_ip not in [ip.strip() for ip in existing_ips.split(',')]:
                        if existing_ips:
                            sessions_dict[sid]['ip_address'] = existing_ips + ", " + new_ip
                        else:
                            sessions_dict[sid]['ip_address'] = new_ip
                else:
                    s['created_dt'] = datetime.datetime.fromtimestamp(s['created_at']).strftime('%d/%m/%Y %H:%M')
                    s['expires_at'] = s['created_at'] + (timeout_h * 3600) if timeout_h > 0 else None
                    s['expires_dt'] = datetime.datetime.fromtimestamp(s['expires_at']).strftime('%d/%m/%Y %H:%M') if s['expires_at'] else "Infini"
                    s['time_remaining_sec'] = s['expires_at'] - int(time.time()) if s['expires_at'] else None
                    sessions_dict[sid] = s
        return jsonify({"sessions": list(sessions_dict.values())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/action/auth/session/revoke', methods=['POST'])
def auth_revoke_session():
    if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        if not data or 'session_id' not in data:
            return jsonify({"error": "Missing session_id"}), 400
        session_id = data['session_id']
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/action/auth/session/revoke_all', methods=['POST'])
def auth_revoke_all_sessions():
    if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
    try:
        with sqlite3.connect("/app/auth-data/auth.db") as conn:
            conn.execute("DELETE FROM sessions")
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/action/auth/delete', methods=['POST'])
def auth_delete_user():
    if not session.get('logged_in'): return redirect(url_for('index'))
    email = request.form.get('email', '').strip()
    
    if email == SYSTEM_ADMIN_EMAIL or email.endswith('@echo.local'):
        flash("Action bloquée : La suppression d'un compte système est strictement interdite.", "danger")
        return redirect(url_for('index'))

    if not os.path.exists(WEBUI_DB_PATH):
        flash("Action annulée : Base SSoT Open WebUI (webui.db) injoignable.", "danger")
        return redirect(url_for('index'))

    try:
        uid = None
        # 1. Transaction SSoT prioritaire sur webui.db (Fail-Closed : si webui.db est verrouillée/injoignable, aucune action)
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True, timeout=10.0) as conn_owui:
            u_row = conn_owui.execute("SELECT id, role FROM user WHERE email=?", (email,)).fetchone()
            if u_row:
                uid, u_role = str(u_row[0]), u_row[1]
                if u_role == 'admin' and not email.endswith('@echo.local'):
                    admins = conn_owui.execute("SELECT email FROM user WHERE role = 'admin'").fetchall()
                    human_admins = [a[0] for a in admins if not a[0].endswith('@echo.local')]
                    if len(human_admins) <= 1 and email in human_admins:
                        flash('Action bloquée : Suppression impossible du dernier administrateur humain.', 'danger')
                        return redirect(url_for('index'))

                tables = [row[0] for row in conn_owui.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for table in tables:
                    if table == "user":
                        continue
                    try:
                        columns = [col[1] for col in conn_owui.execute(f"PRAGMA table_info({table})").fetchall()]
                        if "user_id" in columns:
                            conn_owui.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                    except Exception as e:
                        print(f"Erreur purge table {table}: {e}")

                conn_owui.execute("DELETE FROM user WHERE id=?", (uid,))
                conn_owui.commit()

        # 2. Suppression dans la base SSO optionnelle (/app/auth-data/auth.db)
        if os.path.exists("/app/auth-data/auth.db"):
            with sqlite3.connect("/app/auth-data/auth.db", timeout=10.0) as conn_auth:
                conn_auth.execute("DELETE FROM users WHERE email=?", (email,))
                conn_auth.execute("DELETE FROM sessions WHERE email=?", (email,))
                conn_auth.commit()

        # 3. Cascade multi-volumes synchrone si l'utilisateur existait dans la SSoT
        if uid:
            purge_user_artifacts(uid)
            flash(f"Utilisateur {email} supprimé de la SSoT Open WebUI, de l'Auth et de tous les volumes ECHO.", "success")
        else:
            flash(f"Utilisateur {email} supprimé de l'Auth (non trouvé dans la SSoT Open WebUI).", "success")
    except Exception as e:
        flash(f"Erreur: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/action/user_role/<user_id>', methods=['POST'])
def auth_user_role(user_id):
    if not session.get('logged_in'): return redirect(url_for('index'))
    new_role = request.form.get('new_role')
    if new_role not in ('admin', 'user'): return redirect(url_for('index'))
    try:
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True) as conn:
            user = conn.execute("SELECT name, email, role FROM user WHERE id = ?", (user_id,)).fetchone()
            if not user:
                flash('Utilisateur introuvable.', 'danger')
                return redirect(url_for('index'))
            
            if user[1].endswith('@echo.local'):
                flash("Impossible de modifier le rôle d'un compte système.", 'danger')
                return redirect(url_for('index'))
                
            if new_role == 'user' and user[2] == 'admin':
                admins = conn.execute("SELECT email FROM user WHERE role = 'admin'").fetchall()
                human_admins = [a[0] for a in admins if not a[0].endswith('@echo.local')]
                if len(human_admins) <= 1 and user[1] in human_admins:
                    flash('Action bloquée : Rétrogradation impossible du dernier administrateur humain.', 'danger')
                    return redirect(url_for('index'))
                    
            conn.execute("UPDATE user SET role = ? WHERE id = ?", (new_role, user_id))
            conn.commit()
            flash(f"Le rôle de {user[0]} est désormais : {new_role.upper()}.", 'success')
    except Exception as e:
        flash(f"Erreur changement de rôle: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/api/bunkerweb/status')
def bunkerweb_status():
    if not DOCKER_AVAILABLE or not session.get('logged_in'): return jsonify({"active": False}), 403
    try:
        container = docker.from_env().containers.get('bunkerweb')
        return jsonify({"active": container.status == 'running'})
    except Exception:
        return jsonify({"active": False})

@app.route('/api/bunkerweb/action', methods=['POST'])
def bunkerweb_action():
    if not DOCKER_AVAILABLE or not session.get('logged_in'): return jsonify({"error": "Non autorisé"}), 403
    action = request.form.get('action')
    ip = request.form.get('ip', '').strip()
    if not re.match(r"^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$", ip) and not re.match(r"^[0-9a-fA-F:]+$", ip):
        return jsonify({"error": "IP invalide"}), 400
    if action not in ('ban', 'unban'): return jsonify({"error": "Action invalide"}), 400
    try:
        res = docker.from_env().containers.get('bunkerweb').exec_run(['bwcli', action, ip])
        return jsonify({"success": True, "output": res.output.decode('utf-8', errors='ignore')})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/bunkerweb/logs')
def bunkerweb_logs():
    if not DOCKER_AVAILABLE or not session.get('logged_in'): return jsonify([]), 403
    try:
        raw_logs = docker.from_env().containers.get('bunkerweb').logs(tail=5000).decode('utf-8', errors='ignore')
        filtered = [l.strip() for l in raw_logs.splitlines() if 'ModSecurity' in l or '[error]' in l or 'Bad Behavior' in l or ' 403 ' in l]
        return jsonify(filtered[-200:][::-1])
    except Exception as e: return jsonify([f"Erreur logs: {str(e)}"])

@app.route('/api/bunkerweb/list_bans')
def bunkerweb_list_bans():
    if not DOCKER_AVAILABLE or not session.get('logged_in'): return jsonify({"error": "Non autorisé"}), 403
    try:
        res = docker.from_env().containers.get('bunkerweb').exec_run(['bwcli', 'bans'])
        output = res.output.decode('utf-8', errors='ignore').strip()
        output = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', output)
        parsed_bans = []
        current_ban = None
        current_type = "Global"
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith('·') or line.startswith('─') or line.startswith('[202') or 'BunkerWeb Ban List' in line or '▓' in line: continue
            if line.startswith('🌐'): current_type = line.replace('🌐', '').strip(); continue
            if line.startswith('🖧'): current_type = line.replace('🖧', '').strip(); continue
            if line.startswith('🔒'):
                if current_ban: parsed_bans.append(current_ban)
                parts = line.replace('🔒', '').strip().split(' - ', 1)
                ip_country = parts[0].split(' [')
                ip = ip_country[0].strip()
                country = ip_country[1].replace(']', '').strip() if len(ip_country) > 1 else ""
                svc = parts[1].strip() if len(parts) > 1 else current_type
                current_ban = {"ip": ip, "country": country, "type": svc, "details": ""}
            elif current_ban and line.startswith('⏱'): current_ban["details"] += line + "<br>"
            elif current_ban and line.startswith('ℹ️'): current_ban["details"] += line
        if current_ban: parsed_bans.append(current_ban)
        
        if parsed_bans:
            def get_date(b):
                m = re.search(r"Banned on (\d{4}-\d{2}-\d{2} at \d{2}:\d{2}:\d{2})", b["details"])
                if m:
                    try: return datetime.datetime.strptime(m.group(1), "%Y-%m-%d at %H:%M:%S")
                    except Exception: pass
                return datetime.datetime.min
            parsed_bans.sort(key=get_date, reverse=True)
            
            try:
                import urllib.request
                ips_to_check = [b["ip"] for b in parsed_bans]
                as_map = {}
                
                # On découpe par lots de 100 (limite de ip-api)
                # On limite à 500 IPs (5 requêtes) pour ne pas bloquer l'UI ni le quota (15 req/min)
                for i in range(0, min(len(ips_to_check), 500), 100):
                    batch = ips_to_check[i:i+100]
                    req = urllib.request.Request("http://ip-api.com/batch", data=json.dumps(batch).encode('utf-8'), headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        as_data = json.loads(response.read().decode('utf-8'))
                        for d in as_data:
                            if d.get("status") == "success":
                                as_map[d.get("query")] = d
                                
                for b in parsed_bans:
                    info = as_map.get(b["ip"])
                    if info:
                        asn = info.get('as', '').split(' ')[0] if info.get('as') else 'N/A'
                        b["as_info"] = f"{asn} - {info.get('isp', 'N/A')}"
            except Exception: pass

        return jsonify({"success": True, "bans": parsed_bans})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
