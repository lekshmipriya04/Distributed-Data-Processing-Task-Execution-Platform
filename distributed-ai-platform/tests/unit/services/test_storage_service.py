import sys
import time
import uuid

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock

from shared.common.config import get_base_settings
from tests.conftest import import_service_module

# Load the real storage-service modules (services/storage-service/*.py). Import
# ``routes`` exactly once: it pulls in dataset_service and models, and importing
# ``models`` a second time would redefine the ``datasets`` table on the shared
# SQLAlchemy metadata.
routes_mod = import_service_module("storage-service", "routes")
DatasetService = routes_mod.DatasetService
dataset_service_mod = sys.modules[DatasetService.__module__]
Dataset = dataset_service_mod.Dataset


class _FakeUploadFile:
    """Minimal stand-in for FastAPI's UploadFile.

    DatasetService.create_dataset only reads ``.filename`` and passes ``.file``
    straight to the (mocked) HDFS service, so this is enough.
    """

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = content


@pytest.mark.asyncio
async def test_create_dataset_success():
    db_mock = AsyncMock()
    # session.add() is synchronous in SQLAlchemy; keep it a plain mock so it
    # does not create an un-awaited coroutine.
    db_mock.add = MagicMock()
    hdfs_mock = AsyncMock()

    service = DatasetService(db_mock, hdfs_mock)

    upload = _FakeUploadFile("test.csv", b"header1,header2\n1,2")

    result = await service.create_dataset(
        file=upload,
        name="test_dataset",
        description="A test dataset",
        created_by="test_user",
    )

    assert result.name == "test_dataset"
    assert result.file_format == "csv"
    assert result.hdfs_path == f"/platform/raw/{result.id}.csv"
    assert result.created_by == "test_user"
    # The file object is uploaded to HDFS and the record is committed.
    hdfs_mock.upload_file.assert_awaited_once()
    db_mock.add.assert_called_once()
    db_mock.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_dataset_rejects_unsupported_extension():
    from shared.common.exceptions import ValidationError

    service = DatasetService(AsyncMock(), AsyncMock())
    upload = _FakeUploadFile("notes.txt", b"nope")

    with pytest.raises(ValidationError):
        await service.create_dataset(
            file=upload,
            name="bad",
            description=None,
            created_by=None,
        )


# ---------------------------------------------------------------------------
# Owner scoping in DatasetService (real queries against in-memory SQLite)
# ---------------------------------------------------------------------------

ALICE_ID = uuid.uuid4()
ALICE_ID_2 = uuid.uuid4()
BOB_ID = uuid.uuid4()


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Dataset.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                Dataset(id=ALICE_ID, name="a1", hdfs_path=f"/platform/raw/{ALICE_ID}.csv",
                        file_format="csv", created_by="alice", validation_status="pending"),
                Dataset(id=ALICE_ID_2, name="a2", hdfs_path=f"/platform/raw/{ALICE_ID_2}.csv",
                        file_format="csv", created_by="alice", validation_status="pending"),
                Dataset(id=BOB_ID, name="b1", hdfs_path=f"/platform/raw/{BOB_ID}.csv",
                        file_format="csv", created_by="bob", validation_status="pending"),
            ]
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_dataset_only_returns_owned(session_factory):
    async with session_factory() as session:
        service = DatasetService(session, AsyncMock())
        assert (await service.get_dataset(ALICE_ID, "alice")).name == "a1"
        assert await service.get_dataset(ALICE_ID, "bob") is None
        assert await service.get_dataset(uuid.uuid4(), "alice") is None


@pytest.mark.asyncio
async def test_list_datasets_scoped_to_owner(session_factory):
    async with session_factory() as session:
        service = DatasetService(session, AsyncMock())
        items, total = await service.list_datasets("alice")
        assert total == 2
        assert {d.name for d in items} == {"a1", "a2"}

        items, total = await service.list_datasets("bob")
        assert total == 1
        assert [d.name for d in items] == ["b1"]

        items, total = await service.list_datasets("mallory")
        assert (items, total) == ([], 0)


