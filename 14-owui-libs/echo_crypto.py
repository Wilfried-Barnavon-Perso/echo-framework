# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Crypto (Unified AEAD Engine)
author: Wilfried BARNAVON
version: 6.22.1.1
description: Chiffrement AEAD (AES-256-GCM + HKDF-SHA256) Vault, Auth, TOTP, Admin et Sauvegardes EBKP1.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.1.1: Moteur streaming Zéro-RAM EBKP1 (AES-256-GCM par blocs 4 MiB, vérification clé d'en-tête) et chiffrement admin_settings.
# 6.21.10.1: Unification AEAD multi-domaines (HKDF Vault, auth_data et TOTP SSO) via _derive_scoped_key/_encrypt_scoped_payload/_decrypt_scoped_payload.
# 5.231.21.3: Création du moteur cryptographique AEAD AES-256-GCM (HKDF-SHA256, AAD et politique Fail-Closed) pour l'Identity Vault.

import base64
import hashlib
import os
import struct
from typing import BinaryIO, Dict, Tuple
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENC_PREFIX = "enc:v1:"
ENV_VAULT_KEY = "ECHO_VAULT_ENCRYPTION_KEY"
ENV_AUTH_KEY = "ECHO_AUTH_ENCRYPTION_KEY"
ENV_BACKUP_KEY = "ECHO_BACKUP_ENCRYPTION_KEY"

HKDF_INFO = b"echo-identity-vault"
HKDF_INFO_AUTH_DATA = b"echo-auth-data"
HKDF_INFO_AUTH_TOTP = b"echo-auth-totp"
HKDF_INFO_ADMIN_SETTINGS = b"echo-admin-settings"
HKDF_INFO_BACKUP_STREAM = b"echo-backup-stream-v1"

BACKUP_ENC_MAGIC = b"EBKP1"
BACKUP_DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
_BACKUP_HDR_CHECK_PLAIN = b"OK\x00\x01"
_BACKUP_HEADER_FMT = ">5sI32s4s20s"
_BACKUP_HEADER_LEN = struct.calcsize(_BACKUP_HEADER_FMT)  # 65 octets
_BACKUP_BLK_HDR_FMT = ">BI"
_BACKUP_BLK_HDR_LEN = struct.calcsize(_BACKUP_BLK_HDR_FMT)  # 5 octets
_GCM_TAG_LEN = 16

_SCOPED_KEY_CACHE: Dict[Tuple[bytes, bytes, str], bytes] = {}


def _get_master_key(env_var: str = ENV_VAULT_KEY) -> bytes:
    """Récupère une clé maître d'instance depuis l'environnement."""
    env_key = os.environ.get(env_var, "").strip()
    return env_key.encode("utf-8") if env_key else b""


def _derive_scoped_key(env_var: str, scope_id: str, hkdf_info: bytes) -> bytes:
    """Dérive de manière déterministe une clé AES-256 (32 octets) via HKDF-SHA256."""
    master_key = _get_master_key(env_var)
    if not master_key:
        return b""
    master_digest = hashlib.sha256(master_key).digest()
    norm_scope = str(scope_id)
    cache_key = (master_digest, hkdf_info, norm_scope)
    cached = _SCOPED_KEY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=norm_scope.encode("utf-8"),
        info=hkdf_info,
    )
    derived = hkdf.derive(master_key)
    _SCOPED_KEY_CACHE[cache_key] = derived
    return derived


def derive_user_key(user_id: str) -> bytes:
    """Dérive une clé AES-256 dédiée par utilisateur pour l'Identity Vault."""
    return _derive_scoped_key(ENV_VAULT_KEY, str(user_id), HKDF_INFO)


def is_encrypted_payload(payload: str) -> bool:
    """Indique si une chaîne est déjà chiffrée au format enc:v1:."""
    return isinstance(payload, str) and payload.startswith(ENC_PREFIX)


def _encrypt_scoped_payload(
    plaintext: str,
    env_var: str,
    scope_id: str,
    hkdf_info: bytes,
    aad_str: str,
) -> str:
    """Chiffre une chaîne UTF-8 en AES-256-GCM avec dérivation HKDF et liaison AAD."""
    if not plaintext or not isinstance(plaintext, str) or plaintext.startswith(ENC_PREFIX):
        return plaintext

    key = _derive_scoped_key(env_var, scope_id, hkdf_info)
    if not key:
        raise RuntimeError(f"{env_var} absente : chiffrement AEAD impossible (Fail-Closed).")

    nonce = os.urandom(12)
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad_str.encode("utf-8"))
    ciphertext, tag = ct_and_tag[:-16], ct_and_tag[-16:]

    n_b64 = base64.b64encode(nonce).decode("ascii")
    c_b64 = base64.b64encode(ciphertext).decode("ascii")
    t_b64 = base64.b64encode(tag).decode("ascii")
    return f"{ENC_PREFIX}{n_b64}:{c_b64}:{t_b64}"


