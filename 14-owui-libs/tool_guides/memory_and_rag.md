---
domain_id: memory_and_rag
version: 5.239.14.2
updated_at: 2026-11-14
name: Mémoire Vectorielle Long-Terme (Méta-Artéfacts) & Mémoire Vectorielle de Session
description: Gouvernance de la mémoire persistante inter-sessions (PACP/PRAC) et de la Mémoire Vectorielle de la session (documents ingérés et historique conversationnel).
tools: [update_meta_artifact, search_meta_artifacts, delete_meta_artifact_item, save_session_context, delete_session_context_source, search_sessions_context]
keywords: [mémoire, rag, meta_artifact, pacp, prac, profil_alignement, hypotheses_apprentissage, smart_context, session, souvenir, importance, certainty_level, cosine_threshold, inventory, chronological]
---

# MANUEL OPÉRATIONNEL : MÉMOIRE VECTORIELLE LONG-TERME & DE SESSION (`memory_and_rag`)

## 1. ARCHITECTURE & MATRICE DE DÉCISION
Le système mémoriel d'ECHO est scindé en deux espaces vectoriels isolés :
1. **Méta-Artéfacts Long-Terme (Inter-Sessions)** : Stockent les invariants cognitifs de l'Utilisateur (`PACP`) et les heuristiques d'apprentissage du Modèle (`PRAC`).
   - Création / Mise à jour : `update_meta_artifact`
   - Recherche sémantique ou Lecture globale : `search_meta_artifacts`
   - Suppression chirurgicale : `delete_meta_artifact_item`
2. **Mémoire Vectorielle de la Session (Intra/Inter-Sessions)** : Stocke les documents ingérés (`AEC_smart_context`), les captures d'outils et les échanges conversationnels vectorisés.
   - Inventaire des sources, Lecture chronologique ou Recherche sémantique : `search_sessions_context`
   - Sauvegarde explicite d'un fait/extrait dans la session : `save_session_context`
   - Purge d'une source de la session : `delete_session_context_source`

---

## 2. PROTOCOLE DES MÉTA-ARTÉFACTS LONG-TERME (`PACP` / `PRAC`)

### 2.1 Taxonomie Stricte des Catégories (`artifact_name`)
Le paramètre `artifact_name` n'accepte que deux valeurs littérales :
- `"Profil d'Alignement"` (**PACP**) : Préférences explicites ou inférées de l'Utilisateur, style cognitif, contraintes métier récurrentes, stack technique personnelle, attentes de formatage.
- `"Hypothèses d'Apprentissage"` (**PRAC**) : Rétrospectives opérationnelles du Modèle, stratégies d'outils optimales, contournements d'erreurs découverts, patterns validés.

### 2.2 Échelle Déterministe de Pondération (`importance`)
Dans `update_meta_artifact(artifact_name, fact, importance)`, le paramètre `importance` (`Literal["1", "2", "3", "4", "5"]`) est **REQUIS** :
- `"1"` (**Trivial**) : 1 occurrence isolée observée, indice faible non confirmé.
- `"2"` (**Mineur**) : 2 occurrences concordantes rapprochées, préférence secondaire.
- `"3"` (**Utile**) : 3 occurrences concordantes, fait vérifié ou habitude opérationnelle établie.
- `"4"` (**Majeur**) : 3 occurrences concordantes structurantes, préférence forte ou architecture clé.
- `"5"` (**Axiome**) : Injonction explicite de l'Utilisateur ou invariant stratégique critique validé.

### 2.3 Spécifications des Fonctions Méta-Artéfacts
- `update_meta_artifact` :
  - `fact` : Énoncé factuel, dense et autonome (sans pronoms ambigus ni références temporelles relatives).
  - **Retour** : Génère et retourne le `memory_id` unique (indispensable pour suppression ultérieure).
- `search_meta_artifacts` :
  - `artifact_name` : `"Profil d'Alignement"`, `"Hypothèses d'Apprentissage"`, ou `None` (les deux simultanément).
  - **Mode Recherche Ciblée (`query` renseigné)** : Extraction sémantique pondérée par niveau d'importance.
  - **Mode Lecture Globale (`query=None` ou omis)** : Extraction chronologique des `limit` derniers faits mémorisés avec leurs `memory_id` (**REQUIS** avant suppression).
  - `start_date` / `end_date` : Bornes temporelles optionnelles au format ISO 8601 (`"YYYY-MM-DD"`).
