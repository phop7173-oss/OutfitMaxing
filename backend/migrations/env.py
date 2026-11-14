from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.database.base import Base
from app.modules.products import models as _catalog_models  # noqa: F401  (registers tables)

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
# Functional index managed only in the migration (see migrations/versions/0001_catalog.py).
MANUAL_INDEXES = {"uq_product_variants_combo"}


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    return not (type_ == "index" and name in MANUAL_INDEXES)


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