def _decrypt_scoped_payload(
    payload: str,
    env_var: str,
    scope_id: str,
    hkdf_info: bytes,
    aad_str: str,
) -> str:
    """Déchiffre un payload enc:v1:... en AES-256-GCM avec vérification AAD."""
    if not payload or not isinstance(payload, str):
        return payload or ""
    if not payload.startswith(ENC_PREFIX):
        return payload

    key = _derive_scoped_key(env_var, scope_id, hkdf_info)
    if not key:
        raise RuntimeError(f"{env_var} absente : déchiffrement AEAD impossible.")

    parts = payload[len(ENC_PREFIX):].split(":")
    if len(parts) != 3:
        raise ValueError("Format de payload chiffré enc:v1 invalide.")

    nonce = base64.b64decode(parts[0])
    ciphertext = base64.b64decode(parts[1])
    tag = base64.b64decode(parts[2])
    if len(nonce) != 12 or len(tag) != 16:
        raise ValueError("Taille de nonce (12o) ou tag GCM (16o) invalide.")

    return AESGCM(key).decrypt(nonce, ciphertext + tag, aad_str.encode("utf-8")).decode("utf-8")


def encrypt_vault_payload(plaintext: str, user_id: str, service: str, account_id: str) -> str:
    """Chiffre un secret de l'Identity Vault avec AAD (user_id:service:account_id)."""
    return _encrypt_scoped_payload(
        plaintext, ENV_VAULT_KEY, str(user_id), HKDF_INFO, f"{user_id}:{service}:{account_id}"
    )


def decrypt_vault_payload(payload: str, user_id: str, service: str, account_id: str) -> str:
    """Déchiffre un secret de l'Identity Vault avec vérification AAD."""
    return _decrypt_scoped_payload(
        payload, ENV_VAULT_KEY, str(user_id), HKDF_INFO, f"{user_id}:{service}:{account_id}"
    )


def encrypt_auth_data_payload(plaintext: str, safe_uid: str, key_name: str) -> str:
    """Chiffre une entrée sensible auth_data (Gemini API / OAuth2) avec AAD (safe_uid:auth_data:key_name)."""
    uid = str(safe_uid)
    return _encrypt_scoped_payload(
        plaintext, ENV_VAULT_KEY, uid, HKDF_INFO_AUTH_DATA, f"{uid}:auth_data:{key_name}"
    )


def decrypt_auth_data_payload(payload: str, safe_uid: str, key_name: str) -> str:
    """Déchiffre une entrée sensible auth_data avec vérification AAD."""
    uid = str(safe_uid)
    return _decrypt_scoped_payload(
        payload, ENV_VAULT_KEY, uid, HKDF_INFO_AUTH_DATA, f"{uid}:auth_data:{key_name}"
    )


def encrypt_totp_secret(secret: str, email: str) -> str:
    """Chiffre une graine MFA TOTP (auth.db) via ECHO_AUTH_ENCRYPTION_KEY et AAD (totp:email)."""
    norm_email = (email or "").strip().lower()
    if not norm_email:
        raise ValueError("Email requis pour le chiffrement TOTP.")
    return _encrypt_scoped_payload(
        secret, ENV_AUTH_KEY, norm_email, HKDF_INFO_AUTH_TOTP, f"totp:{norm_email}"
    )


def decrypt_totp_secret(payload: str, email: str) -> str:
    """Déchiffre une graine MFA TOTP (auth.db) avec vérification AAD."""
    norm_email = (email or "").strip().lower()
    if not norm_email:
        raise ValueError("Email requis pour le déchiffrement TOTP.")
    return _decrypt_scoped_payload(
        payload, ENV_AUTH_KEY, norm_email, HKDF_INFO_AUTH_TOTP, f"totp:{norm_email}"
    )


def _derive_raw_key(raw_key: str, salt: bytes, hkdf_info: bytes) -> bytes:
    """Dérive une clé AES-256 (32 octets) par HKDF-SHA256 depuis une clé brute explicite."""
    clean_key = (raw_key or "").strip()
    if not clean_key:
        raise RuntimeError("Clé maîtresse absente : opération AEAD refusée (Fail-Closed).")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=hkdf_info,
    ).derive(clean_key.encode("utf-8"))


