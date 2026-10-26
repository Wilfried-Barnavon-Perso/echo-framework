"""
title: ECHO Tool Guides Manager
author: Wilfried BARNAVON
version: 5.229.0.1
description: Gestionnaire de la base de connaissances opérationnelle des outils ECHO (tool_guides/*.md).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.229.0.1: Intégration de version et updated_at dans le parsing frontmatter, la Carte Hiérarchique et les balises XML.
# 5.229.0.0: Création initiale de EchoToolGuidesManager (indexation YAML, tool->domain et pré-injection).

import os
import re
from typing import Dict, List, Optional, Set


class EchoToolGuidesManager:
    """
    Gère le chargement, l'indexation hiérarchique (Domaine -> Outils -> Guide)
    et la résolution déterministe des manuels opérationnels situés dans
    `14-owui-libs/tool_guides/*.md`.
    """

    _GUIDES_CACHE: Optional[Dict[str, Dict]] = None
    _TOOL_TO_DOMAIN: Optional[Dict[str, str]] = None

    @classmethod
    def get_guides_dir(cls) -> str:
        """Retourne le chemin absolu du dossier `tool_guides`."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_guides")

    @staticmethod
    def _parse_frontmatter(raw_content: str) -> Dict:
        """
        Extrait le frontmatter YAML standardisé (`domain_id`, `name`, `description`,
        `tools`, `keywords`) et le corps Markdown sans dépendance externe.
        """
        meta: Dict = {
            "domain_id": "",
            "name": "",
            "version": "",
            "updated_at": "",
            "description": "",
            "tools": [],
            "keywords": [],
            "body": raw_content.strip(),
        }
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw_content, re.DOTALL)
        if not match:
            return meta

        yaml_block, body = match.group(1), match.group(2).strip()
        meta["body"] = body

        for line in yaml_block.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()

            if key in ("tools", "keywords"):
                # Parse [item1, item2, ...]
                inner = val.strip("[]")
                items = [
                    item.strip().strip("'\"")
                    for item in inner.split(",")
                    if item.strip()
                ]
                meta[key] = items
            else:
                meta[key] = val.strip("'\"")

        return meta

    @classmethod
    def load_all_guides(cls, force_reload: bool = False) -> Dict[str, Dict]:
        """
        Scanne `tool_guides/*.md`, construit le catalogue par `domain_id`
        et l'index inversé `tool_name -> domain_id`.
        """
        if cls._GUIDES_CACHE is not None and not force_reload:
            return cls._GUIDES_CACHE

        guides_dir = cls.get_guides_dir()
        guides: Dict[str, Dict] = {}
        tool_to_domain: Dict[str, str] = {}

        if os.path.isdir(guides_dir):
            for fname in sorted(os.listdir(guides_dir)):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(guides_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        raw = f.read()
                    parsed = cls._parse_frontmatter(raw)
                    domain_id = parsed.get("domain_id") or os.path.splitext(fname)[0]
                    parsed["domain_id"] = domain_id
                    guides[domain_id] = parsed
                    for t_name in parsed.get("tools", []):
                        tool_to_domain[t_name] = domain_id
                except Exception:
                    continue

        cls._GUIDES_CACHE = guides
        cls._TOOL_TO_DOMAIN = tool_to_domain
        return guides

    @classmethod
    def get_tool_to_domain_map(cls) -> Dict[str, str]:
        """Retourne l'index inversé `tool_name -> domain_id`."""
        cls.load_all_guides()
        return cls._TOOL_TO_DOMAIN or {}

    @classmethod
    def get_hierarchical_index(cls) -> str:
        """
        Construit la Carte Hiérarchique complète (`Domaine -> Description -> Outils`)
        injectée dans le prompt système du Conseiller Outils.
        """
        guides = cls.load_all_guides()
        if not guides:
            return "Aucun manuel opérationnel disponible."

        lines: List[str] = []
        for domain_id, info in guides.items():
            tools_str = ", ".join(info.get("tools", []))
            ver_str = f" [v{info.get('version', '')} - {info.get('updated_at', '')}]" if info.get("version") or info.get("updated_at") else ""
            lines.append(
                f"- DOMAINE `{domain_id}` ({info.get('name', domain_id)}){ver_str} :\n"
                f"  Description : {info.get('description', '')}\n"
                f"  Outils couverts : {tools_str}"
            )
        return "\n".join(lines)

    @classmethod
    def get_guide_by_target(cls, target: str) -> Optional[Dict]:
        """
        Résout un manuel à partir d'un `domain_id` exact OU d'un `tool_name` exact.
        """
        if not target:
            return None
        guides = cls.load_all_guides()
        tool_map = cls.get_tool_to_domain_map()
        clean = target.strip().strip("`'\"")

        if clean in guides:
            return guides[clean]
        if clean in tool_map:
            return guides.get(tool_map[clean])
        return None

    @classmethod
    def resolve_guides(
        cls,
        objective: str = "",
        target_tools: Optional[str] = None,
        error_context: Optional[str] = None,
        max_preloaded: int = 4,
    ) -> str:
        """
        Résout déterministement les manuels pertinents à pré-injecter dans le prompt
        du Conseiller Outils en croisant :
        1. Les noms d'outils ou de domaines explicitement cités dans `target_tools`.
        2. Les noms d'outils ou de domaines cités dans `objective` ou `error_context`.
        3. Les `keywords` du Frontmatter YAML détectés dans `objective` ou `error_context`.
        """
        guides = cls.load_all_guides()
        tool_map = cls.get_tool_to_domain_map()
        if not guides:
            return "Aucun manuel pré-injecté."

        matched_domains: List[str] = []
        seen: Set[str] = set()

        if not isinstance(objective, str):
            objective = ""
        if not isinstance(target_tools, str):
            target_tools = None
        if not isinstance(error_context, str):
            error_context = None

        def _add_domain(d_id: str) -> None:
            if d_id in guides and d_id not in seen:
                seen.add(d_id)
                matched_domains.append(d_id)

        # 1. Cibles explicites (`target_tools`)
        if target_tools:
            tokens = [
                t.strip().strip("`'\"")
                for t in re.split(r"[,;\s]+", target_tools)
                if t.strip()
            ]
            for tok in tokens:
                if tok in guides:
                    _add_domain(tok)
                elif tok in tool_map:
                    _add_domain(tool_map[tok])

        combined_text = f"{objective or ''}\n{error_context or ''}".lower()

        # 2. Détection exacte de noms de fonctions ou domain_id dans le texte
        for d_id in guides:
            if d_id.lower() in combined_text:
                _add_domain(d_id)
        for t_name, d_id in tool_map.items():
            if t_name.lower() in combined_text:
                _add_domain(d_id)

        # 3. Scoring par mots-clés (keywords YAML) si on a encore de la place
        if len(matched_domains) < max_preloaded:
            scored: List[tuple] = []
            for d_id, info in guides.items():
                if d_id in seen:
                    continue
                score = 0
                for kw in info.get("keywords", []):
                    kw_low = kw.lower()
                    if kw_low and kw_low in combined_text:
                        score += 1
                if score > 0:
                    scored.append((score, d_id))
            scored.sort(key=lambda x: (-x[0], x[1]))
            for _, d_id in scored:
                if len(matched_domains) >= max_preloaded:
                    break
                _add_domain(d_id)

        if not matched_domains:
            return (
                "Aucun manuel pré-injecté automatiquement. "
                "Consultez `<hierarchical_domains_map>` et utilisez `read_tool_guide(targets=[...])` "
                "si les détails opérationnels d'un domaine sont requis."
            )

        blocks: List[str] = []
        for d_id in matched_domains[:max_preloaded]:
            info = guides[d_id]
            ver_attr = f' version="{info.get("version", "")}"' if info.get("version") else ""
            date_attr = f' updated_at="{info.get("updated_at", "")}"' if info.get("updated_at") else ""
            blocks.append(
                f'<operational_manual domain_id="{d_id}" name="{info.get("name", d_id)}"{ver_attr}{date_attr}>\n'
                f"{info.get('body', '')}\n"
                f"</operational_manual>"
            )
        return "\n\n".join(blocks)

    @classmethod
    def format_guides_for_targets(cls, targets: List[str]) -> str:
        """
        Exécute la lecture à la demande (`read_tool_guide`) pour une liste de `domain_id`
        ou `tool_name`.
        """
        guides = cls.load_all_guides()
        matched: List[str] = []
        seen: Set[str] = set()

        for raw_t in targets or []:
            info = cls.get_guide_by_target(str(raw_t))
            if info:
                d_id = info["domain_id"]
                if d_id not in seen:
                    seen.add(d_id)
                    ver_attr = f' version="{info.get("version", "")}"' if info.get("version") else ""
                    date_attr = f' updated_at="{info.get("updated_at", "")}"' if info.get("updated_at") else ""
                    matched.append(
                        f'<operational_manual domain_id="{d_id}" name="{info.get("name", d_id)}"{ver_attr}{date_attr}>\n'
                        f"{info.get('body', '')}\n"
                        f"</operational_manual>"
                    )

        if not matched:
            valid_domains = ", ".join(sorted(guides.keys()))
            return (
                f"Aucun manuel trouvé pour {targets}. "
                f"Domaines valides : {valid_domains}"
            )
        return "\n\n".join(matched)
