# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - FLASK ROUTES
VERSION : 5.237.5.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.237.5.1: Maintien du job pendant le téléchargement (suppression du pop prématuré) et en-tête Content-Length explicite.
# 5.237.2.1: Intégration des routes d'export asynchrone (/api/me/export/start, status, cancel, download) avec barre de progression.
# 5.236.5.1: Routes self-service utilisateur (stats, sessions, renommage, mot de passe, export .tar.xz, suppression Option B et logout).

from contextlib import closing
import datetime
import glob
import os
import shutil
import sqlite3
import time
from flask import (  # pyright: ignore[reportMissingImports]
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    stream_with_context,
    url_for,
)
from core.config import (
    AUTH_DB_PATH,
    ECHO_DOMAIN,
    ECHO_USERS_ROOT,
    HAS_HTTPX,
    SYSTEM_ADMIN_EMAIL,
    WEBUI_DB_PATH,
    WEBUI_INTERNAL_URL,
    app,
    httpx,
    sanitize_echo_id,
)
from core.security import (
    hash_user_password,
    validate_password_policy,
    verify_user_password,
    verify_user_totp,
)
from core.utils import (
    _format_bytes,
    _get_tree_size,
    get_auth_settings,
    get_echo_version,
)
from services.export_service import (
    _EXPORT_JOBS,
    _EXPORT_JOBS_LOCK,
    build_user_export_archive,
    cancel_user_export_job,
    count_user_qdrant_points,
    get_user_export_lock,
    get_user_export_status,
    start_user_export_job,
    stream_and_cleanup_archive,
    stream_archive_file,
)
from templates.templates import HTML_USER_DASHBOARD


def _get_effective_domain() -> str:
    return (os.environ.get("ECHO_DOMAIN") or ECHO_DOMAIN or "localhost").strip()


