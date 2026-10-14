# 🌌 ECHO Framework - Connaissance Sémantique : `24-docker-echo-auth-manager`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `24-docker-echo-auth-manager`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'application **ECHO Auth Manager**, qui agit comme un **IdP (Identity Provider) Autonome**. Protégé derrière le WAF BunkerWeb, il constitue le portail d'entrée exclusif pour accéder à l'interface Open WebUI. Il centralise la gestion des sessions utilisateurs, l'authentification forte (MFA/TOTP) et l'intégration SSO.

## 2. Cartographie des Fichiers et Algorithmes

### `auth_server.py` (v1.6)
Le cœur du gestionnaire d'identités (FastAPI).
- **Authentification Locale & Hachage Argon2** : Gère les comptes utilisateurs et sessions SSO dans SQLite (`auth.db` en mode WAL, tables `users` et `sessions`) avec hachage de mots de passe et réponses de sécurité via **Argon2** (`passlib.hash.argon2`), gestion de l'expiration des mots de passe temporaires (`temp_pass_expires`), suivi de la date d'enrôlement (`last_enrollment`) et traçabilité de session (`ip_address`, `os`, `browser`, `device`). Dans `stack-echo.yml`, `JWT_EXPIRES_IN` d'Open WebUI est aligné sur `${ECHO_JWT_EXPIRES_IN:-4w}` (4 semaines par défaut) et soutenu par un bounce `/sso-logout` (`Clear-Site-Data: "cache"`).
- **Sécurité MFA (TOTP)** : Contient la logique de génération (`pyotp`) et de vérification des mots de passe à usage unique basés sur le temps (Time-based One-Time Password), imposés comme seconde ligne de défense.
- **Synchronisation avec BunkerWeb (`/api/verify`)** : Le serveur valide les cookies de session et renvoie les en-têtes `X-Webui-User` et `X-Webui-Name` à BunkerWeb (Forward Auth) pour laisser passer le trafic légitime ou bloquer l'accès. Il renvoie un code HTTP `401` (au lieu de `403`) en cas d'absence ou d'expiration de session afin de déclencher proprement la redirection vers le portail `/login` ou la déconnexion transparente côté Open WebUI.

### Dossiers `static/` & `templates/`
- Contiennent les interfaces de connexion front-end (HTML/CSS/JS) présentées à l'utilisateur lors de son authentification, stylisées selon l'esthétique du projet ECHO. Le formulaire d'enrôlement (`enroll.html`) a été réagencé ergonomiquement pour exiger la **Question de Sécurité** *avant* la saisie du code TOTP.

## 3. Dépendances Logiques
- Ce Worker s'interface intimement avec la librairie `echo_auth.py` (dans `14-owui-libs`) pour appliquer les règles de révocation.
- Il partage le volume `echo-auth-data` où est persistée la base de données SQLite contenant les hachages de mots de passe, les secrets TOTP et les tokens de session en cours.
- Dépendance totale à l'infrastructure WAF (`bunkerweb-stack.yml`).
