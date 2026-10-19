from datetime import datetime
from pathlib import Path

from careflow.engine import Case, Pathway
from careflow.persistence import User
from careflow.repository import (
    BaseRepository,
    PostgresRepository,
    SQLiteRepository,
    get_repository,
)

PATHWAY = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)


def test_sqlite_repository(tmp_path):
    db_file = tmp_path / "repo_test.sqlite"
    repo = get_repository(db_file)
    assert isinstance(repo, SQLiteRepository)
    assert isinstance(repo, BaseRepository)

    # Add user
    user = User("coord-1", "Test Coordinator", "care_coordinator", created_at=T0)
    repo.add_user(user)
    fetched_user = repo.get_user("coord-1")
    assert fetched_user is not None
    assert fetched_user.role == "care_coordinator"

    # Save and load case
    case = Case("CASE-REPO-1", PATHWAY, T0)
    repo.save_case(case)
    loaded = repo.load_case("CASE-REPO-1", PATHWAY)
    assert loaded.id == "CASE-REPO-1"
    assert loaded.state == "result_issued"

    # Worklist queries
    worklist = repo.get_worklist_cases()
    assert len(worklist) == 1
    assert worklist[0]["id"] == "CASE-REPO-1"
    assert repo.count_worklist_cases() == 1

    repo.verify_readiness()
    repo.close()


def test_postgres_repository_adapter():
    pg_url = "postgresql://user:pass@db.hospital.org:5432/oncology_careflow"
    repo = get_repository(pg_url)
    assert isinstance(repo, PostgresRepository)
    assert repo.host == "db.hospital.org"
    assert repo.port == 5432
    assert repo.database_name == "oncology_careflow"

    user = User("doc-1", "Dr. Smith", "specialist", created_at=T0)
    repo.add_user(user)
    assert repo.get_user("doc-1").role == "specialist"

    case = Case("CASE-PG-1", PATHWAY, T0)
    repo.save_case(case)
    loaded = repo.load_case("CASE-PG-1", PATHWAY)
    assert loaded.id == "CASE-PG-1"

    worklist = repo.get_worklist_cases()
    assert len(worklist) == 1
    assert repo.count_worklist_cases() == 1

    repo.verify_readiness()
    repo.close()


def test_get_repository_env_detection(monkeypatch, tmp_path):
    monkeypatch.setenv("CAREFLOW_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secret@db.internal:5432/careflow_prod")
    repo = get_repository()
    assert isinstance(repo, PostgresRepository)
    assert repo.host == "db.internal"

    monkeypatch.setenv("CAREFLOW_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CAREFLOW_DATABASE_PATH", str(tmp_path / "env.sqlite"))
    repo2 = get_repository()
    assert isinstance(repo2, SQLiteRepository)