def _list_user_sessions(email: str, current_sid: str) -> list[dict]:
    """Récupère et enrichit toutes les sessions SSO actives de l'utilisateur dans auth.db."""
    if not email or not os.path.exists(AUTH_DB_PATH):
        return []
    now = int(time.time())
    sessions_dict: dict[str, dict] = {}
    try:
        with closing(sqlite3.connect(f"file:{AUTH_DB_PATH}?mode=ro", uri=True, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM sessions WHERE email = ? ORDER BY created_at DESC",
                (email,),
            ).fetchall()
            for row in rows:
                s = dict(row)
                sid = str(s.get("session_id") or "")
                if not sid:
                    continue
                if sid in sessions_dict:
                    existing_ips = sessions_dict[sid].get("ip_address") or ""
                    new_ip = s.get("ip_address") or ""
                    if new_ip and new_ip not in [ip.strip() for ip in existing_ips.split(",")]:
                        sessions_dict[sid]["ip_address"] = (
                            f"{existing_ips}, {new_ip}" if existing_ips else new_ip
                        )
                else:
                    is_trusted = bool(s.get("is_trusted", 0))
                    exp_at = s.get("expires_at")
                    if exp_at is None and not is_trusted:
                        exp_at = int(s.get("created_at") or now) + 86400
                    created_ts = int(s.get("created_at") or now)
                    s["is_trusted"] = is_trusted
                    s["is_current"] = bool(current_sid and sid == current_sid)
                    s["expires_at"] = int(exp_at) if exp_at is not None else None
                    s["created_dt"] = datetime.datetime.fromtimestamp(created_ts).strftime(
                        "%d/%m/%Y %H:%M"
                    )
                    s["expires_dt"] = (
                        datetime.datetime.fromtimestamp(int(exp_at)).strftime("%d/%m/%Y %H:%M:%S")
                        if exp_at is not None
                        else "Éternelle"
                    )
                    s["time_remaining_sec"] = (
                        max(0, int(exp_at) - now) if exp_at is not None else None
                    )
                    sessions_dict[sid] = s
    except Exception:
        pass
    return list(sessions_dict.values())


def _compute_user_stats(user_id: str | None, email: str, current_sid: str) -> dict:
    """Calcule les métriques complètes du profil utilisateur (Disque, Chats, Qdrant, Session SSO)."""
    chat_count = 0
    if user_id and os.path.exists(WEBUI_DB_PATH):
        try:
            with closing(sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=5.0)) as conn:
                row = conn.execute(
                    "SELECT COUNT(id) FROM chat WHERE user_id = ?",
                    (str(user_id),),
                ).fetchone()
                if row and row[0] is not None:
                    chat_count = int(row[0])
        except Exception:
            pass

    profile_bytes = 0
    chats_bytes = 0
    files_bytes = 0
    skills_bytes = 0
    codex_bytes = 0
    session_db_count = 0

    if user_id:
        safe_uid = sanitize_echo_id(user_id)
        user_dir = os.path.join(ECHO_USERS_ROOT, safe_uid)
        if os.path.isdir(user_dir):
            profile_bytes = _get_tree_size(user_dir)
            chats_dir = os.path.join(user_dir, "chats")
            files_dir = os.path.join(user_dir, "files")
            skills_dir = os.path.join(user_dir, "skills")
            codex_dir = os.path.join(user_dir, "codex")
            chats_bytes = _get_tree_size(chats_dir) if os.path.isdir(chats_dir) else 0
            files_bytes = _get_tree_size(files_dir) if os.path.isdir(files_dir) else 0
            skills_bytes = _get_tree_size(skills_dir) if os.path.isdir(skills_dir) else 0
            codex_bytes = _get_tree_size(codex_dir) if os.path.isdir(codex_dir) else 0
            session_db_count = len(glob.glob(os.path.join(chats_dir, "*", "session.db")))

    qdrant_counts = count_user_qdrant_points(user_id)
    sessions_list = _list_user_sessions(email, current_sid)
    current_session = None
    for s in sessions_list:
        if s.get("is_current"):
            current_session = s
            break
    if current_session is None and sessions_list:
        current_session = sessions_list[0]

    return {
        "profile_size_bytes": profile_bytes,
        "profile_size_fmt": _format_bytes(profile_bytes),
        "chats_size_fmt": _format_bytes(chats_bytes),
        "files_size_fmt": _format_bytes(files_bytes),
        "skills_size_fmt": _format_bytes(skills_bytes),
        "codex_size_fmt": _format_bytes(codex_bytes),
        "chat_count": chat_count,
        "session_db_count": session_db_count,
        "qdrant_meta_count": qdrant_counts["meta_artifacts"],
        "qdrant_rag_count": qdrant_counts["session_rag"],
        "qdrant_total_count": qdrant_counts["total"],
        "active_sessions_count": len(sessions_list),
        "current_session": current_session,
    }


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/")
def index():
    stats = _compute_user_stats(g.user_id, g.user_email, g.current_session_id)
    return render_template_string(
        HTML_USER_DASHBOARD,
        version=get_echo_version(),
        domain=_get_effective_domain(),
        user_email=g.user_email,
        user_id=g.user_id or "Non initialisé",
        display_name=g.display_name,
        user_role=g.user_role,
        has_totp=g.has_totp,
        stats=stats,
        auth_settings=get_auth_settings(),
        server_time_iso=datetime.datetime.now().isoformat(),
    )


@app.route("/api/me/stats")
def api_me_stats():
    return jsonify(_compute_user_stats(g.user_id, g.user_email, g.current_session_id))


@app.route("/api/me/sessions")
def api_me_sessions():
    sessions_list = _list_user_sessions(g.user_email, g.current_session_id)
    return jsonify(
        {
            "sessions": sessions_list,
            "current_session_id": g.current_session_id,
        }
    )


