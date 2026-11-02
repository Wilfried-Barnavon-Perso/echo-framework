---
domain_id: web_search_and_browsing
version: 5.231.52.1
updated_at: 2026-11-02
name: Recherche Web Souveraine, Navigateur Autonome & Géolocalisation Maps
description: Escalade de recherche Web (Réponses instantanées, Méta-moteur de recherche, Deep Research), navigation interactive via le Navigateur Autonome et ancrage cartographique Maps.
tools: [search_instant_answer, search_web, delegate_deep_research, delegate_web_browsing, analyze_web_page, archive_web_page, web_browse_reset, close_web_thread, get_browser_frames_history, search_maps]
keywords: [web, recherche, search, navigateur, browser, url, page, maps, carte, géolocalisation, deep_research, actualité]
---

# MANUEL OPÉRATIONNEL : RECHERCHE WEB, NAVIGATEUR AUTONOME & MAPS (`web_search_and_browsing`)

## 1. HIÉRARCHIE D'ESCALADE DE L'INVESTIGATION WEB

1. **Niveau 1 — Définition / Fait Encyclopédique Statique (`search_instant_answer`)** :
   - Interroge le moteur de réponses instantanées pour des définitions de concepts, biographies ou dates établies (**proscrit pour l'actualité**).
   - **Enchaînement** : Si `status="no_result"`, basculer sur `search_web`.
2. **Niveau 2 — Recherche Web Rapide par Extraits (`search_web`)** :
   - Interroge le Méta-moteur de recherche et retourne jusqu'à 8 extraits courts (snippets) et URLs.
   - `categories` : `'general'`, `'it'`, `'science'`, `'images'`, `'videos'`, `'social media'`, `'news'`, `'music'`, `'map'`.
   - `time_range` : `'day'`, `'week'`, `'month'`, `'year'`. **TOUJOURS** renseigner `time_range` dès que la requête porte sur l'actualité ou des faits récents.
   - **RÈGLE ANTI-SPAM** : Il est **INTERDIT** d'exécuter plus de 2 appels simultanés à `search_web` dans un même tour.
   - **Vérification du contenu complet** : Si les extraits sont insuffisants ou nécessitent lecture intégrale d'une URL identifiée, utiliser `analyze_web_page(url, query)` ou `delegate_deep_research`.
3. **Niveau 3 — Recherche Profonde Multi-Sources (`delegate_deep_research`)** :
   - Délègue une investigation complexe à un sous-agent autonome multi-tours (`target_model_key="MODEL_PROMETHEUS"` par défaut) qui croise les requêtes, ouvre les pages via le Navigateur Autonome et synthétise un rapport sourcé.
4. **Niveau 4 — Interaction Web Complexe (`delegate_web_browsing`)** :
   - Déploie le Navigateur Autonome doté de vision multimodale et de contrôle du DOM pour naviguer sur des applications web dynamiques, cliquer, remplir des formulaires ou paginer.

---

## 2. LE NAVIGATEUR AUTONOME (`navigation_engine_tool.py`)

### 2.1 Lecture Sémantique & Archivage Direct d'une URL
- `analyze_web_page(url, query)` (**À PRIVILÉGIER sur une URL connue**) :
  - Ouvre `url`, extrait l'intégralité du texte de la page et délègue son analyse à un sous-agent (`MODEL_APOLLO`) pour répondre précisément à la directive `query` sans saturer la Mémoire Vectorielle de la session.
- `archive_web_page(url)` :
  - Extrait le texte intégral d'une page web volumineuse, l'enregistre en Markdown et l'indexe dans la **Mémoire Vectorielle de la session** (interrogeable via `search_sessions_context`).

### 2.2 Navigation Interactive Multi-Étapes (`delegate_web_browsing`)
- `task_objective` : Mission complète confiée au Navigateur Autonome (DOIT inclure le contexte général et spatio-temporel utile).
- `start_url` : URL initiale optionnelle.
- `max_iterations` : Budget d'actions (défaut `30`, max `100` pour les tâches longues). Si le plafond est atteint (`status="too_many_tries"`), analyser la synthèse intermédiaire retournée et relancer `delegate_web_browsing` pour poursuivre.
- **Administration du Navigateur Autonome** :
  - `get_browser_frames_history(depth=10)` : Consulte l'historique des dernières captures d'écran (`file_id`, `date`, `frame`).
  - `web_browse_reset()` : Réinitialise immédiatement la session active du Navigateur Autonome.
  - `close_web_thread(sub_sid)` : Ferme définitivement une session de navigation, réinitialise le Navigateur Autonome et purge son historique.

---

## 3. GÉOLOCALISATION & AFFICHAGE DE CARTES (`search_maps`)
- `search_maps(query, latitude=None, longitude=None, print_map=True)` :
  - Recherche des données géo-spatiales vérifiées (lieux, adresses, horaires, itinéraires, avis) et affiche une carte interactive (si `print_map=True`).
  - `latitude` / `longitude` : Coordonnées décimales optionnelles (issues de `<artifact id="AEC_localisation">`) pour centrer la recherche.
