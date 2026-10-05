import pytest
from fastapi.testclient import TestClient
from helpers import make_env

from filterphone.api.app import create_app


@pytest.fixture
def env(tmp_path):
    svc = make_env(tmp_path)
    with TestClient(create_app(svc)) as client:
        yield svc, client
