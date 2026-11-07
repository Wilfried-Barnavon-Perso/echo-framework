---
domain_id: visual_and_system_metrics
version: 5.233.0.1
updated_at: 2026-11-03
name: Moteur de Rendu Visuel Interactif (ECHO Visual), Génération d'Images & Jauge de Contexte
description: Génération et retouche d'images (generate_image, close_image_session), visualisations et schémas interactifs (generate_rich_visualization : 17 moteurs 2D/3D) et surveillance de la charge du contexte (get_context_load).
tools: [generate_rich_visualization, generate_image, close_image_session, get_context_load]
keywords: [image, photo, illustration, dessin, generer_image, generate_image, retouche, aspect_ratio, close_image_session, dlg_img, generated, visualisation, schema, 3d, graphique, echarts, mermaid, mindmap, markmap, leaflet, timeline, gantt, cytoscape, science, bio, chem, aframe, contexte, tokens, get_context_load]
---

# MANUEL OPÉRATIONNEL : MOTEUR VISUEL, IMAGERIE & JAUGE DE CONTEXTE (`visual_and_system_metrics`)

## 1. VISUALISATIONS ET SCHÉMAS INTERACTIFS (`generate_rich_visualization`)
- **Contrainte d'environnement** : Réservé au Modèle principal (**interdit depuis un sous-agent headless**). Le rendu interactif est automatiquement déployé dans l'Iframe.
- **Spécifications des Paramètres** :
  - `intention` (`str`, requis) : Objectif précis du rendu visuel attendu (ex: `"Comparer l'évolution trimestrielle du CA et de la marge sous forme de graphique mixte barres/lignes"`).
  - `donnees_contextuelles` (`str`, requis) : **Intégralité des données métier brutes** (chiffres, séries, nœuds, relations, coordonnées, chronologie, code PDB/CID ou SMILES) nécessaires à la construction du visuel. Le générateur interne n'ayant pas accès à l'historique de la conversation, aucune donnée ne doit être sous-entendue. Ne pas y écrire le code technique du moteur (SVG/HTML/JSON) : fournir uniquement les données et relations structurées.
  - `moteur` (`Optional[str]`, défaut `None` — **laisser `None` en cas d'hésitation** pour déléguer le choix optimal, ou cibler l'un des 17 moteurs selon le besoin) :
    - **Biologie & Chimie Moléculaire** :
      - `"bio"` : Structure macromoléculaire 3D officielle (fournir le code RCSB PDB à 4 caractères ex: `1BNA`, `4HHB` ou PubChem `CID:2519` dans `donnees_contextuelles`).
      - `"chem"` : Formule chimique topologique 2D plane (fournir la chaîne SMILES dans `donnees_contextuelles`).
    - **Mathématiques, Sciences & Scènes 3D** :
      - `"science"` : Graphiques scientifiques, courbes paramétriques 2D/3D, surfaces 3D et distributions.
      - `"echarts"` : Tableaux de bord statistiques 2D (barres, lignes, radar, sankey, jauge) et graphiques 3D.
      - `"aframe"` : Scène géométrique 3D spatiale interactive (volumes, maquettes et primitives 3D dans l'espace).
      - `"astro"` : Carte de la voûte céleste et constellations.
    - **Diagrammes, Réseaux, Temps & Cartes** :
      - `"mermaid"` (organigrammes, séquences, états, classes, ER), `"sketch"` (diagramme style croquis à main levée), `"markmap"` (carte mentale hiérarchique), `"bpmn"` (processus BPMN 2.0), `"gantt"` (planning Gantt), `"cytoscape"` (graphe de réseau relationnel), `"timeline"` (frise chronologique), `"vega"` (graphique déclaratif Vega-Lite), `"wavedrom"` (chronogramme de signaux numériques), `"leaflet"` (carte géographique avec marqueurs GPS), `"svg"` (schéma ou illustration vectorielle 2D sur mesure).

---

## 2. GÉNÉRATION ET RETOUCHE D'IMAGES (`generate_image`, `close_image_session`)
- **Contrainte d'environnement** : Outils strictement réservés au Modèle principal (**interdits depuis un sous-agent headless**). Les images produites sont nommées automatiquement en `< 15` caractères (`<short_name>.png`), versionnées sous Git à chaque essai/retouche dans le workspace `generated` du Codex (`generated/<short_name>.png`, `resource_type="generated"`), et affichées à L'Utilisateur dans l'Iframe.
- **Spécifications des Paramètres de `generate_image`** :
  - `prompt` (`str`, requis) : Description exhaustive de l'image à produire (sujet, cadrage, style artistique, éclairage, palette, texte éventuel) ou instructions chirurgicales de correction/retouche lors de la reprise d'un `sub_sid`. Rédiger selon le protocole **PAS** (haut SNR, factuel, impératif).
  - `aspect_ratio` (`Literal["1:1", "3:4", "4:3", "9:16", "16:9"]`, défaut `"1:1"`) : Ratio géométrique cible (`"1:1"` carré, `"4:3"` / `"16:9"` paysage, `"3:4"` / `"9:16"` portrait).
  - `input_images` (`Optional[List[str]]`, défaut `None`) : Cible(s) optionnelle(s) d'une ou plusieurs images de référence (fusion de visuels, composition de sujets/décors, transfert de style, retouche multi-sources) : IDs Registre (`DOC_...`, `U_...`, `dlg_img_...`), chemins/noms dans le Codex ou l'Espace Personnel (`main/...`, `generated/...`, `sandbox/...`, `files/...`), ou URLs publiques (`http(s)://`). Accepte une image unique (ex: `["main/perso.png"]`) ou plusieurs images (ex: `["main/perso.png", "files/decor.jpg", "generated/style.png"]`).
    - **Règle d'Isolation Base64** : Le Modèle NE DOIT JAMAIS lire ni encoder lui-même l'image en Base64 ; `generate_image` charge directement la cible en interne.
  - `sub_sid` (`Optional[str]`, défaut `None`) :
    - **Création initiale** : Omettre (`None`) pour ouvrir une nouvelle session `dlg_img_xxxxxxxx`.
    - **Retouche itérative** : Fournir obligatoirement le `sub_sid` retourné au tour précédent pour poursuivre dans le même fil multimodal (chaque retouche est commitée comme une nouvelle révision Git du même fichier dans `generated/`).
- **Protocole Impératif de Restitution et Cycle de Vie Session (`close_image_session`)** :
  1. **Interdiction de Redondance Markdown** : L'affichage étant assuré par l'Iframe, il est **ABSOLUMENT INTERDIT** pour Le Modèle d'émettre une balise Markdown `![...](...)` dans sa réponse. Le Modèle DOIT uniquement mentionner l'URN Codex navigable (`[nom_court.png](codex:current:generated/nom_court.png)`).
  2. **Vérification auprès de L'Utilisateur** : Le Modèle principal ne voyant pas l'image binaire, il **DOIT IMPÉRATIVEMENT** s'assurer auprès de L'Utilisateur que l'image générée lui convient ou s'il souhaite la retoucher.
  3. **Fermeture et Relance de la Session (`close_image_session(sub_sid)`)** :
     - Si L'Utilisateur demande une modification ou retouche : rappeler `generate_image(prompt="...", sub_sid="dlg_img_...")` (avec `aspect_ratio` ou `input_images` si pertinent).
     - Si L'Utilisateur demande de recommencer depuis le début (*from scratch*) : appeler `close_image_session(sub_sid="dlg_img_...")` puis `generate_image(prompt="...", sub_sid=None)`.
     - Dès que L'Utilisateur confirme que l'image lui convient : appeler **`close_image_session(sub_sid="dlg_img_...")`** pour fermer définitivement le sous-agent et purger son historique.

---

## 3. SURVEILLANCE DE LA SATURATION DU CONTEXTE (`get_context_load`)
- Retourne un objet JSON contenant `context_load_percent`, `used_tokens` (`prompt`, `candidates`) et `status` (`"SAFE"`, `"WARNING"` ou `"CRITICAL"`).
- **Règle d'Escalade Cognitive** : Si `status` atteint `"CRITICAL"`, privilégier `MODEL_PROMETHEUS` et déléguer les opérations lourdes aux sous-agents (`delegate_to_agent`) pour préserver la fenêtre d'attention.
