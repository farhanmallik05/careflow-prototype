"""Column-level envelope encryption for sensitive patient demographic fields at rest."""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any, Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


ENVELOPE_PREFIX = "env:v1:"
DEMOGRAPHIC_FIELDS = {"patient_name", "name", "phone", "email", "dob", "mrn", "address"}


class EnvelopeEncryptor:
    """Envelope encryption provider using AES-256-GCM and HKDF key derivation."""

    def __init__(self, master_key: str | bytes | None = None, *, enabled: bool = True) -> None:
        self.enabled = enabled
        raw_key = master_key or os.environ.get("CAREFLOW_ENCRYPTION_KEY", "careflow-dev-master-encryption-key-32b")
        if isinstance(raw_key, str):
            raw_key = raw_key.encode("utf-8")
        # Derive 32-byte KEK using HKDF with a constant domain separator salt
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"careflow-phi-envelope-salt-v1",
            info=b"careflow-kek-derivation",
        )
        self._kek = hkdf.derive(raw_key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string into an envelope-encrypted token."""
        if not self.enabled or not plaintext:
            return plaintext
        if plaintext.startswith(ENVELOPE_PREFIX):
            return plaintext  # already encrypted

        # 1. Generate ephemeral 256-bit DEK
        dek = AESGCM.generate_key(bit_length=256)
        dek_aes = AESGCM(dek)

        # 2. Encrypt plaintext with DEK
        data_nonce = secrets.token_bytes(12)
        ciphertext = dek_aes.encrypt(data_nonce, plaintext.encode("utf-8"), None)

        # 3. Encrypt DEK with KEK
        kek_aes = AESGCM(self._kek)
        kek_nonce = secrets.token_bytes(12)
        encrypted_dek = kek_aes.encrypt(kek_nonce, dek, None)

        envelope = {
            "kn": base64.b64encode(kek_nonce).decode("ascii"),
            "ed": base64.b64encode(encrypted_dek).decode("ascii"),
            "dn": base64.b64encode(data_nonce).decode("ascii"),
            "ct": base64.b64encode(ciphertext).decode("ascii"),
        }
        encoded = base64.urlsafe_b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")
        return f"{ENVELOPE_PREFIX}{encoded}"

    def decrypt(self, token: str) -> str:
        """Decrypt an envelope-encrypted token back to plaintext string."""
        if not token or not token.startswith(ENVELOPE_PREFIX):
            return token  # Unencrypted legacy data, return as-is

        raw_encoded = token[len(ENVELOPE_PREFIX) :]
        try:
            raw_json = base64.urlsafe_b64decode(raw_encoded.encode("ascii")).decode("utf-8")
            envelope = json.loads(raw_json)
            kek_nonce = base64.b64decode(envelope["kn"])
            encrypted_dek = base64.b64decode(envelope["ed"])
            data_nonce = base64.b64decode(envelope["dn"])
            ciphertext = base64.b64decode(envelope["ct"])

            # 1. Decrypt DEK with KEK
            kek_aes = AESGCM(self._kek)
            dek = kek_aes.decrypt(kek_nonce, encrypted_dek, None)

            # 2. Decrypt data with DEK
            dek_aes = AESGCM(dek)
            plaintext_bytes = dek_aes.decrypt(data_nonce, ciphertext, None)
            return plaintext_bytes.decode("utf-8")
        except Exception as exc:
            raise ValueError(f"Failed to decrypt demographic envelope token: {exc}") from exc

    def encrypt_demographics(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Encrypt all demographic fields in a dictionary record."""
        result = dict(record)
        for key in DEMOGRAPHIC_FIELDS:
            if key in result and isinstance(result[key], str):
                result[key] = self.encrypt(result[key])
        return result

    def decrypt_demographics(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Decrypt all demographic fields in a dictionary record."""
        result = dict(record)
        for key in DEMOGRAPHIC_FIELDS:
            if key in result and isinstance(result[key], str):
                result[key] = self.decrypt(result[key])
        return result


__all__ = ["EnvelopeEncryptor", "ENVELOPE_PREFIX", "DEMOGRAPHIC_FIELDS"]
