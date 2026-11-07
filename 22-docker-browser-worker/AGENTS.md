# 🌌 ECHO Framework - Connaissance Sémantique : `22-docker-browser-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `22-docker-browser-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API Playwright**. Il s'agit d'un conteneur Docker (Worker) qui héberge un navigateur web *headless* (Playwright) piloté par FastAPI. Son rôle est de naviguer de manière autonome sur internet, d'exécuter des actions DOM complexes (clics, scroll, formulaires) pour le compte de l'outil `navigation_engine_tool.py`, et d'enregistrer le flux vidéo des sessions.

## 2. Cartographie des Fichiers et Algorithmes

### `browser_api.py` (v5.236.4.1)
Fichier monolithique exposant l'API de contrôle du navigateur asynchrone (FastAPI).

#### A. Pilotage Asynchrone (Playwright) & Architecture Zéro-Trace DOM
- **Sémantique** : Initialise des contextes Playwright (`async_playwright()` avec support WebGL SwiftShader `--enable-webgl --use-gl=angle --use-angle=swiftshader`). Il crée un onglet par session (`sid`) et isole les cookies et le cache tout en conservant le `chat_id` parent (`session.chat_id`) afin de router les téléchargements (`download`, `save_target`) vers la drop-zone du chat réel (`/app/downloads/<user_id>/<chat_id>/browser`) même lorsque la session est pilotée par un sous-agent (`sub_sid`).
- **Zéro-Trace DOM (Élimination du Tatouage)** : Remplacement complet du tatouage d'attributs intrusifs (`data-echo-index`, `data-echo-found`) par le calcul en mémoire JS d'un sélecteur CSS canonique et déterministe (`GET_CSS_PATH_JS` : `html > ... > tag:nth-child(k)`). Les sélecteurs sont mappés côté serveur dans `session.dom_selectors` et résolus via `session.resolve_indexed_locator(page, idx)` à travers les différentes frames avec fallback résilient sur la boîte englobante et `document.elementFromPoint`.
- **Endpoints d'Action** : Expose les routes REST FastAPI (`POST /start_session`, `POST /action`, `POST /render_ephemeral_view` — rendu HTML incognito isolé 100 % RAM Zéro-Disque avec `wait_until="load"`, polling actif 100ms sur l'apparition des éléments graphiques `#visual-target` (`svg`, `canvas`), `settle_ms=250`, collecte d'erreurs JS/DOM `data-echo-visual-error`/`⚠️` et destruction garantie du contexte en `finally` —, `POST /screencast/start`, `POST /screencast/stop`, `POST /screencast/latest`, `POST /reset` et `GET /health`) permettant au moteur principal de déclencher des manipulations sur la page cible.

#### B. Streaming Vidéo Bridé & Incrustation Active du Curseur Hors-DOM (Pillow)
- **Sémantique** : Enregistre le flux d'interactions de la page en temps réel via le protocole Chrome DevTools (CDP `Page.startScreencast`).
- **Optimisation FPS** : L'API asynchrone est bridée à **9 FPS** (Frames Per Second) pour réduire drastiquement la charge CPU et la bande passante du serveur lors du traitement et du renvoi du flux vidéo, sans perdre l'intelligibilité de la session pour l'utilisateur.
- **Incrustation Active du Curseur Hors-DOM** : Suppression intégrale de l'élément DOM `#echo-cursor` et des écouteurs globaux de souris (`window.__echo_mouse_x/y`). Le pointeur de souris rouge (`#ff0044`, contour blanc `#ffffff` 2px, rayon 8px) est incrusté directement au niveau binaire dans les trames JPEG du screencast via Pillow (`ImageDraw`) lors de chaque micro-mouvement (`update_frame_with_cursor` dans `bezier_mouse_move` et lors de la réception des événements CDP `Page.screencastFrame`). Cette approche neutralise tout déclenchement de `MutationObserver` côté client.
- Ce flux est ensuite exploitable par le *Cockpit de Rejeu* (HUD `web_navigation_replay_action.py`).

#### C. Furtivité & Cohérence Persona Renforcée (WAF Bypass)
- **Sémantique** : Intègre un mécanisme de défense proactif contre les pare-feux applicatifs modernes (Cloudflare Turnstile, DataDome, Akamai).
- **Techniques** :
  - Argument natif Chromium `--disable-blink-features=AutomationControlled` (rendant inutile et proscrivant `delete Navigator.prototype.webdriver`).
  - Alignement dynamique de la chaîne User-Agent sur la version majeure réelle du binaire Chromium (`state.browser.version`) pour les profils Desktop (Linux x86_64) et Mobile (Android 14 Pixel 8 Pro).
  - Injection des en-têtes Client Hints stricts (`Sec-CH-UA`, `Sec-CH-UA-Mobile`, `Sec-CH-UA-Platform`).
  - Définition de getters conformes WebIDL directement sur `Navigator.prototype` (non énumérables en propre pour garantir `navigator.hasOwnProperty(...) === false` sur `languages`, `platform`, `deviceMemory`, `hardwareConcurrency`).
  - Instanciation native de l'objet `PluginArray` pour résister aux audits de type profonds (`Object.prototype.toString.call`).
  - Spoofing WebGL cohérent avec SwiftShader / Linux (`Google Inc. (Google)` / `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)`).
  - Surcharge réaliste des dimensions de fenêtre sur `Window.prototype` (`outerHeight = innerHeight + 85`).
- **DOM & Session (LRU)** : Les sessions sont maintenues en RAM via un système d'éviction LRU (capacité max : 20 sessions) pour prévenir les fuites mémoire serveur.
- **Lidar Check & Settle Asynchrone** : Intègre une attente de stabilisation post-scroll (`wait_for_settle`) et un recalibrage instantané de la bounding box juste avant le clic (Lidar Check) pour ajuster le pointeur et annuler les clics ratés dus aux Layout Shifts.
- **Ciblage Géométrique (Zoom Box)** : Implémente le rognage d'image en temps réel via le CDP de Chrome (`Page.captureScreenshot(clip=...)`) lors d'une action `action_zoom_in` sans altérer le viewport. Injecte dynamiquement un **viseur sniper (croix rouge)** au centre de l'image. Le calque de grille (absolue) s'ajuste dynamiquement pour préserver le mapping des coordonnées sans distorsion, facilitant le ciblage géométrique du LLM sans erreur balistique.

## 3. Dépendances Logiques
- Ce Worker est invoqué exclusivement par la bibliothèque `echo_browser_lib.py` et l'outil `navigation_engine_tool.py`.
- Il fonctionne de manière asynchrone pour ne pas saturer l'Event Loop globale d'Open WebUI. Il s'appuie sur une image Docker massive contenant les dépendances Chromium/Webkit.
