import pytest
from pydantic import ValidationError

from app.core.config import DEV_DB_URL, ENV_FILES, Settings


def test_policies_have_no_hardcoded_defaults():
    s = Settings(_env_file=None)
    assert s.policy_return_window_days is None
    assert s.policy_preorder_deposit_percent is None
    assert s.policy_cod_max_order_mmk is None
    assert s.policies_approved_for_launch is False


def test_reservation_ttl_default_is_15():
    assert Settings(_env_file=None).reservation_ttl_minutes == 15


def test_production_rejects_default_database_url():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", database_url=DEV_DB_URL)


def test_production_accepts_custom_database_url():
    s = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+psycopg://shop:S3cure@db:5432/shop",
    )
    assert s.app_env == "production"


def test_deposit_percent_bounds():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, policy_preorder_deposit_percent=150)


def test_production_rejects_example_credentials_in_database_url():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+psycopg://outfitmaxing:change-me@db:5432/outfitmaxing",
        )


def test_secret_values_collects_configured_secrets_and_url_passwords():
    s = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://shop:DbPw-12345@db:5432/shop",
        redis_url="redis://:RedisPw-9999@redis:6379/0",
        ai_api_key="ai-key-abcdef",
        telegram_bot_token="123456789:tok-abcdefghijklmnopqrstuv",
        telegram_webhook_secret="hook-secret-1",
    )
    values = s.secret_values()
    for expected in (
        "DbPw-12345",
        "RedisPw-9999",
        "ai-key-abcdef",
        "123456789:tok-abcdefghijklmnopqrstuv",
        "hook-secret-1",
    ):
        assert expected in values


def test_env_files_are_absolute_so_commands_work_from_any_directory():
    assert all(p.is_absolute() for p in ENV_FILES)
    assert ENV_FILES[0].name == ".env"


def test_empty_values_in_env_file_mean_unset(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AI_DAILY_REQUEST_LIMIT=\nAI_PER_USER_HOURLY_LIMIT=\nTELEGRAM_BOT_TOKEN=\n")
    s = Settings(_env_file=env)
    assert s.ai_daily_request_limit is None
    assert s.ai_per_user_hourly_limit is None
    assert s.telegram_bot_token is None


def test_env_example_is_valid_settings():
    example = ENV_FILES[0].parent / ".env.example"
    s = Settings(_env_file=example)
    assert s.policies_approved_for_launch is False
    assert s.policy_return_window_days is None  # proposals stay commented out
