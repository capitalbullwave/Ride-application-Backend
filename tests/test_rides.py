import pytest


@pytest.mark.anyio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"


@pytest.mark.anyio
async def test_rides_estimate_public_requires_body(client):
    response = await client.post("/api/v1/rides/estimate", json={})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_rides_estimate_with_coords(client):
    response = await client.post(
        "/api/v1/rides/estimate",
        json={
            "pickup_lat": 28.6314,
            "pickup_lng": 77.2167,
            "dropoff_lat": 28.5495,
            "dropoff_lng": 77.2603,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["distance_km"] > 0
    assert len(data["vehicle_types"]) >= 0


@pytest.mark.anyio
async def test_places_search(client):
    response = await client.get("/api/v1/public/places/search", params={"q": "delhi"})
    assert response.status_code == 200
    assert "results" in response.json()
