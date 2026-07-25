"""
title: ECHO Protocol Adapter
author: Wilfried BARNAVON
version: 1.1
description: Couche de traduction parametres ECHO -> format API Gemini. Deux backends : AI Studio (cle API) et Code Assist v1internal (OAuth2). Fonctions pures -- aucun appel reseau, aucun etat. Point de modification unique pour adapter les requetes CA.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.0: Creation initiale (migration Gemini-CI -> AGY, 2026-05-25).
# 1.1: CA_EXCLUDED_GEN_CONF_FIELDS vide -- thinkingConfig doit passer
# integralement vers CA (includeThoughts=True requis pour visibilite
# des pensees dans pipe_engine). Confirme OK par diag D/D-bis/D-ter.

from echo_constants import get_generation_config


# Champs de generationConfig exclus du payload Code Assist v1internal.
# Seul endroit a modifier si un champ doit etre exclu.
#
# Actuellement vide : tous les champs generationConfig passent integralement vers CA.
#
# thinkingConfig : PAS exclu.
#   pipe_engine.py construit : {includeThoughts: True, thinkingLevel: THINKING_LEVEL_*}.
#   includeThoughts=True est requis pour que CA retourne des parts "thought=True" lisibles.
#   Sans ce champ, CA retourne uniquement le thoughtSignature opaque -- pensees invisibles.
#   thinkingLevel depuis constantes ECHO (THINKING_LEVEL_PRO/FLASH/LITE) -- redondant
#   avec le nom du modele (*-agent=HIGH) mais inoffensif (diag D/D-bis/D-ter : 200).
#
# response_mime_type : PAS exclu.
#   Accepte sur CA (diag section 11, 200 sur tous modeles).
#   Utilise par call_distillation(is_json=True).
CA_EXCLUDED_GEN_CONF_FIELDS: frozenset = frozenset()


def get_ca_model_id(echo_model: str) -> str:
    """
    Traduit un nom abstrait ECHO vers l'ID interne Code Assist.
    """
    from echo_constants import ECHO_MODELS_REGISTRY
    config = ECHO_MODELS_REGISTRY.get(echo_model)
    return config["ca_model_id"] if config else ECHO_MODELS_REGISTRY.get("MODEL_LITE", {}).get("ca_model_id", "gemini-3.1-flash-lite")


def build_ca_generation_config(raw_gen_conf: dict) -> dict:
    """
    Normalise generationConfig pour le backend Code Assist (v1internal).

    Transformations :
      1. Exclusion des champs CA_EXCLUDED_GEN_CONF_FIELDS (actuellement vide).
      2. Cap maxOutputTokens a MAX_TOKENS_DEFAULT (65535 universel, decision D1).
         Corrige le 400 de production : 65536 > 65535 sur Gemini 3.1 Pro/Lite CA.

    thinkingConfig : passe integralement.
      includeThoughts=True est pose par pipe_engine pour rendre les pensees visibles
      dans le stream (<think>...</think> gere par pipe_engine.process()).
      thinkingLevel herite des constantes ECHO (THINKING_LEVEL_PRO/FLASH/LITE).

    topP, temperature, response_mime_type : conserves.
    response_mime_type accepte sur CA (diag section 11, 200 sur tous modeles).

    Args:
        raw_gen_conf : generationConfig brut du payload OWUI

    Returns:
        dict : generationConfig normalise pour Code Assist
    """
    # 1. Exclusion des champs redondants / incompatibles
    gen = {k: v for k, v in raw_gen_conf.items() if k not in CA_EXCLUDED_GEN_CONF_FIELDS}

    # 2. Cap universel maxOutputTokens (meme valeur AI Studio et CA -- decision D1)
    if "maxOutputTokens" in gen:
        gen["maxOutputTokens"] = min(gen["maxOutputTokens"], get_generation_config("MODEL_FLASH").get("maxOutputTokens", 65535))

    return gen
