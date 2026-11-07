# -*- coding: utf-8 -*-
"""
title: ECHO AEC Manager
author: Wilfried BARNAVON
version: 5.235.5.2
description: Composant système interne : ECHO AEC Manager.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.235.5.2: Délégation purge et drainage transactionnel à EchoStateManager, suffixe de provenance hors_branche et suppression des accès directs identity.db.
# 5.231.28.1: Réintroduction de EchoAEC.render_context_checkpoint pour l'encapsulation XML déterministe de <artifact id="AEC_context_checkpoint">.
# 5.231.20.1: Suffixe UUID sur event_id dans EchoAEC.record_event pour éliminer les collisions intra-milliseconde.
# 5.227.1.1: Suppression de render_context_checkpoint suite à la suppression du compactage in-place.
# 5.226.18.0: Alignement sur le versioning global ECHO.

import time
import uuid
from typing import List, Dict, Any, Optional

OFF_BRANCH_SUFFIX = " — produit hors de la branche active de la conversation (production conservée)"


class DrainedDirectivesTuple(tuple):
    """Tuple (xml_formate, raw_events) avec support de recherche de sous-chaîne dans la restitution XML."""
    def __contains__(self, item):
        if len(self) > 0 and isinstance(self[0], str) and isinstance(item, str):
            if item in self[0]:
                return True
        return super().__contains__(item)


class EchoAEC:
    @staticmethod
    def record_event(state_manager: Any, event_name: str, summary: str = None, status: str = "active", resource_type: str = "aec_event") -> str:
        """Transmission SQLite : Inscrit un événement abstrait (ex: Directive de Cascade) dans le Registre Unifié."""
        event_id = f"aec_event_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
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
    def drain_pending_directives(state_manager: Any, chat_id: Optional[str] = None) -> Any:
        """
        Draine (extrait ET purge atomiquement dans une seule transaction) tous les événements
        'aec_directive' et 'aec_event' en attente dans la boîte aux lettres SQLite.
        Garantit une consommation unique avant scellement dans le Shadow.
        Retourne (xml_formate, liste_brute).
        """
        try:
            if isinstance(state_manager, str):
                from echo_state_manager import EchoStateManager
                sm = EchoStateManager(user_id=state_manager, chat_id=chat_id)
            else:
                sm = state_manager
            rows = sm.drain_resources_by_types(("aec_directive", "aec_event"))
            if not rows:
                return DrainedDirectivesTuple(("", []))

            output = ""
            raw_events = []
            for r in rows:
                res_type = r.get("resource_type")
                msg = r.get("summary") or r.get("content") or ""
                source = "Système"
                if res_type == "aec_directive":
                    output += f'<artifact id="AEC_directive" source="{source}">\n{msg}\n</artifact>\n'
                else:
                    output += f'<artifact id="AEC_evenement_systeme" source="{source}">\n{msg}\n</artifact>\n'
                raw_events.append(dict(r))

            return DrainedDirectivesTuple((output.strip(), raw_events))
        except Exception as e:
            print(f"[EchoAEC] Erreur drain_pending_directives: {e}")
            return DrainedDirectivesTuple(("", []))

    @staticmethod
    def purge_orphaned_events(state_manager: Any, t_pivot: float) -> int:
        """
        Purge tout AEC dont created_at > t_pivot.
        Invoqué lors d'un rewind ou d'une retouche de message pour éliminer les événements d'une branche avortée.
        """
        try:
            return state_manager.delete_resources_by_types_after(("aec_event", "aec_directive"), float(t_pivot))
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
                is_off_branch = evt.get("provenance") == "hors_branche"
                is_directive = evt.get("resource_type") == "aec_directive" or evt.get("type") == "directive"

                if msg and msg.startswith("<artifact"):
                    if is_off_branch and not is_directive and "</artifact>" in msg:
                        idx = msg.rfind("</artifact>")
                        before = msg[:idx]
                        if before.endswith("\n"):
                            msg = before[:-1] + OFF_BRANCH_SUFFIX + "\n" + msg[idx:]
                        else:
                            msg = before + OFF_BRANCH_SUFFIX + msg[idx:]
                    output += f"{msg}\n"
                    # Notification spéciale pour l'ingestion asynchrone réussie
                    if 'id="AEC_smart_context"' in msg:
                        notif_suffix = OFF_BRANCH_SUFFIX if is_off_branch else ""
                        output += f'<artifact id="AEC_evenement_systeme" source="{evt.get("source", "outil/HUD")}">\n'
                        output += f"Nouveau fichier asynchrone ingéré : {evt.get('name', '')}{notif_suffix}. Le contexte est disponible.\n"
                        output += '</artifact>\n'
                elif msg:  # RAPPEL COGNITIF OU ERREUR
                    if is_directive:
                        output += f'<artifact id="AEC_directive" source="{evt.get("source", "Système")}">\n{msg}\n</artifact>\n'
                    else:
                        msg_out = f"{msg}{OFF_BRANCH_SUFFIX}" if is_off_branch else msg
                        output += f'<artifact id="AEC_evenement_systeme" source="{evt.get("source", "Système")}">\n{msg_out}\n</artifact>\n'
                else:  # FICHIERS SYNCHRONES UPLOADÉS
                    texte = f"Nouveau fichier attaché : {evt.get('name', '')} (MIME: {evt.get('mime', '')})"
                    if evt.get('source_id'):
                        texte += f", Source ID: {evt.get('source_id')}"
                    if is_off_branch:
                        texte += OFF_BRANCH_SUFFIX
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

    @staticmethod
    def render_context_checkpoint(
        summary_md: str,
        resources_snapshot: str,
        compacted_turns: int,
        compaction_count: int,
        timestamp_str: str,
    ) -> str:
        """Génère la balise XML déterministe <artifact id="AEC_context_checkpoint"> pour la compaction in-place."""
        res_block = f"\n\n### 8. Registre Unifié des Ressources Actives (SQLite)\n{resources_snapshot}" if resources_snapshot else ""
        return (
            f'<artifact id="AEC_context_checkpoint" compaction_count="{compaction_count}" '
            f'compacted_turns="{compacted_turns}" timestamp="{timestamp_str}">\n'
            f'{summary_md.strip()}{res_block}\n\n'
            f'[DIRECTIVE SYSTÈME : Ce checkpoint synthétise les {compacted_turns} premiers tours de cette conversation. '
            f'Il fait foi sur l\'état antérieur. Si un détail granulaire antérieur au checkpoint (extrait de code exact, '
            f'log brut, valeur précise) est requis, le Modèle DOIT interroger la mémoire vectorielle de session via '
            f'`search_sessions_context` ou consulter le Registre Unifié via `query_registry`.]\n'
            f'</artifact>'
        )


