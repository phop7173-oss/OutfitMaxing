import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_admin_api_key_must_be_long_enough():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_api_key="short")


def test_admin_key_is_registered_for_log_redaction():
    s = Settings(_env_file=None, admin_api_key="a-long-admin-key-123456")
    assert "a-long-admin-key-123456" in s.secret_values()


def test_admin_key_defaults_to_unset():
    assert Settings(_env_file=None, admin_api_key=None).admin_api_key is None
