# -*- coding: utf-8 -*-
"""
title: ECHO AEC Manager
author: Wilfried BARNAVON
version: 2.1
description: Composant système interne : ECHO AEC Manager.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 2.1: Refonte totale de render_system_events pour supporter le XML natif et migration globale vers <artifact>.
# 2.0: Suppression de _dict_to_yaml_aec au profit d'un templating XML natif (5 AEC distincts) et implémentation d'un tri chronologique FIFO pour la queue d'évènements.

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
        """Génère la balise XML <artifact id="AEC_evenement_systeme"> formattée en XML natif."""
        if not sys_events and not error_events:
            return ""
            
        output = ""
        if sys_events:
            # Tri chronologique (FIFO strict)
            sys_events.sort(key=lambda x: x.get('date', ''))
            for evt in sys_events:
                msg = evt.get('message', '')
                if msg and msg.startswith("<artifact"):
                    output += f"{msg}\n"
                    # Notification spéciale pour l'ingestion asynchrone réussie
                    if 'id="AEC_smart_context"' in msg:
                        output += f'<artifact id="AEC_evenement_systeme" source="{evt.get("source", "outil/HUD")}">\n'
                        output += f"Nouveau fichier asynchrone ingéré : {evt.get('name', '')}. Le contexte est disponible.\n"
                        output += f'</artifact>\n'
                elif msg: # RAPPEL COGNITIF OU ERREUR
                    output += f'<artifact id="AEC_evenement_systeme" source="{evt.get("source", "Système")}">\n{msg}\n</artifact>\n'
                else: # FICHIERS SYNCHRONES UPLOADÉS
                    texte = f"Nouveau fichier attaché : {evt.get('name', '')} (MIME: {evt.get('mime', '')})"
                    if evt.get('source_id'):
                        texte += f", Source ID: {evt.get('source_id')}"
                    output += f'<artifact id="AEC_evenement_systeme" source="{evt.get("source", "utilisateur")}">\n{texte}\n</artifact>\n'
            
        if error_events:
            for err in error_events:
                texte = f"Erreur d'ingestion pour le fichier {err.get('name', '')}: {err.get('error', '')}"
                output += f'<artifact id="AEC_evenement_systeme" source="Système">\n{texte}\n</artifact>\n'
            
        return output.strip() + "\n\n" if output else ""

    @staticmethod
    def render_identity_context(name: str) -> str:
        """Génère la balise XML <artifact id="AEC_identite">."""
        return f'<artifact id="AEC_identite">\n  <utilisateur>{name}</utilisateur>\n</artifact>\n\n'
        
    @staticmethod
    def render_location_context(location: str) -> str:
        """Génère la balise XML <artifact id="AEC_localisation">."""
        return f'<artifact id="AEC_localisation">\n  <coordonnees>{location}</coordonnees>\n</artifact>\n\n'

    @staticmethod
    def render_time_context(date_heure: str, timezone: str, tour: int) -> str:
        """Génère la balise XML <artifact id="AEC_temporalite">."""
        return (
            f'<artifact id="AEC_temporalite">\n'
            f'  <horodatage fuseau="{timezone}">{date_heure}</horodatage>\n'
            f'  <tour_conversation>{tour}</tour_conversation>\n'
            f'</artifact>\n\n'
        )

    @staticmethod
    def render_model_context(model_id: str, model_origin: str, version: str) -> str:
        """Génère la balise XML <artifact id="AEC_modele">."""
        return (
            f'<artifact id="AEC_modele">\n'
            f'  <modele_actuel>{model_id}</modele_actuel>\n'
            f'  <modele_origine>{model_origin}</modele_origine>\n'
            f'  <framework_version>{version}</framework_version>\n'
            f'</artifact>\n\n'
        )

    @staticmethod
    def render_smart_context(filename: str, mime: str, mode: str, source_id: str, summary: str, sys_msg: str) -> str:
        """Génère la balise XML <artifact id="AEC_smart_context"> utilisée par le pipeline d'ingestion RAG."""
        return (
            f'<artifact id="AEC_smart_context" filename="{filename}" mime_type="{mime}" mode="{mode}" source_id="{source_id}">\n'
            f'{summary}\n\n{sys_msg}\n'
            f'</artifact>'
        )
