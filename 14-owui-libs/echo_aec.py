# -*- coding: utf-8 -*-
"""
title: ECHO AEC Manager
author: Wilfried BARNAVON
version: 1.0
description: SSOT pour la gestion, le formatage et le cycle de vie de toutes les balises XML AEC.
"""

import time
from typing import List, Dict, Any

class EchoAEC:
    @staticmethod
    def _dict_to_yaml_aec(d: Any, indent: int = 0) -> str:
        """Helper local abstrait pour la sérialisation YAML de l'AEC."""
        lines = []
        space = "  " * indent
        if isinstance(d, list):
            for item in d:
                if isinstance(item, dict):
                    lines.append(f"{space}-")
                    lines.append(EchoAEC._dict_to_yaml_aec(item, indent + 1))
                else:
                    lines.append(f"{space}- {item}")
        elif isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict):
                    if not v: lines.append(f"{space}{k}: {{}}")
                    else:
                        lines.append(f"{space}{k}:")
                        lines.append(EchoAEC._dict_to_yaml_aec(v, indent + 1))
                elif isinstance(v, list):
                    if not v: lines.append(f"{space}{k}: []")
                    else:
                        lines.append(f"{space}{k}:")
                        for item in v:
                            if isinstance(item, dict):
                                lines.append(f"{space}  -")
                                lines.append(EchoAEC._dict_to_yaml_aec(item, indent + 2))
                            else:
                                lines.append(f"{space}  - {item}")
                else:
                    val = str(v).replace("\n", " ") if v is not None else ""
                    lines.append(f"{space}{k}: {val}")
        return "\n".join(lines)

    @staticmethod
    def record_event(state_manager: Any, event_name: str, status: str, summary: str = None, resource_type: str = "aec_event") -> str:
        """Transmission SQLite : Inscrit un événement abstrait (ex: Rappel Cognitif) dans le Registre Unifié."""
        event_id = f"aec_event_{int(time.time()*1000)}"
        state_manager.save_resource(
            id=event_id,
            name=event_name,
            resource_type=resource_type,
            status=status,
            summary=summary
        )
        return event_id

    @staticmethod
    def get_pending_events(state_manager: Any, last_check: float) -> List[Dict]:
        """Extraction SQLite : Récupère le delta des événements depuis le dernier contrôle."""
        return state_manager.get_resources(created_after=last_check)

    @staticmethod
    def render_system_events(sys_events: list = None, error_events: list = None) -> str:
        """Génère la balise XML <AEC_evenement_systeme> formattée en YAML."""
        if not sys_events and not error_events:
            return ""
        
        events_text = "<AEC_evenement_systeme>\n"
        if sys_events:
            events_text += EchoAEC._dict_to_yaml_aec(sys_events) + "\n"
            events_text += "> Utilisez `query_registry` pour consulter l'état complet des ressources.\n"
        if error_events:
            events_text += "\n[ERREURS D'INGESTION]\n" + EchoAEC._dict_to_yaml_aec(error_events) + "\n"
            events_text += "> Ces fichiers ont échoué et ne sont pas exploitables.\n"
        events_text += "</AEC_evenement_systeme>\n\n"
        
        return events_text

    @staticmethod
    def render_environment_context(env_snapshot: dict) -> str:
        """Génère la balise XML <AEC_environnement_contexte> formattée en YAML."""
        yaml_str = EchoAEC._dict_to_yaml_aec(env_snapshot)
        return f"<AEC_environnement_contexte>\n{yaml_str}\n</AEC_environnement_contexte>\n\n"

    @staticmethod
    def render_smart_context(filename: str, mime: str, mode: str, source_id: str, summary: str, sys_msg: str) -> str:
        """Génère la balise XML <AEC_smart_context> utilisée par le pipeline d'ingestion RAG."""
        return (
            f"<AEC_smart_context filename=\"{filename}\" mime_type=\"{mime}\" mode=\"{mode}\"\n"
            f"                source_id=\"{source_id}\">\n"
            f"{summary}\n\n"
            f"{sys_msg}\n"
            f"</AEC_smart_context>"
        )
