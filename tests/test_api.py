import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "app" in data


@pytest.mark.asyncio
async def test_user_register_validation(client):
    response = await client.post("/api/v1/auth/user/register", json={
        "email": "invalid-email",
        "phone": "123",
        "password": "short",
        "first_name": "",
        "last_name": "",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unauthorized_access(client):
    response = await client.get("/api/v1/auth/user/me")
    assert response.status_code == 401