- `delete_meta_artifact_item` :
  - Supprime définitivement un souvenir à partir de son `memory_id` exact (obtenu via `update_meta_artifact` ou `search_meta_artifacts`).

---

## 3. PROTOCOLE DE LA MÉMOIRE VECTORIELLE DE SESSION (`search_sessions_context`)

### 3.1 Exploitation des Documents Ingérés (`AEC_smart_context`)
Lorsqu'un document est ingéré, la balise `<artifact id="AEC_smart_context">` fournit son `source_id` et son résumé exécutif.
- Le Modèle **NE DOIT PAS** interroger la Mémoire Vectorielle de la session si le résumé du `AEC_smart_context` suffit.
- Si une précision granulaire est requise, appeler `search_sessions_context` en filtrant sur `source_id`.

### 3.2 Spécifications de `search_sessions_context`
- `mode` (**Seul paramètre obligatoire** — `Literal["inventory", "chronological", "semantic"]`) :
  - `"inventory"` : Liste tous les `source_id` indexés (`"conversation_history"`, `file_id` de documents joints, notes enregistrées), leur nombre exact de fragments, la plage temporelle ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`) et un aperçu des bornes. Le Modèle **DOIT** invoquer ce mode en amont pour identifier les `source_id` disponibles et l'étendue temporelle d'une session. `query`, `sort_order`, `cosine_threshold` et `certainty_level != "STANDARD"` sont **ABSOLUMENT INTERDITS**.
  - `"chronological"` : Lit les fragments complets dans l'ordre temporel (`sort_order="newest_first"` par défaut, ou `"oldest_first"` pour remonter au début d'une session). **REQUIS** pour toute exploration temporelle ou question ouverte sans terme sémantique discriminant. `query`, `sort_order="cosine_desc"`, `cosine_threshold` et `certainty_level != "STANDARD"` sont **ABSOLUMENT INTERDITS**.
  - `"semantic"` : Recherche vectorielle par proximité cosinus. `query` est **REQUIS** (horodatage ISO 8601 restitué sur chaque extrait).
- `certainty_level` & `cosine_threshold` (**Uniquement en `mode="semantic"`**) :
  - Paliers pré-calibrés (`cosine_threshold` **DOIT** rester `None`) : `"BROAD"` (`0.34`), `"STANDARD"` (`0.52`, défaut), `"STRICT"` (`0.63`), `"EXACT"` (`0.81`).
  - Seuil cosinus manuel : le Modèle **DOIT** définir simultanément `certainty_level="COSINE"` **ET** `cosine_threshold` dans `[0.0, 1.0]` (**REQUIS** ; toute combinaison partielle est **ABSOLUMENT INTERDITE** et retourne une erreur).
- `sort_order` (`Optional[Literal["cosine_desc", "newest_first", "oldest_first"]] = None`) :
  - Si `None` : `"cosine_desc"` (score cosinus décroissant) en `semantic`, `"newest_first"` en `chronological`.
- `source_id` (`Optional[str] = None`) : Filtre sur une source précise obtenue via `mode="inventory"`, `<artifact id="AEC_smart_context" source_id="...">` ou `save_session_context`.
- `start_date` / `end_date` (`Optional[str] = None`) : Bornes temporelles inclusives au format ISO `"YYYY-MM-DD"`.
- `max_fragments` (`int = 20`) : Nombre maximal de fragments retournés en `chronological` et `semantic` (ajustable selon le volume annoncé par `inventory`).
- `tag` :
  - Restreint la recherche aux conversations rattachées à cette étiquette.
  - **Prérequis obligatoire** : Appeler préalablement `list_ui_tags(global_scope=True)` pour identifier la chaîne anglaise exacte (`snake_case` / `kebab-case`).
- `global_search` :
  - `False` (Défaut) : Restreint la recherche à la session active avec filtrage automatique sur les messages de la **branche active** (`build_branch_rag_filter`). Les messages de branches mortes ou alternatives sont exclus de la restitution textuelle.
  - `True` : Étend la recherche à **l'intégralité de l'historique inter-sessions** de l'Utilisateur (**DOIT** être activé dès que l'Utilisateur mentionne un échange antérieur).

### 3.3 Persistance et Purge de Sources de Session
- `save_session_context(text, source_id)` : Remplace tout vecteur existant sous `source_id` puis vectorise `text` dans la session active.
- `delete_session_context_source(source_id)` : Purge tous les vecteurs associés à `source_id` dans la session active (un sous-agent ne peut purger que les sources qu'il a lui-même créées).
