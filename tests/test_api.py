from fastapi.testclient import TestClient
from app.config import settings
settings.app_api_key="test-secret"
from app.main import app

client=TestClient(app)

def test_health():
    assert client.get("/health").json()["status"]=="ok"

def test_api_key_required():
    assert client.get("/suppliers").status_code==401