@app.route("/action/session/revoke", methods=["POST"])
def action_revoke_session():
    data = request.get_json(silent=True) or {}
    target_sid = (data.get("session_id") or request.form.get("session_id") or "").strip()
    if not target_sid:
        return jsonify({"error": "Identifiant de session manquant."}), 400

    try:
        with closing(sqlite3.connect(AUTH_DB_PATH, timeout=10.0)) as conn:
            cur = conn.execute(
                "DELETE FROM sessions WHERE session_id = ? AND email = ?",
                (target_sid, g.user_email),
            )
            conn.commit()
            deleted = int(cur.rowcount or 0)
        return jsonify(
            {
                "status": "success",
                "deleted": deleted,
                "revoked_current": bool(g.current_session_id and target_sid == g.current_session_id),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/action/session/revoke_others", methods=["POST"])
def action_revoke_other_sessions():
    try:
        with closing(sqlite3.connect(AUTH_DB_PATH, timeout=10.0)) as conn:
            if g.current_session_id:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE email = ? AND session_id != ?",
                    (g.user_email, g.current_session_id),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE email = ?",
                    (g.user_email,),
                )
            conn.commit()
            revoked_count = int(cur.rowcount or 0)
        return jsonify({"status": "success", "revoked_count": revoked_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/action/profile/rename", methods=["POST"])
def action_profile_rename():
    new_name = (request.form.get("name") or "").strip()
    if not new_name or len(new_name) > 120:
        flash("Le nom d'affichage doit contenir entre 1 et 120 caractères.", "danger")
        return redirect(url_for("index"))

    try:
        with closing(sqlite3.connect(AUTH_DB_PATH, timeout=10.0)) as conn:
            conn.execute(
                "UPDATE users SET name = ? WHERE email = ?",
                (new_name, g.user_email),
            )
            conn.commit()

        if os.path.exists(WEBUI_DB_PATH):
            try:
                with closing(sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True, timeout=10.0)) as w_conn:
                    w_conn.execute(
                        "UPDATE user SET name = ? WHERE email = ?",
                        (new_name, g.user_email),
                    )
                    w_conn.commit()
            except Exception:
                pass

        flash(f"Nom d'affichage mis à jour en « {new_name} » (SSO & Open WebUI synchronisés).", "success")
    except Exception as e:
        flash(f"Erreur lors de la mise à jour du nom : {e}", "danger")
    return redirect(url_for("index"))


@app.route("/action/profile/password", methods=["POST"])
def action_profile_password():
    current_pwd = request.form.get("current_password") or ""
    new_pwd = request.form.get("new_password") or ""
    confirm_pwd = request.form.get("confirm_password") or ""
    totp_code = request.form.get("totp_code") or ""
    revoke_others = request.form.get("revoke_others") == "on"

    if new_pwd != confirm_pwd:
        flash("La confirmation du nouveau mot de passe ne correspond pas.", "danger")
        return redirect(url_for("index"))

    if not verify_user_password(g.user_email, current_pwd):
        flash("Votre mot de passe actuel est incorrect.", "danger")
        return redirect(url_for("index"))

    if g.has_totp and not verify_user_totp(g.user_email, totp_code):
        flash("Code MFA TOTP à 6 chiffres invalide ou expiré.", "danger")
        return redirect(url_for("index"))

    ok_policy, policy_msg = validate_password_policy(new_pwd, get_auth_settings())
    if not ok_policy:
        flash(policy_msg, "danger")
        return redirect(url_for("index"))

    try:
        new_hash = hash_user_password(new_pwd)
        with closing(sqlite3.connect(AUTH_DB_PATH, timeout=10.0)) as conn:
            conn.execute(
                "UPDATE users SET pass_hash = ? WHERE email = ?",
                (new_hash, g.user_email),
            )
            if revoke_others and g.current_session_id:
                conn.execute(
                    "DELETE FROM sessions WHERE email = ? AND session_id != ?",
                    (g.user_email, g.current_session_id),
                )
            conn.commit()
        flash("Mot de passe modifié avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de la mise à jour du mot de passe : {e}", "danger")
    return redirect(url_for("index"))


@app.route("/api/me/export/start", methods=["POST"])
def api_me_export_start():
    """Déclenche la préparation asynchrone de l'archive .tar.xz de l'utilisateur."""
    st = start_user_export_job(g.user_id, g.user_email, g.display_name)
    return jsonify(st)


@app.route("/api/me/export/status", methods=["GET"])
def api_me_export_status():
    """Consulte l'avancement temps réel (pourcentage et étape) de l'exportation."""
    st = get_user_export_status(g.user_id or g.user_email)
    return jsonify(st)


@app.route("/action/account/export/cancel", methods=["POST"])
def action_account_export_cancel():
    """Annule l'export en cours ou supprime l'archive prête du serveur."""
    cancel_user_export_job(g.user_id or g.user_email)
    return jsonify({"status": "cancelled"})


@app.route("/action/account/export/download")
def action_account_export_download():
    """Télécharge l'archive préparée et sanctuarise le flux jusqu'à transmission complète à 100%."""
    key = str(g.user_id or g.user_email).strip().lower()
    with _EXPORT_JOBS_LOCK:
        job = _EXPORT_JOBS.get(key)
        if not job or not job.get("ready") or not job.get("archive_path") or not os.path.isfile(job["archive_path"]):
            flash("Aucune archive prête pour le téléchargement ou archive expirée.", "warning")
            return redirect(url_for("index"))

        archive_path = job["archive_path"]
        download_filename = job.get("filename") or "echo_user_export.tar.xz"
        tmp_dir = job.get("tmp_dir")
        file_sz = job.get("size_bytes") or (os.path.getsize(archive_path) if os.path.isfile(archive_path) else 0)

    headers = {
        "Content-Disposition": f'attachment; filename="{download_filename}"',
        "Content-Length": str(file_sz),
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }
    return Response(
        stream_with_context(
            stream_archive_file(key, archive_path, tmp_dir)
        ),
        mimetype="application/x-xz",
        headers=headers,
    )


@app.route("/action/account/export")
def action_account_export():
    """Point d'entrée synchrone direct (rétrocompatibilité tests et téléchargements directs)."""
    key = str(g.user_id or g.user_email).strip().lower()
    with _EXPORT_JOBS_LOCK:
        job = _EXPORT_JOBS.get(key)
        if job and job.get("ready") and job.get("archive_path") and os.path.isfile(job["archive_path"]):
            return redirect(url_for("action_account_export_download"))

    user_lock = get_user_export_lock(key)
    if not user_lock.acquire(blocking=False):
        flash("Un export de vos données est déjà en cours de génération.", "warning")
        return redirect(url_for("index"))

    tmp_dir = ""
    try:
        archive_path, download_filename, tmp_dir = build_user_export_archive(
            g.user_id,
            g.user_email,
            g.display_name,
            user_key=key,
        )

        def _cleanup_sync():
            user_lock.release()

        return Response(
            stream_with_context(
                stream_and_cleanup_archive(archive_path, tmp_dir, on_cleanup=_cleanup_sync)
            ),
            mimetype="application/x-xz",
            headers={
                "Content-Disposition": f'attachment; filename="{download_filename}"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as e:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        user_lock.release()
        flash(f"Erreur lors de la génération de l'archive : {e}", "danger")
        return redirect(url_for("index"))


def _revoke_owui_token():
    """Révoque de manière non-bloquante le jeton JWT Open WebUI courant s'il est présent."""
    owui_token = request.cookies.get("token")
    if owui_token and HAS_HTTPX and httpx is not None:
        try:
            with httpx.Client(timeout=3.0) as client:
                client.post(
                    f"{WEBUI_INTERNAL_URL}/api/v1/auths/signout",
                    headers={"Authorization": f"Bearer {owui_token}"},
                )
        except Exception:
            pass


@app.route("/action/account/delete", methods=["POST"])
def action_account_delete():
    if g.user_email == SYSTEM_ADMIN_EMAIL or g.user_email.endswith("@echo.local"):
        flash("Action interdite : la suppression d'un compte système est bloquée.", "danger")
        return redirect(url_for("index"))

    confirm_text = (request.form.get("confirm_text") or "").strip()
    current_pwd = request.form.get("current_password") or ""
    totp_code = request.form.get("totp_code") or ""

    if confirm_text != "SUPPRIMER":
        flash("Veuillez saisir exactement « SUPPRIMER » pour confirmer la suppression.", "danger")
        return redirect(url_for("index"))

    if not verify_user_password(g.user_email, current_pwd):
        flash("Mot de passe actuel incorrect. Suppression annulée.", "danger")
        return redirect(url_for("index"))

    if g.has_totp and not verify_user_totp(g.user_email, totp_code):
        flash("Code MFA TOTP invalide. Suppression annulée.", "danger")
        return redirect(url_for("index"))

    try:
        # 1. Option B : Suppression de l'entrée maîtresse dans webui.db (déclenche le GC nocturne de l'admin-manager)
        if os.path.exists(WEBUI_DB_PATH):
            with closing(sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True, timeout=10.0)) as w_conn:
                u_row = w_conn.execute(
                    "SELECT id, role FROM user WHERE email = ?",
                    (g.user_email,),
                ).fetchone()
                if u_row:
                    uid, u_role = str(u_row[0]), str(u_row[1] or "user")
                    if u_role == "admin":
                        admins = w_conn.execute(
                            "SELECT email FROM user WHERE role = 'admin'"
                        ).fetchall()
                        human_admins = [
                            str(a[0])
                            for a in admins
                            if a and a[0] and not str(a[0]).endswith("@echo.local")
                        ]
                        if len(human_admins) <= 1 and g.user_email in human_admins:
                            flash(
                                "Action bloquée : suppression impossible du dernier administrateur humain.",
                                "danger",
                            )
                            return redirect(url_for("index"))
                    try:
                        w_conn.execute("DELETE FROM auth WHERE id = ?", (uid,))
                    except Exception:
                        pass
                    w_conn.execute("DELETE FROM user WHERE id = ?", (uid,))
                    w_conn.commit()

        # 2. Suppression du compte et des sessions dans auth.db (auth-manager)
        with closing(sqlite3.connect(AUTH_DB_PATH, timeout=10.0)) as conn:
            conn.execute("DELETE FROM sessions WHERE email = ?", (g.user_email,))
            conn.execute("DELETE FROM users WHERE email = ?", (g.user_email,))
            conn.commit()

        # 3. Révocation Open WebUI & nettoyage des cookies SSO
        _revoke_owui_token()
        session.clear()
        domain = _get_effective_domain()
        target_url = f"https://auth.{domain}/login" if domain != "localhost" else "/logged-out"
        resp = redirect(target_url)
        resp.delete_cookie("echo_auth_session")
        resp.delete_cookie("token")
        if domain != "localhost":
            resp.delete_cookie("echo_auth_session", domain=f".{domain}")
            resp.delete_cookie("token", domain=f".{domain}")
        return resp
    except Exception as e:
        flash(f"Erreur lors de la suppression du compte : {e}", "danger")
        return redirect(url_for("index"))


@app.route("/logout")
def logout():
    _revoke_owui_token()
    if g.current_session_id and os.path.exists(AUTH_DB_PATH):
        try:
            with closing(sqlite3.connect(AUTH_DB_PATH, timeout=5.0)) as conn:
                conn.execute(
                    "DELETE FROM sessions WHERE session_id = ? AND email = ?",
                    (g.current_session_id, g.user_email),
                )
                conn.commit()
        except Exception:
            pass

    session.clear()
    domain = _get_effective_domain()
    target_url = (
        f"https://ui.{domain}/sso-logout"
        if domain != "localhost"
        else "https://auth.localhost/logout"
    )
    resp = redirect(target_url)
    resp.delete_cookie("echo_auth_session")
    resp.delete_cookie("token")
    if domain != "localhost":
        resp.delete_cookie("echo_auth_session", domain=f".{domain}")
        resp.delete_cookie("token", domain=f".{domain}")
    return resp
