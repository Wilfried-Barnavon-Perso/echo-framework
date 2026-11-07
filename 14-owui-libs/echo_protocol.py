"""
title: ECHO Protocol Adapter
author: Wilfried BARNAVON
version: 5.233.0.1
description: Couche de traduction parametres ECHO -> format API Gemini. Deux backends : AI Studio (cle API) et Code Assist v1internal (OAuth2). Fonctions pures -- aucun appel reseau, aucun etat. Point de modification unique pour adapter les requetes CA.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.233.0.1: Ajout de normalize_response_schema, normalize_generation_config et des modèles Pydantic SSOT de sorties JSON contraintes (responseSchema Gemini 3+).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.3: Alignement documentaire interne sur echo_orchestrator.py.
# 1.2: Alignement sur la nomenclature cognitive (MODEL_ZEPHYR, MODEL_APOLLO, MODEL_PROMETHEUS).
# 1.1: CA_EXCLUDED_GEN_CONF_FIELDS vide -- thinkingConfig doit passer integralement vers CA.

import copy
from typing import Any, Dict, List, Literal, Optional, Type, Union
from pydantic import BaseModel, Field

from echo_constants import get_generation_config

# Champs de generationConfig exclus du payload Code Assist v1internal.
# Seul endroit a modifier si un champ doit etre exclu.
#
# Actuellement vide : tous les champs generationConfig passent integralement vers CA.
#
# thinkingConfig : PAS exclu.
#   echo_orchestrator.py construit : {includeThoughts: True, thinkingLevel: THINKING_LEVEL_*}.
#   includeThoughts=True est requis pour que CA retourne des parts "thought=True" lisibles.
#   Sans ce champ, CA retourne uniquement le thoughtSignature opaque -- pensees invisibles.
#   thinkingLevel depuis constantes ECHO (THINKING_LEVEL_PROMETHEUS/APOLLO/ZEPHYR) -- redondant
#   avec le nom du modele (*-agent=HIGH) mais inoffensif (diag D/D-bis/D-ter : 200).
#
# response_mime_type : PAS exclu.
#   Accepte sur CA (diag section 11, 200 sur tous modeles).
#   Utilise par call_distillation(is_json=True).
CA_EXCLUDED_GEN_CONF_FIELDS: frozenset = frozenset()

GEMINI_SUPPORTED_SCHEMA_KEYS: frozenset = frozenset({
    "type",
    "description",
    "enum",
    "nullable",
    "properties",
    "required",
    "items",
})

GEMINI_TYPE_MAPPING: Dict[str, str] = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
}


# ==============================================================================
# MODÈLES PYDANTIC SSOT POUR SORTIES JSON CONTRAINTES (GEMINI 3+ responseSchema)
# ==============================================================================

class RagDistillOutput(BaseModel):
    memory_id: str = Field(description="Identifiant unique du souvenir consolidé.")
    tags: List[str] = Field(default_factory=list, description="Liste des tags identifiés.")


class SkillRouterOutput(BaseModel):
    best_matches: List[str] = Field(default_factory=list, description="Liste des compétences pertinentes sélectionnées.")


class WorkerFeedbackItem(BaseModel):
    worker_id: str = Field(description="Identifiant unique du worker.")
    status: Literal["ok", "needs_correction"] = Field(description="Statut d'évaluation du travail fourni.")
    feedback: str = Field(default="", description="Commentaire ou directive d'ajustement pour le worker.")


class SupervisorCriticOutput(BaseModel):
    global_assessment: str = Field(description="Évaluation globale de la synthèse.")
    verdict: Literal["APPROVED", "REJECTED"] = Field(description="Verdict de validation globale.")
    worker_feedback: List[WorkerFeedbackItem] = Field(default_factory=list, description="Retours individuels par worker.")


class VisualCriticOutput(BaseModel):
    verdict: Literal["APPROVED", "REJECTED"] = Field(description="Verdict de conformité visuelle.")
    score: int = Field(description="Score esthétique et technique de 0 à 100.")
    short_name: str = Field(default="", description="Nom synthétique du sujet visuel.")
    analysis: str = Field(description="Analyse critique détaillée.")
    defects: List[str] = Field(default_factory=list, description="Défauts visuels ou anomalies identifiés.")
    correction_prompt: str = Field(default="", description="Prompt de correction à appliquer.")


class FastTrackTitleOutput(BaseModel):
    title: str = Field(description="Titre concis généré pour la conversation.")


class FastTrackTagsOutput(BaseModel):
    tags: List[str] = Field(default_factory=list, description="Tags générés pour la conversation.")


class FastTrackFollowUpsOutput(BaseModel):
    follow_ups: List[str] = Field(default_factory=list, description="Suggestions de relances ou questions suivantes.")


FAST_TRACK_TASK_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "title_generation": FastTrackTitleOutput,
    "tags_generation": FastTrackTagsOutput,
    "follow_up_generation": FastTrackFollowUpsOutput,
}


def normalize_response_schema(schema: Union[Type[BaseModel], dict, None]) -> Optional[dict]:
    """
    Normalise un modèle Pydantic ou un schéma JSON Schema brut au format Gemini OpenAPI 3.0.

    Gemini OpenAPI 3.0 exige :
    - Clés autorisées : type, description, enum, nullable, properties, required, items.
    - Types en majuscules : OBJECT, ARRAY, STRING, INTEGER, NUMBER, BOOLEAN.
    - Résolution inline des $defs / $ref (aucun $ref externe autorisé).
    - Conversion des anyOf / oneOf nullables en type unique avec nullable=True.
    - Conversion de prefixItems en items.
    """
    if schema is None:
        return None

    if isinstance(schema, type) and issubclass(schema, BaseModel):
        if hasattr(schema, "model_json_schema"):
            raw_dict = schema.model_json_schema()
        else:
            raw_dict = schema.schema()
    elif isinstance(schema, dict):
        raw_dict = schema
    else:
        return None

    root = copy.deepcopy(raw_dict)
    defs = root.pop("$defs", root.pop("definitions", {}))

    def _resolve_node(node: Any) -> Any:
        if not isinstance(node, dict):
            return node

        current = dict(node)

        # 1. Résolution de $ref
        ref = current.pop("$ref", None)
        if ref and isinstance(ref, str):
            ref_name = ref.split("/")[-1]
            if ref_name in defs:
                resolved_def = copy.deepcopy(defs[ref_name])
                for k, v in current.items():
                    resolved_def[k] = v
                current = resolved_def

        # 2. Résolution anyOf / oneOf (support Optionals/Nullables)
        branches = current.pop("anyOf", current.pop("oneOf", None))
        if isinstance(branches, list) and branches:
            is_nullable = any(isinstance(b, dict) and b.get("type") == "null" for b in branches)
            if is_nullable:
                current["nullable"] = True
            non_null = [b for b in branches if isinstance(b, dict) and b.get("type") != "null"]
            if non_null:
                primary = copy.deepcopy(non_null[0])
                for k, v in primary.items():
                    if k not in current:
                        current[k] = v

        # 3. Conversion prefixItems -> items
        prefix_items = current.pop("prefixItems", None)
        if prefix_items and "items" not in current:
            if isinstance(prefix_items, list) and prefix_items:
                current["items"] = prefix_items[0]
            elif isinstance(prefix_items, dict):
                current["items"] = prefix_items

        # 4. Mapping de types
        raw_type = current.get("type")
        if isinstance(raw_type, str):
            t_lower = raw_type.lower()
            if t_lower in GEMINI_TYPE_MAPPING:
                current["type"] = GEMINI_TYPE_MAPPING[t_lower]
            elif t_lower == "null":
                current["nullable"] = True
                current.pop("type", None)
        elif isinstance(raw_type, list):
            if "null" in raw_type:
                current["nullable"] = True
            non_null_types = [t for t in raw_type if t != "null"]
            if non_null_types:
                t_first = non_null_types[0].lower()
                if t_first in GEMINI_TYPE_MAPPING:
                    current["type"] = GEMINI_TYPE_MAPPING[t_first]

        # 5. Normalisation récursive des sous-arbres (properties et items)
        if "properties" in current and isinstance(current["properties"], dict):
            current["properties"] = {k: _resolve_node(v) for k, v in current["properties"].items()}

        if "items" in current:
            current["items"] = _resolve_node(current["items"])

        # 6. Filtrage strict Gemini OpenAPI 3.0
        cleaned = {k: v for k, v in current.items() if k in GEMINI_SUPPORTED_SCHEMA_KEYS}
        return cleaned

    return _resolve_node(root)


def normalize_generation_config(raw_gen_conf: dict) -> dict:
    """
    Normalise generationConfig pour Gemini (AI Studio et Code Assist) :
    - response_mime_type -> responseMimeType
    - response_schema / responseSchema -> responseSchema normalisé + responseMimeType="application/json"
    """
    if not isinstance(raw_gen_conf, dict):
        return {}

    conf = copy.deepcopy(raw_gen_conf)

    # 1. Normalisation mime type
    mime = conf.pop("response_mime_type", None)
    if mime and "responseMimeType" not in conf:
        conf["responseMimeType"] = mime

    # 2. Normalisation responseSchema
    schema = conf.pop("response_schema", None)
    if schema is None:
        schema = conf.pop("responseSchema", None)

    if schema is not None:
        normalized_schema = normalize_response_schema(schema)
        if normalized_schema:
            conf["responseSchema"] = normalized_schema
            conf["responseMimeType"] = "application/json"

    return conf


def get_ca_model_id(echo_model: str) -> str:
    """
    Traduit un nom abstrait ECHO vers l'ID interne Code Assist.
    """
    from echo_constants import ECHO_MODELS_REGISTRY
    config = ECHO_MODELS_REGISTRY.get(echo_model)
    return config["ca_model_id"] if config else ECHO_MODELS_REGISTRY.get("MODEL_ZEPHYR", {}).get("ca_model_id", "gemini-3.1-flash-lite")


def build_ca_generation_config(raw_gen_conf: dict) -> dict:
    """
    Normalise generationConfig pour le backend Code Assist (v1internal).

    Transformations :
      1. Normalisation responseSchema et responseMimeType via normalize_generation_config.
      2. Exclusion des champs CA_EXCLUDED_GEN_CONF_FIELDS (actuellement vide).
      3. Cap maxOutputTokens a MAX_TOKENS_DEFAULT (65535 universel, decision D1).
         Corrige le 400 de production : 65536 > 65535 sur Gemini 3.1 Pro/Lite CA.

    thinkingConfig : passe integralement.
      includeThoughts=True est pose par echo_orchestrator pour rendre les pensees visibles
      dans le stream (<think>...</think> gere par echo_orchestrator.process()).
      thinkingLevel herite des constantes ECHO (THINKING_LEVEL_PROMETHEUS/APOLLO/ZEPHYR).

    topP, temperature, response_mime_type : conserves.
    response_mime_type accepte sur CA (diag section 11, 200 sur tous modeles).

    Args:
        raw_gen_conf : generationConfig brut du payload OWUI

    Returns:
        dict : generationConfig normalise pour Code Assist
    """
    # 1. Normalisation responseSchema / responseMimeType
    normalized = normalize_generation_config(raw_gen_conf)

    # 2. Exclusion des champs redondants / incompatibles
    gen = {k: v for k, v in normalized.items() if k not in CA_EXCLUDED_GEN_CONF_FIELDS}

    # 3. Cap universel maxOutputTokens (meme valeur AI Studio et CA -- decision D1)
    if "maxOutputTokens" in gen:
        gen["maxOutputTokens"] = min(gen["maxOutputTokens"], get_generation_config("MODEL_APOLLO").get("maxOutputTokens", 65535))

    return gen