def encrypt_admin_secret(plaintext: str, raw_key: str, field_name: str) -> str:
    """Chiffre un secret d'administration (ex: sftp_pass) au format enc:v1:... avec clé explicite."""
    if not plaintext or not isinstance(plaintext, str) or plaintext.startswith(ENC_PREFIX):
        return plaintext
    key = _derive_raw_key(raw_key, f"admin:{field_name}".encode("utf-8"), HKDF_INFO_ADMIN_SETTINGS)
    nonce = os.urandom(12)
    aad = f"admin_settings:{field_name}".encode("utf-8")
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    ciphertext, tag = ct_and_tag[:-_GCM_TAG_LEN], ct_and_tag[-_GCM_TAG_LEN:]
    n_b64 = base64.b64encode(nonce).decode("ascii")
    c_b64 = base64.b64encode(ciphertext).decode("ascii")
    t_b64 = base64.b64encode(tag).decode("ascii")
    return f"{ENC_PREFIX}{n_b64}:{c_b64}:{t_b64}"


def decrypt_admin_secret(payload: str, raw_key: str, field_name: str) -> str:
    """Déchiffre un secret d'administration enc:v1:... avec vérification AAD."""
    if not payload or not isinstance(payload, str):
        return payload or ""
    if not payload.startswith(ENC_PREFIX):
        return payload
    key = _derive_raw_key(raw_key, f"admin:{field_name}".encode("utf-8"), HKDF_INFO_ADMIN_SETTINGS)
    parts = payload[len(ENC_PREFIX):].split(":")
    if len(parts) != 3:
        raise ValueError("Format de secret admin enc:v1 invalide.")
    nonce = base64.b64decode(parts[0])
    ciphertext = base64.b64decode(parts[1])
    tag = base64.b64decode(parts[2])
    if len(nonce) != 12 or len(tag) != _GCM_TAG_LEN:
        raise ValueError("Taille de nonce ou tag GCM invalide.")
    aad = f"admin_settings:{field_name}".encode("utf-8")
    return AESGCM(key).decrypt(nonce, ciphertext + tag, aad).decode("utf-8")


def is_encrypted_backup_file(file_path: str) -> bool:
    """Retourne True si le fichier débute par la signature magique EBKP1."""
    if not file_path or not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            return f.read(len(BACKUP_ENC_MAGIC)) == BACKUP_ENC_MAGIC
    except OSError:
        return False


def _read_full_chunk(stream: BinaryIO, size: int) -> bytes:
    """Lit jusqu'à `size` octets complets depuis un flux (pipe ou fichier) avant EOF."""
    if size <= 0:
        return b""
    buf = bytearray()
    while len(buf) < size:
        part = stream.read(size - len(buf))
        if not part:
            break
        buf.extend(part)
    return bytes(buf)


def _parse_and_verify_backup_header(header_bytes: bytes, raw_key: str) -> Tuple[int, bytes, AESGCM]:
    """Valide l'en-tête EBKP1 (65o) et vérifie le tag GCM rapide de la clé."""
    if len(header_bytes) != _BACKUP_HEADER_LEN:
        raise ValueError("En-tête d'archive chiffrée tronqué ou invalide.")
    magic, chunk_size, salt, nonce_prefix, hdr_ct = struct.unpack(_BACKUP_HEADER_FMT, header_bytes)
    if magic != BACKUP_ENC_MAGIC:
        raise ValueError("Signature d'archive EBKP1 absente.")
    if not (65536 <= chunk_size <= 64 * 1024 * 1024):
        raise ValueError("Taille de bloc EBKP1 hors bornes autorisées.")
    aes_key = _derive_raw_key(raw_key, salt, HKDF_INFO_BACKUP_STREAM)
    aesgcm = AESGCM(aes_key)
    nonce_hdr = nonce_prefix + struct.pack(">Q", 0)
    try:
        plain_hdr = aesgcm.decrypt(nonce_hdr, hdr_ct, b"EBKP1:HDR")
    except InvalidTag as exc:
        raise ValueError("Clé de déchiffrement invalide.") from exc
    if plain_hdr != _BACKUP_HDR_CHECK_PLAIN:
        raise ValueError("Validation d'en-tête EBKP1 échouée.")
    return chunk_size, nonce_prefix, aesgcm


