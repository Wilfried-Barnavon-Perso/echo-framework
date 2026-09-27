# RÈGLES DE RÉDACTION DE LA DOCUMENTATION (DOCS HTML)

> **ATTENTION AGENTS** : Ce fichier dicte formellement les règles de rédaction, de structuration et de ségrégation de l'information pour l'intégralité du dossier `docs/` contenant la documentation HTML d'ECHO.

## 1. Ségrégation Radicale (Fonctionnel vs. Technique)

L'expérience de l'utilisateur final doit être absolument préservée de la complexité de l'ingénierie sous-jacente. L'objectif documentaire est "User-Friendly first".

### Le Tronc Utilisateur (La Valeur Fonctionnelle Pure & Zéro Survente)
- **Cible :** L'humain final.
- **Règle :** Le texte de la page HTML exclut toute justification technique ou jargon d'infrastructure (pas de WAF, pas de paquets réseau, pas de ports).
- **Focus :** Le texte se concentre *uniquement* sur l'usage et les bénéfices couverts (le "Pourquoi" et "Pour Quoi").
- **Ton :** Factuel et "Wikipédia technique". **Zéro survente.** (Ex: ne jamais utiliser d'adjectifs marketing comme "fluide et instantanée", préférer "avec fluidité").

### L'Encart Ingénieur Unique (Le "Comment")
- **Cible :** L'ingénieur système / Le développeur.
- **Règle :** Chaque page ne doit contenir qu'un **SEUL ET UNIQUE encart technique global** (balise `<details class="tech-spec">` située à la fin de la page ou de manière non obstructive).
- **Contenu :** Toute spécification pure, code, table de routage IPAM, mécanismes UCTP, dépendances Docker, ou schémas SQL y est impitoyablement reléguée. *Il est formellement interdit d'éparpiller des `<details>` techniques au milieu des paragraphes de la page.*

## 2. Pont Pédagogique (Les Schémas)

- **Règle HLD/LLD :** Les diagrammes Mermaid de haut niveau (HLD) ou de bas niveau (LLD) *ne sont pas* obligatoirement relégués dans l'Encart Ingénieur. 
- **Pertinence :** S'ils aident l'utilisateur à se représenter visuellement un concept abstrait (ex: l'isolation pour la sécurité de ses données), ils restent affichés dans le tronc principal. Ils servent de pont pédagogique entre l'Utilisateur et l'Ingénieur.

## 3. Loi Fondamentale (La Single Source of Truth)

- **Principe d'Exactitude :** L'intégralité des descriptions fonctionnelles ou techniques doit être validée par le code source (SSOT). Toute affirmation théorique non prouvée par le code (scripts, yaml, python) est invalidée et doit être purgée.
- **Évolution :** L'alignement de la documentation s'effectue via des tâches asynchrones pilotées par l'IA (Sous-Agents Antigravity) lisant le code brut croisé avec la mémoire fractale (`AGENTS.md` locaux).
