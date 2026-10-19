# -*- coding: utf-8 -*-
"""
title: ECHO AEC Manager
author: Wilfried BARNAVON
version: 5.227.1.1
description: Composant système interne : ECHO AEC Manager.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.227.1.1: Suppression de render_context_checkpoint suite à la suppression du compactage in-place.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 2.5: Ajout de render_context_checkpoint pour l'encapsulation XML déterministe de <artifact id="AEC_context_checkpoint">.
# 2.4: Ajout de render_directive pour le formatage XML natif des directives système ponctuelles (PIS et mise à niveau).
# 2.3: Ajout de drain_pending_directives (transaction atomique), purge_orphaned_events et status optionnel dans record_event.

import time
import sqlite3
from typing import List, Dict, Any


class EchoAEC:
    @staticmethod
    def record_event(state_manager: Any, event_name: str, summary: str = None, status: str = "active", resource_type: str = "aec_event") -> str:
        """Transmission SQLite : Inscrit un événement abstrait (ex: Directive de Cascade) dans le Registre Unifié."""
        event_id = f"aec_event_{int(time.time() * 1000)}"
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
    def drain_pending_directives(state_manager: Any) -> tuple[str, List[Dict]]:
        """
        Draine (extrait ET purge atomiquement dans une seule transaction) tous les événements
        'aec_directive' et 'aec_event' en attente dans la boîte aux lettres SQLite.
        Garantit une consommation unique avant scellement dans le Shadow.
        Retourne (xml_formate, liste_brute).
        """
        try:
            with state_manager._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                rows = cursor.execute(
                    "SELECT id, name, resource_type, summary, created_at "
                    "FROM echo_resources "
                    "WHERE resource_type IN ('aec_directive', 'aec_event') "
                    "ORDER BY created_at ASC"
                ).fetchall()

                if not rows:
                    return "", []

                output = ""
                raw_events = []
                ids_to_delete = []

                for r in rows:
                    evt_id = r["id"]
                    res_type = r["resource_type"]
                    msg = r["summary"] or ""
                    source = "Système"

                    if res_type == "aec_directive":
                        output += f'<artifact id="AEC_directive" source="{source}">\n{msg}\n</artifact>\n'
                    else:
                        output += f'<artifact id="AEC_evenement_systeme" source="{source}">\n{msg}\n</artifact>\n'

                    ids_to_delete.append(evt_id)
                    raw_events.append(dict(r))

                # Purge atomique dans la même transaction
                if ids_to_delete:
                    placeholders = ",".join("?" for _ in ids_to_delete)
                    cursor.execute(f"DELETE FROM echo_resources WHERE id IN ({placeholders})", ids_to_delete)
                    conn.commit()

                return output.strip(), raw_events
        except Exception as e:
            print(f"[EchoAEC] Erreur drain_pending_directives: {e}")
            return "", []

    @staticmethod
    def purge_orphaned_events(state_manager: Any, t_pivot: float) -> int:
        """
        Purge tout AEC dont created_at > t_pivot.
        Invoqué lors d'un rewind ou d'une retouche de message pour éliminer les événements d'une branche avortée.
        """
        try:
            with state_manager._get_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM echo_resources WHERE resource_type IN ('aec_event', 'aec_directive') AND created_at > ?",
                    (float(t_pivot),)
                )
                conn.commit()
                return cur.rowcount
        except Exception as e:
            print(f"[EchoAEC] Erreur purge_orphaned_events: {e}")
            return 0

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
                        output += '</artifact>\n'
                elif msg:  # RAPPEL COGNITIF OU ERREUR
                    if evt.get('resource_type') == 'aec_directive':
                        output += f'<artifact id="AEC_directive" source="{evt.get("source", "Système")}">\n{msg}\n</artifact>\n'
                    else:
                        output += f'<artifact id="AEC_evenement_systeme" source="{evt.get("source", "Système")}">\n{msg}\n</artifact>\n'
                else:  # FICHIERS SYNCHRONES UPLOADÉS
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
    def render_directive(message: str, source: str = "Système") -> str:
        """Génère la balise XML <artifact id="AEC_directive">."""
        return f'<artifact id="AEC_directive" source="{source}">\n{message}\n</artifact>\n\n'

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

