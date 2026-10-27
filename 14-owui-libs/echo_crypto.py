# -*- coding: utf-8 -*-
"""
title: ECHO Crypto (Identity Vault AEAD)
author: Wilfried BARNAVON
version: 5.231.21.3
description: Chiffrement applicatif au niveau champ (AES-256-GCM + HKDF-SHA256) pour l'Identity Vault.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.21.3: Création du moteur cryptographique AEAD AES-256-GCM (HKDF-SHA256, AAD et politique Fail-Closed) pour l'Identity Vault.

import os
import base64
import hashlib
from typing import Dict, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

ENC_PREFIX = "enc:v1:"
HKDF_INFO = b"echo-identity-vault"

_USER_KEY_CACHE: Dict[Tuple[bytes, str], bytes] = {}


def _get_master_key() -> bytes:
    """Récupère la clé maître d'instance depuis l'environnement."""
    env_key = os.environ.get("ECHO_VAULT_ENCRYPTION_KEY", "").strip()
    return env_key.encode("utf-8") if env_key else b""


def derive_user_key(user_id: str) -> bytes:
    """Dérive une clé AES-256 (32 octets) dédiée par utilisateur via HKDF-SHA256."""
    master_key = _get_master_key()
    if not master_key:
        return b""
    master_digest = hashlib.sha256(master_key).digest()
    cache_key = (master_digest, str(user_id))
    cached = _USER_KEY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(user_id).encode("utf-8"),
        info=HKDF_INFO,
    )
    derived = hkdf.derive(master_key)
    _USER_KEY_CACHE[cache_key] = derived
    return derived


def is_encrypted_payload(payload: str) -> bool:
    """Indique si une chaîne est déjà chiffrée au format enc:v1:."""
    return isinstance(payload, str) and payload.startswith(ENC_PREFIX)


def encrypt_vault_payload(plaintext: str, user_id: str, service: str, account_id: str) -> str:
    """Chiffre une chaîne UTF-8 en AES-256-GCM avec AAD (user_id:service:account_id)."""
    if not plaintext or not isinstance(plaintext, str) or plaintext.startswith(ENC_PREFIX):
        return plaintext

    key = derive_user_key(user_id)
    if not key:
        raise RuntimeError("ECHO_VAULT_ENCRYPTION_KEY absente : chiffrement du Vault impossible (Fail-Closed).")

    nonce = os.urandom(12)
    aad = f"{user_id}:{service}:{account_id}".encode("utf-8")
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    ciphertext, tag = ct_and_tag[:-16], ct_and_tag[-16:]

    n_b64 = base64.b64encode(nonce).decode("ascii")
    c_b64 = base64.b64encode(ciphertext).decode("ascii")
    t_b64 = base64.b64encode(tag).decode("ascii")
    return f"{ENC_PREFIX}{n_b64}:{c_b64}:{t_b64}"


def decrypt_vault_payload(payload: str, user_id: str, service: str, account_id: str) -> str:
    """Déchiffre un payload enc:v1:... en AES-256-GCM avec vérification AAD."""
    if not payload or not isinstance(payload, str):
        return payload or ""
    if not payload.startswith(ENC_PREFIX):
        return payload

    key = derive_user_key(user_id)
    if not key:
        raise RuntimeError("ECHO_VAULT_ENCRYPTION_KEY absente : déchiffrement impossible.")

    parts = payload[len(ENC_PREFIX):].split(":")
    if len(parts) != 3:
        raise ValueError("Format de payload chiffré enc:v1 invalide.")

    nonce = base64.b64decode(parts[0])
    ciphertext = base64.b64decode(parts[1])
    tag = base64.b64decode(parts[2])
    if len(nonce) != 12 or len(tag) != 16:
        raise ValueError("Taille de nonce (12o) ou tag GCM (16o) invalide.")
    aad = f"{user_id}:{service}:{account_id}".encode("utf-8")

    return AESGCM(key).decrypt(nonce, ciphertext + tag, aad).decode("utf-8")

