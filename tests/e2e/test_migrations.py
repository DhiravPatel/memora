"""The migration must produce exactly the schema the models expect.

Runs ``alembic upgrade head`` against the test database and asserts autogenerate finds
no difference, so a model change that nobody wrote a migration for fails here rather
than in production.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from tests.conftest import database_required

from common.settings import get_settings
from database import models  # noqa: F401  (registers the tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]


@pytest.fixture
def clean_database():
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.drop_all(connection)
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    yield engine
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    engine.dispose()


def test_migration_matches_the_models(clean_database):
    config = Config("alembic.ini")
    os.environ["DATABASE_URL"] = get_settings().database_url
    command.upgrade(config, "head")

    with clean_database.connect() as connection:
        differences = [
            difference
            for difference in compare_metadata(
                MigrationContext.configure(connection), Base.metadata
            )
            if "alembic_version" not in str(difference)
        ]
    assert differences == [], f"schema drift: {differences}"


def test_migration_creates_the_vector_and_full_text_indexes(clean_database):
    command.upgrade(Config("alembic.ini"), "head")
    with clean_database.connect() as connection:
        indexes = {
            row[0]
            for row in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
        }
    assert "ix_embeddings_vector" in indexes
    assert "ix_memories_content_fts" in indexes
