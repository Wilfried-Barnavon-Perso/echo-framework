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
        """Génère la balise XML <AEC_evenement_systeme> formattée en XML natif."""
        if not sys_events and not error_events:
            return ""
            
        events_text = "<AEC_evenement_systeme>\n"
        if sys_events:
            # Tri chronologique (FIFO strict)
            sys_events.sort(key=lambda x: x.get('date', ''))
            events_text += "  <nouveaux_evenements>\n"
            for evt in sys_events:
                events_text += f"    <evenement type=\"{evt.get('type', 'inconnu')}\" source=\"{evt.get('source', 'utilisateur')}\">\n"
                events_text += f"      <nom>{evt.get('name', '')}</nom>\n"
                events_text += f"      <mime>{evt.get('mime', '')}</mime>\n"
                events_text += f"      <date>{evt.get('date', '')}</date>\n"
                if evt.get('source_id'):
                    events_text += f"      <source_id>{evt.get('source_id')}</source_id>\n"
                events_text += "    </evenement>\n"
            events_text += "  </nouveaux_evenements>\n"
            events_text += "  <!-- [GUARDRAIL FIFO] : Liste des modifications récentes du tour courant. Pour l'état complet, utilisez query_registry. -->\n"
            
        if error_events:
            events_text += "  <erreurs_ingestion>\n"
            for err in error_events:
                events_text += f"    <erreur fichier=\"{err.get('name', '')}\">{err.get('error', '')}</erreur>\n"
            events_text += "  </erreurs_ingestion>\n"
            
        events_text += "</AEC_evenement_systeme>\n\n"
        return events_text

    @staticmethod
    def render_identity_context(name: str) -> str:
        """Génère la balise XML <AEC_identite>."""
        return f"<AEC_identite>\n  <utilisateur>{name}</utilisateur>\n</AEC_identite>\n\n"
        
    @staticmethod
    def render_location_context(location: str) -> str:
        """Génère la balise XML <AEC_localisation>."""
        return f"<AEC_localisation>\n  <coordonnees>{location}</coordonnees>\n</AEC_localisation>\n\n"

    @staticmethod
    def render_time_context(date_heure: str, timezone: str, tour: int) -> str:
        """Génère la balise XML <AEC_temporalite>."""
        return (
            f"<AEC_temporalite>\n"
            f"  <horodatage fuseau=\"{timezone}\">{date_heure}</horodatage>\n"
            f"  <tour_conversation>{tour}</tour_conversation>\n"
            f"</AEC_temporalite>\n\n"
        )

    @staticmethod
    def render_model_context(model_id: str, model_origin: str, version: str) -> str:
        """Génère la balise XML <AEC_modele>."""
        return (
            f"<AEC_modele>\n"
            f"  <modele_actuel>{model_id}</modele_actuel>\n"
            f"  <modele_origine>{model_origin}</modele_origine>\n"
            f"  <framework_version>{version}</framework_version>\n"
            f"</AEC_modele>\n\n"
        )

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
