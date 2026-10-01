import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import import_service_module

# Load the real preprocessing-service module
# (services/preprocessing-service/preprocessing_service.py).
preprocessing_mod = import_service_module("preprocessing-service", "preprocessing_service")
PreprocessingService = preprocessing_mod.PreprocessingService


def test_preprocessing_service_public_api():
    """The real module loads and exposes the expected async API.

    The previous version of this test imported a non-existent path
    (services.preprocessing_service.app.services...) and could not even be
    collected. This verifies the actual service surface.
    """
    for method in ("submit_job", "get_job_status", "_upload_config_to_hdfs"):
        assert hasattr(PreprocessingService, method), f"missing {method}"
        assert inspect.iscoroutinefunction(getattr(PreprocessingService, method))


def test_preprocessing_service_wires_collaborators():
    db = AsyncMock()
    settings = MagicMock()
    service = PreprocessingService(db, settings)
    assert service._db is db
    assert service._settings is settings
