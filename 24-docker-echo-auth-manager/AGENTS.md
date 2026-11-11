# 🌌 ECHO Framework - Connaissance Sémantique : `24-docker-echo-auth-manager`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `24-docker-echo-auth-manager`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'application **ECHO Auth Manager**, qui agit comme un **IdP (Identity Provider) Autonome**. Protégé derrière le WAF BunkerWeb, il constitue le portail d'entrée exclusif pour accéder à l'interface Open WebUI. Il centralise la gestion des sessions utilisateurs, l'authentification forte (MFA/TOTP) et l'intégration SSO.

## 2. Cartographie des Fichiers et Algorithmes

### `auth_server.py` (v5.239.2.1)
Le cœur du gestionnaire d'identités (FastAPI).
- **Authentification Locale & Hachage Argon2** : Gère les comptes utilisateurs et sessions SSO dans SQLite (`auth.db` en mode WAL, tables `users` et `sessions`) avec hachage de mots de passe et réponses de sécurité via **Argon2** (`passlib.hash.argon2`), gestion de l'expiration des mots de passe temporaires (`temp_pass_expires`), suivi de la date d'enrôlement (`last_enrollment`) et traçabilité de session (`ip_address`, `os`, `browser`, `device`). Dans `stack-echo.yml`, `JWT_EXPIRES_IN` d'Open WebUI est aligné sur `${ECHO_JWT_EXPIRES_IN:-4w}` (4 semaines par défaut) et soutenu par un bounce `/sso-logout` (`Clear-Site-Data: "cache"`).
- **Garde Anti-Bruteforce & Politique de Verrouillage Dual-Tier** :
  - **Délai inter-essais paramétrable (`login_retry_delay_sec`)** : Constante `LOGIN_RETRY_DELAY_VALID_SEC = (0, 5, 10, 20)`. Imposé sur tous les comptes (enrôlés ou non via `last_attempt_at` SQLite) ainsi que sur les IP inconnues/adresses non reconnues via le cache mémoire LRU `_ip_last_attempt` (purge automatique au-delà de 2048 entrées).
  - **Verrouillage des comptes non-enrôlés (`must_enroll == 1`)** : Seuil d'échecs `unenrolled_max_attempts` clamped dans `[1..20]` et sanction `unenrolled_lockout_min` validée sur `UNENROLLED_LOCKOUT_VALID_MIN = (-1, 1, 3, 10, 60, 180, 1440)`. Le verrouillage est persisté dans `users` (`failed_attempts`, `last_attempt_at`, `locked_until`). La valeur `-1` correspond à un verrouillage définitif (nécessitant une réinitialisation MFA par l'administrateur) ; les durées positives temporisent le compte avec déverrouillage automatique à échéance pour un nouveau cycle de tentatives.
  - **Fonctions factorisées de sécurité** :
    * `_extract_client_ip(request)` : Extraction unifiée de l'IP cliente réelle (`X-Forwarded-For` / `X-Real-IP` / `request.client.host`), partagée entre le garde et `create_session()`.
    * `_format_duration_fr(seconds)` & `_format_sanction_label(lockout_min)` : Formatage en français des durées restantes (`X min Ys`, `Xh Ym`, `Xs`) et des libellés de sanction.
    * `get_security_policy(settings)` : Extraction et clamping strict des paramètres de sécurité avec valeurs par défaut robustes (`5s`, `5` tentatives, `10` minutes).
    * `check_login_guard(user, client_ip, now, settings)` & `record_login_attempt(user, client_ip, is_success, now, settings)` : Gestion unifiée des vérifications pré-authentification et de l'enregistrement des tentatives (succès / échecs) avec mise à jour SQLite atomique.
  - **Enrichissement de `_render_login()`** : Injection dans le contexte Jinja2 de `email_prefill`, `retry_delay_sec`, `cooldown_sec`, `lockout_sec`, `attempts_remaining`, `max_attempts` et `sanction_label`.
  - **Réinitialisation automatique** : Réinitialisation à zéro de `failed_attempts` et `locked_until` lors d'un enrôlement réussi (`update_user_enrollment()`) ou d'une authentification valide.
- **Politique Dual-Tier des Sessions SSO** :
  - **Champs de session SQLite** : Chaque session stocke explicitement `is_trusted` (INTEGER 0 ou 1) et `expires_at` (INTEGER timestamp Unix ou NULL pour session éternelle).
  - **Mode sans confiance (défaut)** : Limité aux durées courtes `UNTRUSTED_VALID_HOURS = (1, 2, 4, 8, 12, 24)` bornées par `untrusted_session_timeout` (24h max). Purge opportuniste lors de `create_session()`.
  - **Mode terminal de confiance (`trusted_device`)** : Accessible uniquement sur action explicite de l'utilisateur, avec durées étendues `TRUSTED_VALID_HOURS = (168, 720, 2160, 4380, 8760, 0)` bornées par `session_timeout` de l'Admin Manager. Pour la session permanente (`0`), le cookie est émis avec `COOKIE_ETERNAL_MAX_AGE = 315360000` (10 ans).
  - **Fonction factorisée `resolve_session_state()`** : Interroge SQLite, applique un fallback sécurisé de 24h (`created_at + 86400`) pour les sessions antérieures non migrées, révoque automatiquement la session via `delete_session()` si l'échéance est atteinte, et calcule le compte à rebours résiduel `time_remaining_sec`. `get_current_user_email()` délègue directement à cette fonction.
  - **Cache réactif de configuration** : `get_auth_settings()` invalide immédiatement son cache en mémoire dès détection d'une mutation du `mtime` de `auth-settings.json` (`_settings_mtime`), évitant tout délai de propagation lors d'une modification par l'Admin Manager.
  - **Résolution sécurisée de redirection (`resolve_safe_next` & `get_default_redirect_url`)** : Assainit toute URL `next` en la restreignant aux sous-domaines applicatifs autorisés par la directive CSP `form-action` de BunkerWeb (`ui.*`, `am.*`, `my.*`), exclut les chemins relatifs (`/...`) et les auto-références vers `auth.*` pour empêcher toute boucle de redirection 302 (`/` $\leftrightarrow$ `/login`), replie par défaut sur `https://ui.{ECHO_DOMAIN}/` (ou `http://localhost:3000/` en local), et propage `next` de bout en bout à travers `/login`, `/enroll` et `/recovery`.
  - **Rendu factorisé `_render_login()`** : Centralise la résolution `resolve_safe_next(next_url)` et le clamping des seuils pour les 8 occurrences de rendu de `login.html`.
  - **Endpoint d'inspection `/api/session/status`** : Expose pour le client connecté le statut d'authentification (`authenticated`, `email`, `is_trusted`, `created_at`, `expires_at`, `time_remaining_sec`).
