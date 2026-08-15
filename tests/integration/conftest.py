"""Environment for integration tests: they talk to real services.

The root conftest strips `IPA_*` variables so unit tests stay hermetic; this
conftest re-points them at the local compose services afterwards. Values
already present in the environment (CI provides them at the job level) win.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

DEFAULT_ENDPOINTS = {
    "IPA_S3_ENDPOINT": "http://localhost:9000",
    "IPA_S3_ACCESS_KEY": "minioadmin",
    "IPA_S3_SECRET_KEY": "minioadmin",
    "IPA_S3_BUCKET": "ipa-documents",
}


@pytest.fixture(autouse=True)
def integration_endpoints() -> Iterator[None]:
    """Restore compose-service endpoints for integration tests.

    Yields:
        None, with infrastructure endpoints present in the environment.
    """
    for name, default in DEFAULT_ENDPOINTS.items():
        os.environ.setdefault(name, default)
    yield
