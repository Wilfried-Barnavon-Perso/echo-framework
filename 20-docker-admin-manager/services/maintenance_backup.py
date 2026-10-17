# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - MAINTENANCE & BACKUP SCHEDULER
VERSION : 5.137
================================================================================
"""
import datetime
import glob
import html
import json
import os
import subprocess
import time
from core.config import (
    BACKUP_DIR,
    DOCKER_AVAILABLE,
    HAS_PSUTIL,
    HAS_SCHEDULER,
    MAINTENANCE_STATE,
    MCP_CACHE_DIR,
    N8N_DATA_DIR,
    RCLONE_CONF_PATH,
    TARGET_CONTAINER,
    backup_scheduler,
    docker,
    paramiko,
    psutil,
)
from core.utils import human_size, _format_bytes, _get_tree_size
from core.settings_io import (
    load_settings,
    load_maint_config,
    save_maint_report,
)
from services.semantic_pruning import run_semantic_pruning

__all__ = [
    'perform_backup_task',
    'run_nightly_maintenance_cycle',
    'update_system_schedules',
    'get_backup_list',
]

def _sftp_mkdir_p(sftp, remote_directory: str):
    """Crée récursivement l'arborescence distante en SFTP (équivalent mkdir -p)."""
    if not remote_directory or remote_directory == "/":
        return
    dirs = []
    curr = remote_directory.rstrip("/")
    while curr and curr != "/":
        try:
            sftp.stat(curr)
            break
        except FileNotFoundError:
            dirs.append(curr)
            curr = os.path.dirname(curr)
    for d in reversed(dirs):
        sftp.mkdir(d)

def _get_proc_file_offset(pid: int, target_path: str) -> int:
    """Lit la position courante (offset en octets) d'un descripteur ouvert par `pid` sur `target_path` via /proc."""
    max_pos = 0
    target_name = os.path.basename(target_path)
    for fd_path in glob.glob(f"/proc/{pid}/fd/*"):
        try:
            link_target = os.readlink(fd_path)
            if target_name in link_target:
                fd_num = os.path.basename(fd_path)
                with open(f"/proc/{pid}/fdinfo/{fd_num}", "r", encoding="utf-8") as f_info:
                    for line in f_info:
                        if line.startswith("pos:"):
                            pos = int(line.split()[1])
                            if pos > max_pos:
                                max_pos = pos
        except Exception:
            pass
    return max_pos

def _build_ext_status_html(ext_mode: str, sets: dict, ext_error: str | None) -> tuple[str, str]:
    """Construit le badge HTML et le détail d'erreur éventuel pour l'externalisation."""
    if ext_mode == "local":
        return "<span class='badge bg-secondary'>Désactivée (Local)</span>", ""
    if ext_error:
        target = sets.get("rclone_remote", "") if ext_mode == "rclone" else sets.get("sftp_host", "")
        target_html = f" <small class='text-muted'>({html.escape(target)})</small>" if target else ""
        return (
            f"<span class='badge bg-warning text-dark'>Échec Distant</span>{target_html}",
            f"<div class='text-warning x-small mt-1'>⚠️ {html.escape(ext_error)}</div>",
        )
    if ext_mode == "rclone":
        remote_lbl = html.escape(sets.get("rclone_remote", ""))
        return f"<span class='badge bg-success'>Cloud Rclone OK</span> <small class='text-muted'>({remote_lbl})</small>", ""
    sftp_lbl = html.escape(f"{sets.get('sftp_host', '')}:{sets.get('sftp_path', '/backups/echo')}")
    return f"<span class='badge bg-success'>NAS SFTP OK</span> <small class='text-muted'>({sftp_lbl})</small>", ""