@pytest.mark.asyncio
async def test_download_dataset_rejects_other_owner(session_factory):
    from shared.common.exceptions import ValidationError

    hdfs = AsyncMock()
    hdfs.download_file.return_value = b"x,y\n1,2"
    async with session_factory() as session:
        service = DatasetService(session, hdfs)
        content, fmt = await service.download_dataset(BOB_ID, "bob")
        assert (content, fmt) == (b"x,y\n1,2", "csv")
        hdfs.download_file.assert_awaited_once_with(f"/platform/raw/{BOB_ID}.csv")

        hdfs.download_file.reset_mock()
        with pytest.raises(ValidationError):
            await service.download_dataset(BOB_ID, "alice")
        hdfs.download_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_dataset_rejects_other_owner(session_factory):
    hdfs = AsyncMock()
    async with session_factory() as session:
        service = DatasetService(session, hdfs)
        assert await service.delete_dataset(BOB_ID, "alice") is False
        hdfs.delete_file.assert_not_awaited()

        assert await service.delete_dataset(BOB_ID, "bob") is True
        hdfs.delete_file.assert_awaited_once_with(f"/platform/raw/{BOB_ID}.csv")

    async with session_factory() as session:
        service = DatasetService(session, AsyncMock())
        assert await service.get_dataset(BOB_ID, "bob") is None
        # Alice's data is untouched.
        _, total = await service.list_datasets("alice")
        assert total == 2


# ---------------------------------------------------------------------------
# Owner resolution: jwt.sub, or X-On-Behalf-Of for internal tokens only
# ---------------------------------------------------------------------------

def test_resolve_owner_uses_sub_for_regular_tokens():
    assert routes_mod.resolve_owner_id({"sub": "alice"}, None) == "alice"


def test_resolve_owner_ignores_header_for_non_internal_tokens():
    assert routes_mod.resolve_owner_id({"sub": "alice"}, "bob") == "alice"
    # Only the literal boolean claim counts, not a truthy string.
    assert routes_mod.resolve_owner_id({"sub": "alice", "internal": "true"}, "bob") == "alice"


def test_resolve_owner_honours_header_for_internal_tokens():
    user = {"sub": "ssh-executor", "internal": True}
    assert routes_mod.resolve_owner_id(user, "bob") == "bob"
    # Internal token without the header acts as itself.
    assert routes_mod.resolve_owner_id(user, None) == "ssh-executor"
    assert routes_mod.resolve_owner_id(user, "  ") == "ssh-executor"


def test_resolve_owner_requires_subject():
    with pytest.raises(HTTPException) as exc:
        routes_mod.resolve_owner_id({}, None)
    assert exc.value.status_code == 401


class _RecordingService:
    """Fake DatasetService that records which owner each call was scoped to."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def list_datasets(self, owner_id, skip=0, limit=100):
        self.calls.append(("list", owner_id))
        return [], 0

    async def get_dataset(self, dataset_id, owner_id):
        self.calls.append(("get", owner_id))
        return None

    async def download_dataset(self, dataset_id, owner_id):
        from shared.common.exceptions import ValidationError

        self.calls.append(("download", owner_id))
        raise ValidationError("Dataset not found")

    async def delete_dataset(self, dataset_id, owner_id):
        self.calls.append(("delete", owner_id))
        return False


def _token(sub: str, **claims) -> str:
    settings = get_base_settings()
    now = int(time.time())
    payload = {"sub": sub, "iat": now, "exp": now + 300, **claims}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@pytest.fixture
def api():
    fake = _RecordingService()
    app = FastAPI()
    app.include_router(routes_mod.router, prefix="/api/v1/storage")
    app.dependency_overrides[routes_mod.get_dataset_service] = lambda: fake
    with TestClient(app) as client:
        yield client, fake


def test_routes_scope_every_read_and_delete_to_caller(api):
    client, fake = api
    headers = {"Authorization": f"Bearer {_token('alice')}"}
    ds = uuid.uuid4()

    assert client.get("/api/v1/storage/datasets", headers=headers).status_code == 200
    assert client.get(f"/api/v1/storage/datasets/{ds}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/storage/datasets/{ds}/download", headers=headers).status_code == 404
    assert client.delete(f"/api/v1/storage/datasets/{ds}", headers=headers).status_code == 404

    assert fake.calls == [
        ("list", "alice"), ("get", "alice"), ("download", "alice"), ("delete", "alice"),
    ]


def test_routes_ignore_on_behalf_of_for_user_tokens(api):
    client, fake = api
    headers = {"Authorization": f"Bearer {_token('alice')}", "X-On-Behalf-Of": "bob"}
    client.get("/api/v1/storage/datasets", headers=headers)
    client.get(f"/api/v1/storage/datasets/{uuid.uuid4()}/download", headers=headers)
    assert fake.calls == [("list", "alice"), ("download", "alice")]


def test_routes_honour_on_behalf_of_for_internal_tokens(api):
    from shared.common.auth import create_internal_token

    client, fake = api
    headers = {
        "Authorization": f"Bearer {create_internal_token('ssh-executor')}",
        "X-On-Behalf-Of": "bob",
    }
    client.get(f"/api/v1/storage/datasets/{uuid.uuid4()}", headers=headers)
    client.get(f"/api/v1/storage/datasets/{uuid.uuid4()}/download", headers=headers)
    assert fake.calls == [("get", "bob"), ("download", "bob")]
