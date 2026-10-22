---
domain_id: memory_and_rag
version: 5.229.0.0
updated_at: 2026-10-22
name: Mémoire Vectorielle Long-Terme (Méta-Artéfacts) & Mémoire Vectorielle de Session
description: Gouvernance de la mémoire persistante inter-sessions (PACP/PRAC) et de la Mémoire Vectorielle de la session (documents ingérés et historique conversationnel).
tools: [update_meta_artifact, search_meta_artifacts, delete_meta_artifact_item, save_session_context, delete_session_context_source, search_sessions_context]
keywords: [mémoire, rag, meta_artifact, pacp, prac, profil_alignement, hypotheses_apprentissage, smart_context, session, souvenir, importance, certainty_level]
---

# MANUEL OPÉRATIONNEL : MÉMOIRE VECTORIELLE LONG-TERME & DE SESSION (`memory_and_rag`)

## 1. ARCHITECTURE & MATRICE DE DÉCISION
Le système mémoriel d'ECHO est scindé en deux espaces vectoriels strictement isolés :
1. **Méta-Artéfacts Long-Terme (Inter-Sessions)** : Gouvernés par les principes `PACP` et `PRAC` du Kernel. Stockent les invariants cognitifs de l'Utilisateur et les heuristiques d'apprentissage du Modèle.
   - Création / Mise à jour : `update_meta_artifact`
   - Recherche sémantique ou Lecture globale : `search_meta_artifacts`
   - Suppression chirurgicale : `delete_meta_artifact_item`
2. **Mémoire Vectorielle de la Session (Intra/Inter-Sessions)** : Stocke les documents ingérés (`AEC_smart_context`), les captures d'outils et les échanges conversationnels vectorisés.
   - Exploration sémantique ou Cartographie d'index : `search_sessions_context`
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
- `"1"` (**Trivial — Pondération 0.6x, TTL 30j**) : 1 occurrence isolée observée, indice faible non confirmé (éligible à la consolidation nocturne Lvl1 ➔ Lvl2).
- `"2"` (**Mineur — Pondération 0.8x, TTL 30j**) : 2 occurrences concordantes rapprochées, préférence secondaire ou synthèse consolidée.
- `"3"` (**Utile — Pondération 1.0x, TTL 30j**) : 3 occurrences concordantes, fait vérifié ou habitude opérationnelle établie.
- `"4"` (**Majeur — Pondération 1.3x, Permanent hors TTL**) : 3 occurrences concordantes structurantes, préférence forte ou architecture clé.
- `"5"` (**Axiome — Pondération 1.6x, Sanctuarisé hors TTL**) : Injonction explicite de l'Utilisateur ou invariant stratégique critique validé.

### 2.3 Spécifications des Fonctions Méta-Artéfacts
- `update_meta_artifact(artifact_name, fact, importance)` :
  - `fact` : Énoncé factuel, dense et autonome (sans pronoms ambigus ni références temporelles relatives).
  - **Retour** : Génère et retourne le `memory_id` unique (ex: `pref_python_typing`), indispensable pour toute suppression ultérieure.
- `search_meta_artifacts(query=None, artifact_name=None, limit=20, start_date="", end_date="")` :
  - `artifact_name` : `"Profil d'Alignement"`, `"Hypothèses d'Apprentissage"`, ou `None` (pour interroger les deux Méta-Artéfacts simultanément).
  - **Mode Recherche Ciblée (`query` renseigné)** : Extraction sémantique avec reranking pondéré par le niveau d'importance (`score * weight`).
  - **Mode Lecture Globale (`query=None` ou omis)** : Extraction chronologique neutre des `limit` derniers faits mémorisés avec leurs `memory_id`. **REQUIS** lors de `!status` ou avant suppression.
  - `start_date` / `end_date` : Bornes temporelles optionnelles au format ISO 8601 (`"YYYY-MM-DD"`).
- `delete_meta_artifact_item(memory_id)` :
  - Supprime définitivement un souvenir à partir de son `memory_id` exact (obtenu via `update_meta_artifact` ou `search_meta_artifacts`).

---

## 3. PROTOCOLE DE LA MÉMOIRE VECTORIELLE DE SESSION (`search_sessions_context`)

### 3.1 Exploitation des Documents Ingérés (`AEC_smart_context`)
Lorsqu'un document est ingéré, la balise `<artifact id="AEC_smart_context">` fournit son `source_id` et son résumé exécutif.
- Le Modèle **NE DOIT PAS** interroger la Mémoire Vectorielle de la session si le résumé du `AEC_smart_context` suffit.
- Si une précision granulaire est requise, appeler `search_sessions_context` en filtrant sur `source_id`. En phase prospective complexe, privilégier la délégation de cette exploration à un Sous-Agent pour distiller les résultats sans alourdir le contexte principal.

### 3.2 Spécifications de `search_sessions_context`
`search_sessions_context(certainty_level, query=None, source_id="", global_search=False, limit=32, sort_by_time=False, start_date="", end_date="")` :
- `certainty_level` (**Obligatoire**) : Niveau de certitude sémantique attendu :
  - `"BROAD"` (seuil 0.40) : Exploration large, concepts vagues ou reformulés.
  - `"STANDARD"` (seuil 0.55) : Équilibre nominal pertinence/rappel.
  - `"STRICT"` (seuil 0.70) : Recherche factuelle à haute correspondance sémantique.
  - `"EXACT"` (seuil 0.85) : Recherche quasi-littérale d'un terme ou identifiant.
- `query` :
  - **Défini (`query="..."`)** : Recherche et extrait les fragments textuels complets dédupliqués.
  - **Omis (`query=None`)** : **Mode Cartographie d'Index** — retourne la liste des `source_id` indexés dans la session avec horodatage, tags et aperçu de 60 caractères.
- `global_search` :
  - `False` (Défaut) : Restreint la recherche à la session de conversation active.
  - `True` : Étend la recherche à **l'intégralité de l'historique inter-sessions** de l'Utilisateur. **DOIT** être activé dès que l'Utilisateur fait référence à un échange antérieur (*"hier"*, *"la semaine dernière"*, *"dans une autre session"*).
- `sort_by_time` : Si `True`, trie les résultats du plus récent au plus ancien au lieu du score de pertinence.

### 3.3 Persistance et Purge de Sources de Session
- `save_session_context(text, source_id)` : Nettoie préventivement tout vecteur existant sous `source_id` puis vectorise `text` dans la Mémoire Vectorielle de la session active.
- `delete_session_context_source(source_id)` : Purge de manière irréversible tous les vecteurs associés à `source_id` dans la session active (un sous-agent ne peut purger que les sources qu'il a lui-même créées).