def encrypt_backup_stream(
    src_stream: BinaryIO,
    dst_stream: BinaryIO,
    raw_key: str,
    chunk_size: int = BACKUP_DEFAULT_CHUNK_SIZE,
) -> int:
    """Chiffre un flux binaire en streaming Zéro-RAM (EBKP1 / AES-256-GCM par blocs)."""
    if not (65536 <= chunk_size <= 64 * 1024 * 1024):
        raise ValueError("chunk_size doit être compris entre 64 Kio et 64 Mio.")
    salt = os.urandom(32)
    nonce_prefix = os.urandom(4)
    aes_key = _derive_raw_key(raw_key, salt, HKDF_INFO_BACKUP_STREAM)
    aesgcm = AESGCM(aes_key)

    hdr_ct = aesgcm.encrypt(nonce_prefix + struct.pack(">Q", 0), _BACKUP_HDR_CHECK_PLAIN, b"EBKP1:HDR")
    header = struct.pack(_BACKUP_HEADER_FMT, BACKUP_ENC_MAGIC, chunk_size, salt, nonce_prefix, hdr_ct)
    dst_stream.write(header)
    written = len(header)

    cur_buf = _read_full_chunk(src_stream, chunk_size)
    chunk_idx = 1

    if not cur_buf:
        # Flux vide : émet un unique bloc terminal (is_final=1, plain_len=0) pour authentifier l'EOF
        aad = b"EBKP1:BLK:" + struct.pack(">QBI", chunk_idx, 1, 0)
        enc_blk = aesgcm.encrypt(nonce_prefix + struct.pack(">Q", chunk_idx), b"", aad)
        blk_hdr = struct.pack(_BACKUP_BLK_HDR_FMT, 1, 0)
        dst_stream.write(blk_hdr)
        dst_stream.write(enc_blk)
        return written + len(blk_hdr) + len(enc_blk)

    while True:
        # Lecture d'avance uniquement si le bloc courant est plein (sinon EOF déjà atteint)
        next_buf = _read_full_chunk(src_stream, chunk_size) if len(cur_buf) == chunk_size else b""
        is_final = 0 if next_buf else 1
        plain_len = len(cur_buf)
        nonce = nonce_prefix + struct.pack(">Q", chunk_idx)
        aad = b"EBKP1:BLK:" + struct.pack(">QBI", chunk_idx, is_final, plain_len)
        enc_blk = aesgcm.encrypt(nonce, cur_buf, aad)
        blk_hdr = struct.pack(_BACKUP_BLK_HDR_FMT, is_final, plain_len)
        dst_stream.write(blk_hdr)
        dst_stream.write(enc_blk)
        written += len(blk_hdr) + len(enc_blk)
        if is_final:
            break
        cur_buf = next_buf
        chunk_idx += 1

    return written


def verify_backup_key_file(file_path: str, raw_key: str) -> bool:
    """Vérifie en < 1 ms la validité d'une clé sur l'en-tête EBKP1 d'une sauvegarde."""
    if not (raw_key or "").strip() or not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(_BACKUP_HEADER_LEN)
        _parse_and_verify_backup_header(hdr, raw_key)
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def decrypt_backup_file_to_path(enc_path: str, dst_plain_path: str, raw_key: str) -> int:
    """Déchiffre et authentifie intégralement une archive EBKP1 vers `dst_plain_path` (Fail-Closed)."""
    if not (raw_key or "").strip():
        raise ValueError("Clé de déchiffrement absente.")
    total_plain = 0
    try:
        with open(enc_path, "rb") as f_in:
            header_bytes = f_in.read(_BACKUP_HEADER_LEN)
            chunk_size, nonce_prefix, aesgcm = _parse_and_verify_backup_header(header_bytes, raw_key)

            fd_out = os.open(dst_plain_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd_out, "wb") as f_out:
                chunk_idx = 1
                saw_final = False
                while True:
                    blk_hdr = _read_full_chunk(f_in, _BACKUP_BLK_HDR_LEN)
                    if not blk_hdr:
                        break
                    if len(blk_hdr) != _BACKUP_BLK_HDR_LEN:
                        raise ValueError("En-tête de bloc chiffré tronqué.")
                    is_final, plain_len = struct.unpack(_BACKUP_BLK_HDR_FMT, blk_hdr)
                    if is_final not in (0, 1) or plain_len > chunk_size:
                        raise ValueError("Métadonnées de bloc EBKP1 corrompues.")

                    enc_blk = _read_full_chunk(f_in, plain_len + _GCM_TAG_LEN)
                    if len(enc_blk) != plain_len + _GCM_TAG_LEN:
                        raise ValueError("Archive chiffrée tronquée en cours de bloc.")

                    nonce = nonce_prefix + struct.pack(">Q", chunk_idx)
                    aad = b"EBKP1:BLK:" + struct.pack(">QBI", chunk_idx, is_final, plain_len)
                    try:
                        plain_blk = aesgcm.decrypt(nonce, enc_blk, aad)
                    except InvalidTag as exc:
                        raise ValueError(
                            f"Intégrité AEAD compromise ou clé incorrecte (bloc {chunk_idx})."
                        ) from exc

                    if plain_blk:
                        f_out.write(plain_blk)
                        total_plain += len(plain_blk)

                    chunk_idx += 1
                    if is_final == 1:
                        saw_final = True
                        if f_in.read(1):
                            raise ValueError("Données parasites détectées après le bloc terminal.")
                        break

                if not saw_final:
                    raise ValueError("Archive chiffrée tronquée : marqueur de fin de flux absent.")
    except Exception:
        try:
            if os.path.exists(dst_plain_path):
                os.remove(dst_plain_path)
        except OSError:
            pass
        raise
    return total_plain
