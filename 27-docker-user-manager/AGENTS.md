# 👤 Composant : 27-docker-user-manager (Mémoire Sémantique Locale)

> **Rôle :** Portail Utilisateur Self-Service (`echo-user-manager`, exposé en mode public via BunkerWeb sous `https://my.{ECHO_DOMAIN}`, IP statique `.27`, port interne `3002`). Permet à chaque utilisateur authentifié via SSO/MFA de consulter ses statistiques (taille du profil, chats, mémoire vectorielle Qdrant, compte à rebours d'expiration SSO), de gérer ses connexions actives, de modifier son nom et son mot de passe, d'exporter l'intégralité de ses données Open WebUI et Qdrant dans une archive `.tar.xz`, de supprimer son compte et de se déconnecter.

---

## 1. 📂 Cartographie des Fichiers & Rôles

L'architecture est structurée en 4 couches modulaires calquées sur `20-docker-admin-manager` :

- **`server.py` (v5.236.5.1)** : Point d'entrée minimaliste chargeant l'application Flask en mode multi-threadé (`threaded=True`, port `3002`).
- **`core/config.py` (v5.236.5.1)** : Configuration centrale, chargement optionnel de `/app/echo_libs` (`COLLECTION_META_ARTIFACTS`, `COLLECTION_SESSION_RAG`, `ECHO_SYNC_EXCLUDE_LIST`, `sanitize_echo_id`), isolation du cookie de session Flask (`SESSION_COOKIE_NAME = "echo_um_session"`) et chemins SQLite (`webui.db`, `auth.db`, `auth-settings.json`).
- **`core/utils.py` (v5.236.5.1)** : Fonctions utilitaires de formatage d'octets (`_format_bytes`), de calcul récursif de taille d'arborescence (`_get_tree_size`), de lecture de version (`get_echo_version`) et de lecture de la politique SSO (`get_auth_settings`).
- **`core/security.py` (v5.236.5.1)** : Middleware `@app.before_request` (`check_user_sso_auth`) appliquant une authentification Zero-Trust en mode public : vérification de `ECHO_SSO_SECRET` via `hmac.compare_digest` sur `X-Echo-Sso-Secret`, extraction de `X-Webui-User` et `echo_auth_session`, résolution de l'identité dans `auth.db` et `webui.db` stockée dans le contexte thread-local `flask.g`. Fournit également `verify_user_password`, `hash_user_password` (Argon2id), `verify_user_totp` et `validate_password_policy`.
- **`services/export_service.py` (v5.236.5.1)** : Moteur d'export souverain `.tar.xz` (`build_user_export_archive`, `stream_and_cleanup_archive`) protégé par un verrou granulaire non-bloquant par utilisateur (`get_user_export_lock`). Extrait en JSON les enregistrements `webui.db` (`export_webui_user_records`), copie l'arbre `/app/backend/data/users/<uid>` avec déréférencement sécurisé des symlinks internes et dumps JSON des bases `session.db` / `identity.db` (en masquant `auth_data` et `identity_vault`), et extrait par pagination (`scroll`) les points Qdrant (`echo_meta_artifacts` et `echo_session_rag`).
- **`routes/routes.py` (v5.236.5.1)** : Contrôleurs HTTP scopés sur `g.user_email` / `g.user_id` (`/health`, `/`, `/api/me/stats`, `/api/me/sessions`, `/action/session/revoke`, `/action/session/revoke_others`, `/action/profile/rename`, `/action/profile/password`, `/action/account/export`, `/action/account/delete`, `/logout`).
- **`templates/templates.py` (v5.237.1.1)** : Interface graphique Dark Mode Bootstrap 5.3 (`HTML_USER_DASHBOARD`) avec lien direct vers `https://ui.{ECHO_DOMAIN}`, bandeau de 4 KPI temps réel (dont compte à rebours d'expiration SSO calculé sur `expires_at`), gestion du profil/mot de passe, table des connexions actives avec résolution GeoIP asynchrone et export/suppression de compte.
- **`Dockerfile` & `requirements.txt`** : Image basée sur `echo-python-base:latest` (`flask`, `passlib[argon2]`, `pyotp`, `httpx`).

---

## 2. 🛡️ Modèle de Sécurité & Moindre Privilège

1. **Déploiement Exclusif Mode Public (`bunkerweb-stack.yml`)** : Le service `echo-user-manager` est déclaré uniquement dans `01-config/bunkerweb-stack.yml`. Il n'est instancié que lorsque le WAF BunkerWeb est actif et n'expose aucun port sur l'hôte.
2. **Étanchéité des Volumes** : Contrairement à `echo-admin-manager`, ce conteneur ne monte **jamais** `/var/run/docker.sock`, `/opt/ECHO/.secrets`, `echo-backups`, `echo-n8n-data` ni `echo-mcp-cache`.
3. **Suppression de Compte & Purge Nocturne (Option B)** : L'action `/action/account/delete` (protégée par mot de passe + TOTP et bloquée pour `@echo.local` ou le dernier administrateur humain) supprime le compte dans `auth.db` (`users` + `sessions`) ainsi que l'entrée maîtresse dans `webui.db` (`user` + `auth`), déclenchant automatiquement la purge multi-volumes complète par le Garbage Collector nocturne de `20-docker-admin-manager`.
