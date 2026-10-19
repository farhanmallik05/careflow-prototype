import pytest

from careflow.encryption import ENVELOPE_PREFIX, EnvelopeEncryptor


def test_envelope_encryption_roundtrip():
    encryptor = EnvelopeEncryptor(master_key="hospital-master-key-secure-2026")
    plaintext = "Jane Doe, DOB: 1982-04-12, MRN: #994827"

    token = encryptor.encrypt(plaintext)
    assert token.startswith(ENVELOPE_PREFIX)
    assert plaintext not in token  # PHI is fully obscured

    decrypted = encryptor.decrypt(token)
    assert decrypted == plaintext


def test_envelope_encryption_idempotency_and_legacy_fallback():
    encryptor = EnvelopeEncryptor(master_key="hospital-master-key-secure-2026")
    plaintext = "John Smith"

    # Legacy plaintext without prefix passes through transparently
    assert encryptor.decrypt(plaintext) == plaintext

    # Idempotent encryption: already encrypted token is not double-encrypted
    token = encryptor.encrypt(plaintext)
    token2 = encryptor.encrypt(token)
    assert token2 == token
    assert encryptor.decrypt(token2) == plaintext


def test_envelope_encryption_record_mapping():
    encryptor = EnvelopeEncryptor(master_key="hospital-master-key-secure-2026")
    record = {
        "case_id": "CASE-101",
        "patient_name": "Priya Sharma",
        "phone": "+1-555-0199",
        "email": "priya.sharma@example.org",
        "status": "pending_review",
    }

    encrypted_rec = encryptor.encrypt_demographics(record)
    assert encrypted_rec["case_id"] == "CASE-101"
    assert encrypted_rec["status"] == "pending_review"
    assert encrypted_rec["patient_name"].startswith(ENVELOPE_PREFIX)
    assert encrypted_rec["phone"].startswith(ENVELOPE_PREFIX)
    assert encrypted_rec["email"].startswith(ENVELOPE_PREFIX)

    decrypted_rec = encryptor.decrypt_demographics(encrypted_rec)
    assert decrypted_rec == record


def test_envelope_encryption_tamper_detection():
    encryptor = EnvelopeEncryptor(master_key="hospital-master-key-secure-2026")
    token = encryptor.encrypt("Confidential Diagnostic Note")

    # Corrupt the payload
    corrupted = token[:-5] + "AAAAA"
    with pytest.raises(ValueError, match="Failed to decrypt demographic envelope token"):
        encryptor.decrypt(corrupted)


def test_envelope_encryption_wrong_key_fails():
    encryptor1 = EnvelopeEncryptor(master_key="key-alpha-1234567890123456")
    encryptor2 = EnvelopeEncryptor(master_key="key-beta-98765432109876543")

    token = encryptor1.encrypt("Secret Patient Details")
    with pytest.raises(ValueError, match="Failed to decrypt demographic envelope token"):
        encryptor2.decrypt(token)
