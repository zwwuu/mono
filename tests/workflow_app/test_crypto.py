from apps.workflow_app.config import Settings
from apps.workflow_app.utils.crypto import decrypt_value, encrypt_value, generate_key, is_encrypted


def test_key_generation():
    key1 = generate_key()
    key2 = generate_key()
    assert key1 != key2
    assert len(key1) > 20


def test_encrypt_and_decrypt_cycle():
    key = generate_key()
    raw_password = "SuperSecretDbPassword#2026!"
    
    # Encrypt
    encrypted = encrypt_value(raw_password, key=key)
    assert encrypted.startswith("ENC(")
    assert encrypted.endswith(")")
    assert is_encrypted(encrypted) is True
    assert raw_password not in encrypted
    
    # Decrypt
    decrypted = decrypt_value(encrypted, key=key)
    assert decrypted == raw_password


def test_is_encrypted_helper():
    assert is_encrypted("ENC(xyz123)") is True
    assert is_encrypted("plain_password") is False
    assert is_encrypted("mysql+pymysql://user:pass@host/db") is False
    assert is_encrypted(None) is False


def test_decrypt_plain_returns_as_is():
    key = generate_key()
    plain = "standard_unencrypted_value"
    assert decrypt_value(plain, key=key) == plain


def test_settings_auto_decrypts():
    test_key = generate_key()
    plain_ldap_pass = "P@ssw0rd_ldap_bind_99"
    encrypted_pass = encrypt_value(plain_ldap_pass, key=test_key)

    # Instantiate Settings directly with an encrypted value in kwargs
    test_settings = Settings(
        APP_ENCRYPTION_KEY=test_key,
        LDAP_BIND_USER_PASSWORD=encrypted_pass
    )
    
    assert test_settings.LDAP_BIND_USER_PASSWORD == plain_ldap_pass