- **Sécurité MFA (TOTP)** : Contient la logique de génération (`pyotp`) et de vérification des mots de passe à usage unique basés sur le temps (Time-based One-Time Password), imposés comme seconde ligne de défense.
- **Synchronisation avec BunkerWeb (`/api/verify`)** : Le serveur valide les cookies de session et renvoie les en-têtes `X-Webui-User` et `X-Webui-Name` à BunkerWeb (Forward Auth) pour laisser passer le trafic légitime ou bloquer l'accès. Il renvoie un code HTTP `401` en cas d'absence ou d'expiration de session afin de déclencher proprement la redirection vers le portail `/login` ou la déconnexion transparente côté Open WebUI.

### Dossiers `static/` & `templates/`
- **`templates/login.html`** :
  - **Gestion de session** : Intègre la case à cocher `trusted_device` (`value="1"`, libellé *"Ce terminal est de confiance."*), le bloc d'avertissement de sécurité explicite `#trusted-warning` (affiché dynamiquement uniquement si la case est cochée), le sélecteur dynamique `session_duration` (filtré par le seuil de sécurité actif) et le bandeau d'aperçu live `#session-expiry-preview` actualisé chaque seconde via `setInterval(updatePreview, 1000)` affichant date, heure d'expiration et délai restant, ou la mention permanente pour la session éternelle.
  - **Protection Anti-Bruteforce & Compte à Rebours Temps Réel** :
    * Pré-remplissage résilient de l'adresse email (`value="{{ email_prefill | default('') }}"`) lors des réaffichages post-échec.
    * Bloc informatif `#login-security-policy` restituant dynamiquement les conditions d'accès (délai inter-essais `retry_delay_sec`, nombre d'essais max avant sanction) ou le quota résiduel précis d'essais d'un compte non-enrôlé (`attempts_remaining / max_attempts`) et la sanction encourue (`sanction_label`).
    * Bandeau live `#login-countdown-banner` et contrôle d'état dynamique du bouton `#login-submit-btn` cadencé à 1000ms :
      - *Verrouillage définitif (`lockoutSec === -1`)* : alerte rouge persistante et désactivation stricte du bouton en attente d'une réinitialisation MFA par l'administrateur.
      - *Verrouillage temporaire (`lockoutSec > 0`)* : alerte live décrémentée chaque seconde au format `HH:MM:SS` / `MM:SS` / `Xs`, bouton verrouillé, bascule en message de succès vert à échéance avec restauration du quota nominal (`N/N`) et réactivation immédiate du formulaire.
      - *Délai inter-essais (`cooldownSec > 0`)* : bandeau ambre live bloquant les soumissions précipitées pendant la temporisation configurée (ex: 5s, 10s, 20s), masquage automatique et déverrouillage fluide du bouton à `0s`.
- **`templates/enroll.html`** : Le formulaire d'enrôlement exige la Question de Sécurité avant la saisie du code TOTP.

### Migration CLI `docker exec` (Plafonnement de l'existant à 24h)
Pour migrer à chaud les bases de données existantes sans déconnecter immédiatement les utilisateurs mais en bridant les sessions non marquées à un maximum de 24h résiduelles :
```bash
docker exec -i echo-auth-manager python -c "import sqlite3, time; conn = sqlite3.connect('/app/auth-data/auth.db'); cur = conn.cursor(); cols = [r[1] for r in cur.execute('PRAGMA table_info(sessions)').fetchall()]; ('is_trusted' not in cols) and cur.execute('ALTER TABLE sessions ADD COLUMN is_trusted INTEGER DEFAULT 0'); ('expires_at' not in cols) and cur.execute('ALTER TABLE sessions ADD COLUMN expires_at INTEGER'); now = int(time.time()); exp_target = now + 86400; cur.execute('UPDATE sessions SET is_trusted = 0, expires_at = CASE WHEN created_at + 86400 > ? THEN created_at + 86400 ELSE ? END WHERE expires_at IS NULL OR is_trusted = 0', (now, exp_target)); migrated = cur.rowcount; conn.commit(); print(f'[ECHO-AUTH] Migration 24h terminee : {migrated} session(s) basculee(s) en mode sans confiance (24h max).')"
```

## 3. Dépendances Logiques
- Ce Worker s'interface avec `echo-admin-manager` (`20-docker-admin-manager`) et `echo-open-webui` via la base SQLite partagée `auth.db` et `auth-settings.json` sur le volume `echo-auth-data`.
- Il partage le volume `echo-auth-data` où est persistée la base de données SQLite contenant les hachages de mots de passe, les secrets TOTP et les tokens de session en cours.
- Dépendance totale à l'infrastructure WAF (`bunkerweb-stack.yml`).
