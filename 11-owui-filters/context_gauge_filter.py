# -*- coding: utf-8 -*-
"""
title: ECHO Context Gauge Filter
author: Wilfried BARNAVON
version: 5.231.24.1
description: Filtre Inlet/Outlet (Priorité 10) affichant le HUD contextuel de tokens persistant avec différentiel et pourcentage de capacité perdue.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.24.1: Initialisation baseline des quotas et du contexte à l'Inlet du tour 1 pour calcul différentiel immédiat.
# 5.231.15.1: Alignement sur le calcul unifié d'empreinte capacitaire et la sanctuarisation de promptTokenCount.
# 5.231.13.1: Archivage unique de previous_context_stats en début de tour utilisateur, refresh Cloud des quotas en Outlet et résolution de ca_model_id.
# 5.231.12.1: Affichage garanti du HUD des l'inlet du 1er tour et persistance des fractions de quotas pour calcul du differentiel.
# 5.231.11.1: Réétagement de la priorité à 10 (après edge_embed_bridge_filter à 3 et avant user_native_context_filter à 20).

import sys
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_events import EchoEvents
from echo_state_manager import EchoStateManager
from echo_auth import AuthService, EchoAuth
from echo_protocol import get_ca_model_id
from echo_ui import EchoUI
from echo_window_manager import extract_closure_context


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=10,
            hidden=True,
            description="Priorité d'exécution (10 = après edge_embed_bridge_filter à 3 et avant user_native_context_filter à 20).",
        )
        show_context_metrics: bool = Field(
            default=True,
            description="Active le HUD contextuel de tokens et de quotas.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _resolve_context(
        self,
        __event_emitter__: Optional[Any],
        __user__: Optional[Dict[str, Any]],
        __metadata__: Optional[Dict[str, Any]],
        body: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        """Résout de manière robuste (user_id, chat_id) depuis les fermetures ou les métadonnées."""
        closure = extract_closure_context(__event_emitter__)
        user_id = closure.get("user_id") or (__user__.get("id") if __user__ else "") or "system"
        meta = __metadata__ or (body.get("metadata", {}) if body else {})
        chat_id = closure.get("chat_id") or meta.get("chat_id") or (body.get("chat_id") if body else "") or ""
        return user_id, chat_id

    async def _render_gauge(
        self,
        events: EchoEvents,
        user_id: str,
        chat_id: str,
        stats: Dict[str, Any],
        is_pending: bool,
    ) -> None:
        """Factorise le calcul du différentiel et la notification UI via EchoUI.deploy_context_gauge."""
        sm = EchoStateManager(user_id, chat_id)
        prev_stats = sm.get_previous_context_stats()
        last_model = sm.get_last_active_model()
        ca_model_id = get_ca_model_id(last_model) if last_model else None
        echo_auth = EchoAuth(user_id=user_id)
        quota_snap = echo_auth.get_ui_quota_snapshot(ca_model_id)

        enriched_stats = dict(stats)
        if quota_snap:
            enriched_stats["quota_5h_fraction"] = quota_snap.get("quota_5h_fraction")
            enriched_stats["quota_weekly_fraction"] = quota_snap.get("quota_weekly_fraction")

        delta = EchoUI.calculate_context_delta(enriched_stats, prev_stats)

        c_t = stats.get("cachedContentTokenCount", 0)
        p_t = stats.get("promptTokenCount", 0)
        g_t = stats.get("candidatesTokenCount", 0)
        active_p_t = max(0, p_t - c_t)
        max_t = quota_snap.get("max_tokens") or 1048576

        await EchoUI.deploy_context_gauge(
            events=events,
            plan_name=quota_snap.get("plan_name", ""),
            credits_val=quota_snap.get("credits_val", "N/A"),
            quota_str="",
            c_t=c_t,
            active_p_t=active_p_t,
            g_t=g_t,
            max_t=max_t,
            cache_pct=min(100.0, (c_t / max_t) * 100.0),
            prompt_pct=min(100.0, (active_p_t / max_t) * 100.0),
            gen_pct=min(100.0, (g_t / max_t) * 100.0),
            user_email=quota_snap.get("user_email"),
            user_tier=quota_snap.get("user_tier"),
            project_id=quota_snap.get("project_id"),
            auth_sources=quota_snap.get("auth_sources"),
            quota_fraction=quota_snap.get("quota_fraction", 1.0),
            quota_reset=quota_snap.get("quota_reset", "N/A"),
            quota_type=quota_snap.get("quota_type", "UNKNOWN"),
            quota_model=quota_snap.get("quota_model", ""),
            quota_rpd_rem=quota_snap.get("quota_rpd_rem", "N/A"),
            quota_rpd_lim=quota_snap.get("quota_rpd_lim", "N/A"),
            quota_rpm_rem=quota_snap.get("quota_rpm_rem", "N/A"),
            quota_rpm_lim=quota_snap.get("quota_rpm_lim", "N/A"),
            quota_5h_fraction=quota_snap.get("quota_5h_fraction"),
            quota_5h_reset=quota_snap.get("quota_5h_reset", "N/A"),
            quota_5h_disabled=quota_snap.get("quota_5h_disabled", False),
            quota_weekly_fraction=quota_snap.get("quota_weekly_fraction"),
            quota_weekly_reset=quota_snap.get("quota_weekly_reset", "N/A"),
            delta=delta,
            is_pending=is_pending,
            chat_id=chat_id,
        )

    async def inlet(
        self,
        body: dict,
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
        __event_emitter__: Optional[Any] = None,
        __request__: Optional[Any] = None,
    ) -> dict:
        """Déploie à l'entrée le HUD en mode attente (is_pending=True) avec les métriques du tour précédent."""
        if not self.valves.show_context_metrics or not __event_emitter__:
            return body

        try:
            meta = __metadata__ or (body.get("metadata", {}) if body else {})
            if meta.get("task") is not None:
                return body

            user_id, chat_id = self._resolve_context(__event_emitter__, __user__, __metadata__, body)
            if not chat_id:
                return body

            sm = EchoStateManager(user_id, chat_id)
            last_stats = sm.get_last_context_stats()
            stats_to_render = last_stats if (last_stats and last_stats.get("totalTokenCount", 0) > 0) else {
                "totalTokenCount": 0,
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
                "cachedContentTokenCount": 0,
            }

            events = EchoEvents(__event_emitter__, user_id=user_id, chat_id=chat_id)
            await self._render_gauge(events, user_id, chat_id, stats_to_render, is_pending=True)

            incoming_msgs = body.get("messages", [])
            is_tool_continuation = bool(incoming_msgs and incoming_msgs[-1].get("role") == "tool")
            if not is_tool_continuation:
                if last_stats and last_stats.get("totalTokenCount", 0) > 0:
                    sm.archive_previous_context_stats()
                else:
                    last_model = sm.get_last_active_model()
                    ca_model_id = get_ca_model_id(last_model) if last_model else None
                    echo_auth = EchoAuth(user_id=user_id)
                    quota_snap = echo_auth.get_ui_quota_snapshot(ca_model_id)
                    q5 = quota_snap.get("quota_5h_fraction") if quota_snap else None
                    qw = quota_snap.get("quota_weekly_fraction") if quota_snap else None
                    sm.init_session_context_baseline(quota_5h_fraction=q5, quota_weekly_fraction=qw)
        except Exception:
            pass

        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
        __event_emitter__: Optional[Any] = None,
        __request__: Optional[Any] = None,
    ) -> dict:
        """Met à jour à la sortie le HUD avec les stats fraîches générées par l'orchestrateur (is_pending=False)."""
        if not self.valves.show_context_metrics or not __event_emitter__:
            return body

        try:
            meta = __metadata__ or (body.get("metadata", {}) if body else {})
            if meta.get("task") is not None:
                return body

            user_id, chat_id = self._resolve_context(__event_emitter__, __user__, __metadata__, body)
            if not chat_id:
                return body

            sm = EchoStateManager(user_id, chat_id)
            cur_stats = sm.get_last_context_stats()
            if not cur_stats or cur_stats.get("totalTokenCount", 0) <= 0:
                return body

            try:
                await AuthService(user_id=user_id).refresh_quota_if_needed()
            except Exception:
                pass

            last_model = sm.get_last_active_model()
            ca_model_id = get_ca_model_id(last_model) if last_model else None
            echo_auth = EchoAuth(user_id=user_id)
            quota_snap = echo_auth.get_ui_quota_snapshot(ca_model_id)
            if quota_snap:
                cur_stats["quota_5h_fraction"] = quota_snap.get("quota_5h_fraction")
                cur_stats["quota_weekly_fraction"] = quota_snap.get("quota_weekly_fraction")
                sm.save_context_stats(cur_stats)

            events = EchoEvents(__event_emitter__, user_id=user_id, chat_id=chat_id)
            await self._render_gauge(events, user_id, chat_id, cur_stats, is_pending=False)
        except Exception:
            pass

        return body
