from typing import Any, Optional

from pydantic import BaseModel, model_validator

from apps.workflow_app.utils.crypto import decrypt_value, is_encrypted


class Settings(BaseModel):
    # General App Settings
    APP_NAME: str = "Workflow Portal"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "super-secret-workflow-key-change-in-production-123456"

    # Secret Encryption Settings (for ENC(...) values in .env)
    APP_ENCRYPTION_KEY: Optional[str] = None
    APP_ENCRYPTION_KEY_FILE: Optional[str] = None

    # Database Settings
    # Default to SQLite for easy local setup, or MySQL:
    # mysql+pymysql://workflow_user:secretpassword@localhost:3306/workflow_db
    DATABASE_URL: str = "sqlite:///./workflow.db"

    # LDAP Authentication Settings
    LDAP_SERVER: str = "ldap://127.0.0.1:389"
    LDAP_BASE_DN: str = "dc=example,dc=com"
    LDAP_USER_DN_TEMPLATE: str = "uid={username},ou=users,dc=example,dc=com"
    LDAP_SEARCH_FILTER: str = "(uid={username})"
    LDAP_BIND_USER_DN: Optional[str] = None
    LDAP_BIND_USER_PASSWORD: Optional[str] = None
    LDAP_USE_SSL: bool = False
    
    # When enabled, allows instant login with mock demo credentials without a real LDAP server.
    # Set to False in production once your real LDAP is configured.
    LDAP_MOCK_ENABLED: bool = True

    # Email / Mailing Notification Settings
    MAIL_ENABLED: bool = False  # Set to True when production SMTP is configured
    MAIL_SERVER: str = "smtp.example.com"
    MAIL_PORT: int = 587
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = "workflow-system@example.com"
    MAIL_FROM_NAME: str = "Workflow Notification Center"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    # Session Settings
    SESSION_COOKIE_NAME: str = "workflow_session"
    SESSION_MAX_AGE_SECONDS: int = 86400 * 7  # 7 days

    @model_validator(mode="before")
    @classmethod
    def decrypt_secrets(cls, data: Any) -> Any:
        """
        Inspect all configuration values loaded from .env or environment variables.
        Any string wrapped in ENC(...) will automatically be decrypted using APP_ENCRYPTION_KEY
        or the .secret.key file before assignment to Settings attributes.
        """
        if isinstance(data, dict):
            # Prefer explicit APP_ENCRYPTION_KEY passed in data if present
            explicit_key = data.get("APP_ENCRYPTION_KEY")
            for key, val in list(data.items()):
                if isinstance(val, str) and is_encrypted(val):
                    data[key] = decrypt_value(val, key=explicit_key)
        return data


settings = Settings()
