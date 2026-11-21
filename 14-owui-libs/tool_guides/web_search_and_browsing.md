---
domain_id: web_search_and_browsing
version: 6.17.2.1
updated_at: 2026-11-21
name: Recherche Web Souveraine, Navigateur Autonome & Géolocalisation Maps
description: Escalade de recherche Web (Réponses instantanées, Méta-moteur de recherche, Deep Research), navigation interactive via le Navigateur Autonome et ancrage cartographique Maps.
tools: [search_instant_answer, search_web, delegate_deep_research, delegate_web_browsing, analyze_web_page, archive_web_page, web_browse_reset, get_browser_frames_history, retain_browser_frame, open_browser_ui, search_maps]
keywords: [web, recherche, search, navigateur, browser, browserview, browserviews, capture, frame, clip, mp4, retain_browser_frame, get_browser_frames_history, open_browser_ui, archive_web_page, analyze_web_page, http_status, url, page, maps, carte, géolocalisation, deep_research, actualité]
---

# MANUEL OPÉRATIONNEL : RECHERCHE WEB, NAVIGATEUR AUTONOME & MAPS (`web_search_and_browsing`)

## 1. HIÉRARCHIE D'ESCALADE DE L'INVESTIGATION WEB

1. **Niveau 1 — Définition / Fait Encyclopédique Statique (`search_instant_answer`)** :
   - Interroge le moteur de réponses instantanées pour des définitions de concepts, biographies ou dates établies (**proscrit pour l'actualité**).
   - **Enchaînement** : Si `status="no_result"`, Le Modèle DOIT basculer sur `search_web`.
2. **Niveau 2 — Recherche Web Rapide par Extraits (`search_web`)** :
   - Interroge le Méta-moteur de recherche et retourne jusqu'à 8 extraits courts (snippets) et URLs.
   - `categories` : `'general'`, `'it'`, `'science'`, `'images'`, `'videos'`, `'social media'`, `'news'`, `'music'`, `'map'`.
   - `time_range` : `'day'`, `'week'`, `'month'`, `'year'`. Le Modèle **DOIT TOUJOURS** renseigner `time_range` dès que la requête porte sur l'actualité ou des faits récents.
   - `language` : `'fr-FR'` (défaut), `'en-US'` ou `'all'` ; `safesearch` : `1` (défaut) ; `engines` : moteurs cibles optionnels séparés par des virgules.
   - **RÈGLE ANTI-SPAM** : Il est **ABSOLUMENT INTERDIT** d'exécuter plus de 2 appels simultanés à `search_web` dans un même tour.
   - **Vérification du contenu complet** : Si les extraits sont insuffisants ou nécessitent lecture intégrale d'une URL identifiée, Le Modèle DOIT utiliser `analyze_web_page(url, query)` ou `delegate_deep_research`.
3. **Niveau 3 — Recherche Profonde Multi-Sources (`delegate_deep_research`)** :
   - **Contrainte d'environnement** : **ABSOLUMENT INTERDIT** depuis un Sous-Agent headless (`DELEGATE_AGENT_BLACKLIST`).
   - Délègue une investigation complexe à un Sous-Agent autonome multi-tours (`target_model_key="MODEL_PROMETHEUS"` par défaut, `engines` optionnel ex: `'scholar,wikipedia'`, passe autonome bornée à **15 tours d'outils / 300s**) qui croise les requêtes, ouvre les pages via le Navigateur Autonome et synthétise un rapport sourcé.
4. **Niveau 4 — Interaction Web Complexe (`delegate_web_browsing`)** :
   - Déploie le Navigateur Autonome doté de vision multimodale et de contrôle du DOM pour naviguer sur des applications web dynamiques, cliquer, remplir des formulaires ou paginer.

---

## 2. LE NAVIGATEUR AUTONOME

### 2.1 Lecture Sémantique & Archivage Direct d'une URL
- `analyze_web_page(url, query)` (**À PRIVILÉGIER sur une URL connue**) :
  - Ouvre `url`, extrait l'intégralité du texte de la page et délègue son analyse à un Sous-Agent (`MODEL_APOLLO`) pour répondre précisément à la directive `query` sans saturer la Mémoire Vectorielle de la session.
- `archive_web_page(url)` :
  - Extrait le texte intégral d'une page web volumineuse, l'enregistre en Markdown et l'indexe dans la **Mémoire Vectorielle de la session** (interrogeable via `search_sessions_context`).

### 2.2 Navigation Interactive Multi-Étapes (`delegate_web_browsing`)
- `task_objective` : Mission complète confiée au Navigateur Autonome (DOIT inclure le contexte général et spatio-temporel utile).
- `start_url` : URL initiale optionnelle.
- `target_model_key` : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut), `"MODEL_PROMETHEUS"` ou `"MODEL_ATHENA"`.
- `sub_sid` : Identifiant de session de navigation optionnel (`thread_web_*`). Permet de reprendre une navigation existante après un point de contrôle (`status="suspended_checkpoint"` au bout de **8 tours d'actions / 120s**) soit en rappelant `delegate_web_browsing(task_objective=..., sub_sid=...)`, soit via `control_subagents(action="resume", target_ids=[sub_sid])`. Les métadonnées de retour incluent la télémétrie HTTP (`final_url`, `http_status`, `http_status_text`, `navigation_statuses`), et Le Sous-Agent est informé des codes HTTP `>= 400`.
- **Administration du Navigateur Autonome** :
  - `get_browser_frames_history(depth=10)` : Opère en **lecture seule non destructive** et restitue l'historique des dernières captures d'écran présentes sur disque (`file_id`, `date`, `frame`, `session_id`, `url`, `http_status`, `action`, `status`, `retained`, et `has_clip` indiquant la présence d'un clip vidéo MP4 d'action rejouable et téléchargeable dans l'onglet Navigateur Web).
  - `retain_browser_frame(file_id, retain=True)` : Marque une capture de navigation (`resource_type="browserview"`, stockée dans `browserviews/`) au statut `"retained"` pour la préserver (ainsi que son clip vidéo MP4 d'action) de la purge automatique en fin de session (ou la repasse en `"transient"` si `retain=False`). Les clips `"transient"` sont soumis à un plafond FIFO par conversation (`50 clips / 60 Mo`, sans supprimer les captures HD), les captures `"transient"` sont purgées en fin de session navigateur (`web_browse_reset` / `control_subagents(action="close", target_ids=[sub_sid])` / expiration), et le clonage/migration de session ne transfère que les captures `"retained"`.
  - `open_browser_ui(frame_index=None)` : Ouvre l'onglet natif **Navigateur Web** dans l'interface de L'Utilisateur avec suivi temps réel continu (ou positionné sur l'index de capture indiqué). **ABSOLUMENT INTERDIT** depuis un Sous-Agent headless (`DELEGATE_AGENT_BLACKLIST`).
  - `web_browse_reset()` : Réinitialise immédiatement la session active du Navigateur Autonome.
  - Fermeture et purge d'une session `thread_web_*` : Le Modèle DOIT appeler **`control_subagents(action="close", target_ids=[sub_sid])`**.

---

## 3. GÉOLOCALISATION & AFFICHAGE DE CARTES (`search_maps`)
- `search_maps(query, latitude=None, longitude=None, print_map=True)` :
  - Recherche des données géo-spatiales vérifiées (lieux, adresses, horaires, itinéraires, avis) et affiche une carte interactive (si `print_map=True`).
  - `latitude` / `longitude` : Coordonnées décimales optionnelles (issues de `<artifact id="AEC_localisation">`) pour centrer la recherche.