def perform_backup_task(record_report: bool = False):
    if not DOCKER_AVAILABLE:
        return None
    if record_report and MAINTENANCE_STATE.get("is_running"):
        return "Opération déjà en cours"

    t_start = time.time()
    MAINTENANCE_STATE["is_running"] = True
    MAINTENANCE_STATE["task_type"] = "backup"
    MAINTENANCE_STATE["start_time"] = t_start
    MAINTENANCE_STATE["percent"] = 2
    MAINTENANCE_STATE["status"] = "Sauvegarde (1/3) : Estimation du volume de données..."

    archive_name = f"echo_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.xz"
    fpath = os.path.join(BACKUP_DIR, archive_name)
    ext_error = None
    try:
        sets = load_settings()
        ext_mode = sets.get("ext_mode", "local")
        has_ext = ext_mode in ("sftp", "rclone")
        step_total = "3" if has_ext else "2"

        tar_sources = [
            d for d in ('/app/backend/data', '/qdrant/storage', '/app/auth-data', N8N_DATA_DIR, MCP_CACHE_DIR)
            if os.path.exists(d)
        ]
        total_src_bytes = max(1, sum(_get_tree_size(d) for d in tar_sources))

        MAINTENANCE_STATE["percent"] = 5
        MAINTENANCE_STATE["status"] = f"Sauvegarde (1/{step_total}) : Arrêt temporaire des services (OWUI, Qdrant, N8N, MCP)..."

        client = docker.from_env()
        target = client.containers.get(TARGET_CONTAINER)
        aux_containers = []
        for cname in ('echo-qdrant', 'echo-n8n-worker', 'echo-mcp-broker'):
            try:
                aux_containers.append(client.containers.get(cname))
            except Exception:
                pass
        target.stop()
        for c_aux in aux_containers:
            try:
                c_aux.stop()
            except Exception:
                pass

        # Phase 2 : Compression locale tar -cJf avec suivi temps réel
        proc_tar = subprocess.Popen(
            ['tar', '-cJf', fpath, *tar_sources],
            stderr=subprocess.PIPE,
            text=True
        )
        while proc_tar.poll() is None:
            try:
                read_b = 0
                if HAS_PSUTIL and psutil is not None:
                    read_b = psutil.Process(proc_tar.pid).io_counters().read_bytes
                tar_pct = min(99.0, (read_b * 100.0) / total_src_bytes) if read_b > 0 else 0.0
                cur_arch = os.path.getsize(fpath) if os.path.exists(fpath) else 0
                span = 50.0 if has_ext else 90.0
                MAINTENANCE_STATE["percent"] = round(5.0 + (tar_pct / 100.0) * span, 1)
                MAINTENANCE_STATE["status"] = (
                    f"Sauvegarde (2/{step_total}) : Compression tar.xz... {tar_pct:.0f}% "
                    f"(Archive : {human_size(cur_arch)})"
                )
            except Exception:
                pass
            time.sleep(1)

        if proc_tar.returncode != 0:
            err_tar = (proc_tar.stderr.read() if proc_tar.stderr else "").strip()[:200]
            raise RuntimeError(f"Échec compression tar (code {proc_tar.returncode}): {err_tar}")

        MAINTENANCE_STATE["status"] = f"Sauvegarde (2/{step_total}) : Redémarrage des services..."
        target.start()
        for c_aux in aux_containers:
            try:
                c_aux.start()
            except Exception:
                pass

        sets = load_settings()
        ext_mode = sets.get("ext_mode", "local")
        if sets.get("auto_cleanup", True):
            files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'echo_backup_*.tar.*')), key=os.path.getmtime, reverse=True)
            mode, val = sets.get("cleanup_mode", "count"), int(sets.get("cleanup_value", 5))
            if mode == 'count' and val > 0 and len(files) > val:
                for f in files[val:]:
                    os.remove(f)
            elif mode == 'days' and val > 0:
                for f in files:
                    if os.path.getmtime(f) < (time.time() - (val * 86400)):
                        os.remove(f)

        arch_size = max(1, os.path.getsize(fpath) if os.path.exists(fpath) else 0)
        local_kept = len(glob.glob(os.path.join(BACKUP_DIR, 'echo_backup_*.tar.*')))

        # Phase 3 : Externalisation
        if ext_mode == "sftp":
            t = None
            sftp = None
            try:
                import stat
                MAINTENANCE_STATE["percent"] = 56
                MAINTENANCE_STATE["status"] = f"Sauvegarde (3/3) : Connexion NAS SFTP ({sets.get('sftp_host')})..."
                t = paramiko.Transport((sets.get("sftp_host"), int(sets.get("sftp_port", 22))))
                t.connect(username=sets.get("sftp_user"), password=sets.get("sftp_pass"))
                sftp = paramiko.SFTPClient.from_transport(t)
                r_dir = (sets.get("sftp_path") or "/backups/echo").rstrip("/") or "/"
                _sftp_mkdir_p(sftp, r_dir)

                def _sftp_cb(sent, total):
                    tot = max(1, total)
                    up_pct = min(100.0, (sent * 100.0) / tot)
                    MAINTENANCE_STATE["percent"] = round(56.0 + (up_pct / 100.0) * 40.0, 1)
                    MAINTENANCE_STATE["status"] = (
                        f"Sauvegarde (3/3) : Transfert NAS SFTP... {up_pct:.1f}% "
                        f"({sent / (1024**2):.1f} MB / {tot / (1024**2):.1f} MB)"
                    )

                sftp.put(fpath, f"{r_dir.rstrip('/')}/{os.path.basename(fpath)}", callback=_sftp_cb)

                # Cleanup distant SFTP
                MAINTENANCE_STATE["percent"] = 97
                MAINTENANCE_STATE["status"] = "Sauvegarde (3/3) : Élagage distant SFTP..."
                cmode, cval = sets.get("ext_cleanup_mode", "count"), int(sets.get("ext_cleanup_value", 5))
                f_stat = [f for f in sftp.listdir_attr(r_dir) if stat.S_ISREG(f.st_mode) and f.filename.startswith("echo_backup_")]
                f_stat.sort(key=lambda x: x.st_mtime, reverse=True)
                if cmode == 'count' and cval > 0 and len(f_stat) > cval:
                    for f in f_stat[cval:]:
                        sftp.remove(f"{r_dir.rstrip('/')}/{f.filename}")
                elif cmode == 'days' and cval > 0:
                    for f in f_stat:
                        if f.st_mtime < (time.time() - (cval * 86400)):
                            sftp.remove(f"{r_dir.rstrip('/')}/{f.filename}")
            except Exception as e:
                ext_error = f"SFTP: {e}"
                print(f"SFTP Backup Error: {e}")
            finally:
                if sftp:
                    try:
                        sftp.close()
                    except Exception:
                        pass
                if t:
                    try:
                        t.close()
                    except Exception:
                        pass

        elif ext_mode == "rclone":
            try:
                remote = sets.get("rclone_remote", "")
                conf_path = RCLONE_CONF_PATH
                if not (conf_path and os.path.exists(conf_path) and remote):
                    raise RuntimeError("Profil Rclone non configuré ou fichier rclone.conf introuvable.")

                MAINTENANCE_STATE["percent"] = 56
                MAINTENANCE_STATE["status"] = f"Sauvegarde (3/3) : Initialisation transfert Cloud Rclone ({remote})..."
                proc_rc = subprocess.Popen(
                    ['rclone', '--config', conf_path, 'copy', fpath, remote],
                    stderr=subprocess.PIPE,
                    text=True
                )
                while proc_rc.poll() is None:
                    try:
                        pos = _get_proc_file_offset(proc_rc.pid, fpath)
                        up_pct = min(99.9, (pos * 100.0) / arch_size)
                        MAINTENANCE_STATE["percent"] = round(56.0 + (up_pct / 100.0) * 40.0, 1)
                        MAINTENANCE_STATE["status"] = (
                            f"Sauvegarde (3/3) : Transfert Cloud Rclone ({remote})... {up_pct:.1f}% "
                            f"({pos / (1024**2):.1f} MB / {arch_size / (1024**2):.1f} MB)"
                        )
                    except Exception:
                        pass
                    time.sleep(1)

                if proc_rc.returncode != 0:
                    err_rc = (proc_rc.stderr.read() if proc_rc.stderr else "").strip()
                    raise subprocess.CalledProcessError(proc_rc.returncode, 'rclone', stderr=err_rc)

                MAINTENANCE_STATE["percent"] = 97
                MAINTENANCE_STATE["status"] = "Sauvegarde (3/3) : Élagage distant Rclone..."
                cmode, cval = sets.get("ext_cleanup_mode", "count"), int(sets.get("ext_cleanup_value", 5))
                if cmode == 'days' and cval > 0:
                    subprocess.run(['rclone', '--config', conf_path, 'delete', remote, '--min-age', f"{cval}d"], check=False)
                elif cmode == 'count' and cval > 0:
                    res = subprocess.run(['rclone', '--config', conf_path, 'lsjson', remote], capture_output=True, text=True)
                    if res.returncode == 0:
                        try:
                            f_list = [f for f in json.loads(res.stdout) if not f.get('IsDir') and f.get('Name', '').startswith("echo_backup_")]
                            f_list.sort(key=lambda x: x.get('ModTime'), reverse=True)
                            if len(f_list) > cval:
                                for f in f_list[cval:]:
                                    rel_path = f['Path'].lstrip('/')
                                    target_file = f"{remote}{rel_path}" if remote.endswith(':') else f"{remote.rstrip('/')}/{rel_path}"
                                    subprocess.run(['rclone', '--config', conf_path, 'deletefile', target_file], check=False)
                        except Exception:
                            pass
            except subprocess.CalledProcessError as cpe:
                err_detail = (cpe.stderr or str(cpe)).strip()[:200]
                ext_error = f"Rclone: {err_detail}"
                print(f"Rclone Backup Error: {err_detail}")
            except Exception as e:
                ext_error = f"Rclone: {e}"
                print(f"Rclone Backup Error: {e}")

        MAINTENANCE_STATE["percent"] = 100
        MAINTENANCE_STATE["status"] = "Sauvegarde terminée."

        dur_sec = max(1, int(time.time() - t_start))
        dur_str = f"{dur_sec // 60}m {dur_sec % 60:02d}s" if dur_sec >= 60 else f"{dur_sec}s"
        ext_badge, ext_detail = _build_ext_status_html(ext_mode, sets, ext_error)

        backup_info = {
            "archive_name": archive_name,
            "arch_size": arch_size,
            "arch_size_fmt": human_size(arch_size),
            "duration_str": dur_str,
            "local_kept": local_kept,
            "ext_mode": ext_mode,
            "ext_badge_html": ext_badge,
            "ext_detail_html": ext_detail,
            "ext_error": ext_error,
        }

        if record_report:
            report_html = f"""
            <div style='line-height: 1.45;'>
                <b><i class='bi bi-hdd-network text-success'></i> Sauvegarde Manuelle Complète</b><br>
                📦 <b>Archive</b> : <code>{html.escape(archive_name)}</code> <i>({human_size(arch_size)})</i><br>
                ⏱️ <b>Durée</b> : {dur_str} &nbsp;|&nbsp; 🗂️ <b>Rétention locale</b> : {local_kept} archive(s)<br>
                ☁️ <b>Externalisation</b> : {ext_badge}{ext_detail}
            </div>
            """
            save_maint_report(report_html)

        return backup_info
    except Exception as e:
        for c_name in (TARGET_CONTAINER, 'echo-qdrant', 'echo-n8n-worker', 'echo-mcp-broker'):
            try:
                docker.from_env().containers.get(c_name).start()
            except Exception:
                pass
        if record_report:
            save_maint_report(f"""
            <div style='line-height: 1.45;'>
                <b class='text-danger'><i class='bi bi-exclamation-triangle-fill'></i> Sauvegarde Manuelle (ÉCHEC)</b><br>
                <span class='text-danger x-small'>{html.escape(str(e))}</span>
            </div>
            """)
        raise e
    finally:
        MAINTENANCE_STATE["is_running"] = False
        MAINTENANCE_STATE["task_type"] = ""
        MAINTENANCE_STATE["status"] = ""
        MAINTENANCE_STATE["percent"] = 0
        MAINTENANCE_STATE["start_time"] = None

