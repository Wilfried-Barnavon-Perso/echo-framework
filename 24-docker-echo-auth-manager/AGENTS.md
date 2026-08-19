# 🌌 ECHO Framework - Connaissance Sémantique : `24-docker-echo-auth-manager`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `24-docker-echo-auth-manager`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'application **ECHO Auth Manager**, qui agit comme un **IdP (Identity Provider) Autonome**. Protégé derrière le WAF BunkerWeb, il constitue le portail d'entrée exclusif pour accéder à l'interface Open WebUI. Il centralise la gestion des sessions utilisateurs, l'authentification forte (MFA/TOTP) et l'intégration SSO.

## 2. Cartographie des Fichiers et Algorithmes

### `auth_server.py`
Le cœur du gestionnaire d'identités.
- **Authentification Multi-Provider** : Implémente la validation des Master Keys, le flux OAuth2 (couplé au serveur PKCE local) et les comptes locaux.
- **Sécurité MFA (TOTP)** : Contient la logique de génération et de vérification des mots de passe à usage unique basés sur le temps (Time-based One-Time Password), imposés comme seconde ligne de défense.
- **Synchronisation avec BunkerWeb** : Le serveur gère la validation des tokens de session et renvoie les headers d'autorisation appropriés à BunkerWeb pour laisser passer le trafic légitime ou bloquer l'accès.

### Dossiers `static/` & `templates/`
- Contiennent les interfaces de connexion front-end (HTML/CSS/JS) présentées à l'utilisateur lors de son authentification, stylisées selon l'esthétique du projet ECHO.

## 3. Dépendances Logiques
- Ce Worker s'interface intimement avec la librairie `echo_auth.py` (dans `14-owui-libs`) pour appliquer les règles de révocation.
- Il partage le volume `echo-auth-data` où est persistée la base de données SQLite contenant les hachages de mots de passe, les secrets TOTP et les tokens de session en cours.
- Dépendance totale à l'infrastructure WAF (`bunkerweb-stack.yml`).