def run_nightly_maintenance_cycle():
    """Séquenceur Maître : Sauvegarde -> Redémarrage -> Purge -> Consolidation -> Vacuum."""
    bck_cfg = load_settings()
    bck_info = None
    bck_fatal_err = None

    # 1. Sauvegarde (Coupe potentiellement Qdrant)
    if bck_cfg.get("auto_backup"):
        try:
            res = perform_backup_task(record_report=False)
            if isinstance(res, dict):
                bck_info = res
        except Exception as e:
            bck_fatal_err = str(e)

    # 2. Assurance Haute Disponibilité (Redémarrage garanti des services critiques)
    if DOCKER_AVAILABLE:
        try:
            client = docker.from_env()
            for c_name in (TARGET_CONTAINER, 'echo-qdrant', 'echo-n8n-worker', 'echo-mcp-broker'):
                try:
                    c_obj = client.containers.get(c_name)
                    if c_obj.status != 'running':
                        c_obj.start()
                except Exception:
                    pass
            time.sleep(5)  # Temps de chauffe API HTTP
        except Exception:
            pass

    # 3. Purge, Consolidation et Vacuum
    if not bck_cfg.get("auto_backup"):
        backup_ok_str = "Désactivée"
        badge_cls = "secondary"
        backup_details_html = ""
    elif bck_fatal_err:
        backup_ok_str = "Erreur"
        badge_cls = "danger"
        backup_details_html = (
            f"<div class='text-danger x-small mb-2'>❌ {html.escape(bck_fatal_err)}</div>"
        )
    elif bck_info:
        ext_err = bck_info.get("ext_error")
        backup_ok_str = "Locale OK / Ext. Échec" if ext_err else "Terminée"
        badge_cls = "warning text-dark" if ext_err else "success"
        backup_details_html = f"""
            <div class='mb-2' style='line-height: 1.45;'>
                📦 <b>Archive</b> : <code>{html.escape(bck_info.get('archive_name', ''))}</code> <i>({html.escape(bck_info.get('arch_size_fmt', ''))})</i><br>
                ⏱️ <b>Durée</b> : {html.escape(bck_info.get('duration_str', ''))} &nbsp;|&nbsp; 🗂️ <b>Rétention locale</b> : {bck_info.get('local_kept', 0)} archive(s)<br>
                ☁️ <b>Externalisation</b> : {bck_info.get('ext_badge_html', '')}{bck_info.get('ext_detail_html', '')}
            </div>
        """
    else:
        backup_ok_str = "Ignorée"
        badge_cls = "secondary"
        backup_details_html = ""

    try:
        # On exécute la purge en mode silencieux et on récupère les datas brutes
        report_html, stats = run_semantic_pruning(silent=True)
        drop_total_b = (
            stats.get('drop_users', {}).get('b', 0)
            + stats.get('drop_chats', {}).get('b', 0)
            + stats.get('drop_n8n', {}).get('b', 0)
        )

        pretty_report = f"""
        <div style='line-height: 1.5; font-size: 0.85rem;' class='text-light'>
            <div class='mb-2 pb-1 border-bottom border-secondary'>
                <b class='text-success'><i class='bi bi-check-circle-fill'></i> CYCLE MAINTENANCE GLOBAL</b>
            </div>
            <div class='row gx-2 mb-1'>
                <div class='col-6'>
                    <span class='text-muted'>Sauvegarde :</span> 
                    <span class='badge bg-{badge_cls}'>{backup_ok_str}</span>
                </div>
                <div class='col-6'>
                    <span class='text-muted'>Haute Dispo. :</span> 
                    <span class='badge bg-info'>Vérifiée</span>
                </div>
            </div>
            {backup_details_html}
            <div class='mt-2 pt-1 border-top border-secondary'>
                <b class='text-info'><i class='bi bi-trash'></i> Bilan Élagage & SQLite</b><br>
                <i class='bi bi-people'></i> Utilisateurs : <b>{stats.get('users', {}).get('c', 0)}</b> <i>({_format_bytes(stats.get('users', {}).get('b', 0))})</i><br>
                <i class='bi bi-chat-left-dots'></i> Chats : <b>{stats.get('chats', {}).get('c', 0)}</b> <i>({_format_bytes(stats.get('chats', {}).get('b', 0))})</i><br>
                <i class='bi bi-file-earmark-code'></i> Fichiers FS : <b>{stats.get('files', {}).get('c', 0)}</b> <i>({_format_bytes(stats.get('files', {}).get('b', 0))})</i><br>
                <i class='bi bi-key'></i> MCP Cache : <b>{stats.get('mcp_cache', {}).get('c', 0)}</b> <i>({_format_bytes(stats.get('mcp_cache', {}).get('b', 0))})</i><br>
                <i class='bi bi-hdd'></i> Drop Zones : <b>{stats.get('drop_users', {}).get('c', 0)}</b> Users, <b>{stats.get('drop_chats', {}).get('c', 0)}</b> Chats, <b>{stats.get('drop_n8n', {}).get('c', 0)}</b> N8N <i>(Libéré: {_format_bytes(drop_total_b)})</i><br>
                <i class='bi bi-diagram-3'></i> Consolidation : <b>{stats.get('promoted_clusters', 0)}</b> clusters promus (Lvl1→Lvl2)<br>
                <i class='bi bi-database-check'></i> SQLite : <b>{stats.get('sqlite_ghosts', 0)}</b> fantômes, <b>{stats.get('sqlite_vacuumed', 0)}</b> bases optimisées<br>
            </div>
        </div>
        """
    except Exception as e:
        pretty_report = f"""
        <div style='line-height: 1.5; font-size: 0.85rem;' class='text-light'>
            <div class='mb-2 pb-1 border-bottom border-secondary'>
                <b class='text-danger'><i class='bi bi-exclamation-triangle-fill'></i> CYCLE MAINTENANCE GLOBAL (ÉCHEC)</b>
            </div>
            <div class='text-danger'>{str(e)}</div>
        </div>
        """

    save_maint_report(pretty_report)

def update_system_schedules():
    """Remplace update_backup_schedule et setup_lifecycle_scheduler"""
    if not HAS_SCHEDULER or backup_scheduler is None: return
    try:
        backup_scheduler.remove_all_jobs()
        bck_cfg = load_settings()
        maint_cfg = load_maint_config()
        
        # Asservissement : si backup actif, on utilise son heure pour TOUT le cycle
        target_time = bck_cfg.get("backup_time", "03:00") if bck_cfg.get("auto_backup") else maint_cfg.get("cleanup_hour", "03:00")
        h, m = map(int, target_time.split(':'))
        
        now = datetime.datetime.now()
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now: start += datetime.timedelta(days=1)
        
        interval = int(bck_cfg.get("interval_days", 1)) if bck_cfg.get("auto_backup") else 1
        backup_scheduler.add_job(run_nightly_maintenance_cycle, 'interval', days=interval, start_date=start)
    except Exception: pass

def get_backup_list():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'echo_backup_*.tar.*')), key=os.path.getmtime, reverse=True)
    return [{'name': os.path.basename(f), 'size': human_size(os.path.getsize(f)), 'date': datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')} for f in files]
