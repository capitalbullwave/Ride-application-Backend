"""Google Maps integration service."""
import re
from typing import Optional

import httpx

from app.core.config import settings


class MapsService:
    BASE_URL = "https://maps.googleapis.com/maps/api"
    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    USER_AGENT = "FastBull-RideBooking/1.0 (support@ridebook.com)"

    def __init__(self):
        self.api_key = settings.google_maps_api_key

    @staticmethod
    def _parse_lat_lng_query(value: str) -> Optional[dict]:
        match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", value.strip())
        if not match:
            return None
        lat = float(match.group(1))
        lng = float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        return {
            "lat": lat,
            "lng": lng,
            "formatted_address": value.strip(),
        }

    async def search_places(
        self,
        query: str,
        *,
        limit: int = 8,
        country: str = "in",
    ) -> list[dict]:
        trimmed = query.strip()
        if len(trimmed) < 2:
            return []

        if self.api_key:
            google_results = await self._google_autocomplete(trimmed, limit=limit, country=country)
            if google_results:
                return google_results

        return await self._nominatim_search(trimmed, limit=limit, country=country)

    async def _google_autocomplete(
        self,
        query: str,
        *,
        limit: int,
        country: str,
    ) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/place/autocomplete/json",
                    params={
                        "input": query,
                        "key": self.api_key,
                        "components": f"country:{country}",
                    },
                )
                data = response.json()
        except httpx.HTTPError:
            return []

        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            return []

        predictions = data.get("predictions") or []
        results: list[dict] = []
        for item in predictions[:limit]:
            structured = item.get("structured_formatting") or {}
            results.append(
                {
                    "id": item["place_id"],
                    "name": structured.get("main_text") or item.get("description", ""),
                    "address": item.get("description", ""),
                    "latitude": None,
                    "longitude": None,
                    "source": "google",
                }
            )
        return results

    async def _nominatim_search(
        self,
        query: str,
        *,
        limit: int,
        country: str,
    ) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self.NOMINATIM_URL,
                    params={
                        "q": query,
                        "format": "json",
                        "addressdetails": 1,
                        "countrycodes": country,
                        "limit": limit,
                    },
                    headers={"User-Agent": self.USER_AGENT},
                )
                data = response.json()
        except httpx.HTTPError:
            return []

        if not isinstance(data, list):
            return []

        results: list[dict] = []
        seen: set[str] = set()
        for item in data:
            display_name = item.get("display_name") or ""
            name = item.get("name") or display_name.split(",")[0].strip()
            key = display_name.lower()
            if not display_name or key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "id": f"osm-{item.get('osm_type', 'node')}-{item.get('osm_id', item.get('place_id'))}",
                    "name": name,
                    "address": display_name,
                    "latitude": float(item["lat"]) if item.get("lat") else None,
                    "longitude": float(item["lon"]) if item.get("lon") else None,
                    "source": "nominatim",
                }
            )
        return results[:limit]

    async def resolve_address(self, address: str) -> Optional[dict]:
        trimmed = address.strip()
        if not trimmed:
            return None

        coords = self._parse_lat_lng_query(trimmed)
        if coords:
            return coords

        if self.api_key:
            google = await self._google_geocode(trimmed)
            if google:
                return google

        results = await self._nominatim_search(trimmed, limit=1, country="in")
        if not results:
            return None

        row = results[0]
        if row.get("latitude") is None or row.get("longitude") is None:
            return None

        return {
            "lat": row["latitude"],
            "lng": row["longitude"],
            "formatted_address": row["address"],
        }

    async def get_route_between(self, pickup: str, dropoff: str) -> Optional[dict]:
        origin = await self.resolve_address(pickup)
        destination = await self.resolve_address(dropoff)
        if not origin or not destination:
            return None

        if self.api_key:
            google_route = await self._google_directions_route(
                origin["lat"],
                origin["lng"],
                destination["lat"],
                destination["lng"],
            )
            if google_route:
                return {
                    "pickup": {
                        "lat": origin["lat"],
                        "lng": origin["lng"],
                        "address": origin["formatted_address"],
                    },
                    "dropoff": {
                        "lat": destination["lat"],
                        "lng": destination["lng"],
                        "address": destination["formatted_address"],
                    },
                    "distance_km": google_route["distance_km"],
                    "duration_min": google_route["duration_min"],
                    "path": google_route["path"],
                    "source": "google",
                }

        osrm_route = await self._osrm_route(
            origin["lat"],
            origin["lng"],
            destination["lat"],
            destination["lng"],
        )
        if not osrm_route:
            return None

        return {
            "pickup": {
                "lat": origin["lat"],
                "lng": origin["lng"],
                "address": origin["formatted_address"],
            },
            "dropoff": {
                "lat": destination["lat"],
                "lng": destination["lng"],
                "address": destination["formatted_address"],
            },
            "distance_km": osrm_route["distance_km"],
            "duration_min": osrm_route["duration_min"],
            "path": osrm_route["path"],
            "source": "osrm",
        }

    async def _google_geocode(self, address: str) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/geocode/json",
                    params={"address": address, "key": self.api_key},
                )
                data = response.json()
        except httpx.HTTPError:
            return None

        if data.get("status") != "OK" or not data.get("results"):
            return None

        result = data["results"][0]
        loc = result["geometry"]["location"]
        return {
            "lat": loc["lat"],
            "lng": loc["lng"],
            "formatted_address": result.get("formatted_address", address),
        }

    async def _google_directions_route(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/directions/json",
                    params={
                        "origin": f"{origin_lat},{origin_lng}",
                        "destination": f"{dest_lat},{dest_lng}",
                        "key": self.api_key,
                    },
                )
                data = response.json()
        except httpx.HTTPError:
            return None

        if data.get("status") != "OK" or not data.get("routes"):
            return None

        route = data["routes"][0]
        leg = route["legs"][0]
        return {
            "distance_km": leg["distance"]["value"] / 1000,
            "duration_min": leg["duration"]["value"] / 60,
            "path": self._decode_polyline(route["overview_polyline"]["points"]),
        }

    async def _osrm_route(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> Optional[dict]:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
        )
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    url,
                    params={"overview": "full", "geometries": "geojson", "steps": "false"},
                )
                data = response.json()
        except httpx.HTTPError:
            return None

        if data.get("code") != "Ok" or not data.get("routes"):
            return None

        route = data["routes"][0]
        coordinates = route.get("geometry", {}).get("coordinates") or []
        path = [{"lat": point[1], "lng": point[0]} for point in coordinates if len(point) >= 2]
        if len(path) < 2:
            return None

        return {
            "distance_km": route["distance"] / 1000,
            "duration_min": route["duration"] / 60,
            "path": path,
        }

    @staticmethod
    def _decode_polyline(encoded: str) -> list[dict]:
        coordinates: list[dict] = []
        index = 0
        lat = 0
        lng = 0

        while index < len(encoded):
            shift = 0
            result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta_lat = ~(result >> 1) if result & 1 else (result >> 1)
            lat += delta_lat

            shift = 0
            result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta_lng = ~(result >> 1) if result & 1 else (result >> 1)
            lng += delta_lng

            coordinates.append({"lat": lat / 1e5, "lng": lng / 1e5})

        return coordinates

    async def geocode(self, address: str) -> Optional[dict]:
        return await self.resolve_address(address)

    async def reverse_geocode(self, lat: float, lng: float) -> Optional[str]:
        resolved = await self.reverse_geocode_location(lat, lng)
        return resolved["address"] if resolved else None

    async def reverse_geocode_location(self, lat: float, lng: float) -> Optional[dict]:
        if self.api_key:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{self.BASE_URL}/geocode/json",
                        params={"latlng": f"{lat},{lng}", "key": self.api_key},
                    )
                    data = response.json()
                    if data.get("results"):
                        address = data["results"][0].get("formatted_address")
                        if address:
                            return {
                                "address": address,
                                "latitude": lat,
                                "longitude": lng,
                                "source": "google",
                            }
            except httpx.HTTPError:
                pass

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={"lat": lat, "lon": lng, "format": "json"},
                    headers={"User-Agent": self.USER_AGENT},
                )
                data = response.json()
                display = data.get("display_name")
                if display:
                    return {
                        "address": display,
                        "latitude": lat,
                        "longitude": lng,
                        "source": "nominatim",
                    }
        except httpx.HTTPError:
            pass

        return None

    async def get_place_details(self, place_id: str) -> Optional[dict]:
        trimmed = place_id.strip()
        if not trimmed or trimmed.startswith("osm-"):
            return None

        if not self.api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/place/details/json",
                    params={
                        "place_id": trimmed,
                        "fields": "place_id,name,formatted_address,geometry",
                        "key": self.api_key,
                    },
                )
                data = response.json()
        except httpx.HTTPError:
            return None

        if data.get("status") != "OK":
            return None

        result = data.get("result") or {}
        location = (result.get("geometry") or {}).get("location") or {}
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is None or lng is None:
            return None

        return {
            "id": result.get("place_id", trimmed),
            "name": result.get("name", ""),
            "address": result.get("formatted_address", ""),
            "latitude": float(lat),
            "longitude": float(lng),
            "source": "google",
        }

    async def get_directions(self, origin: str, destination: str) -> Optional[dict]:
        route = await self.get_route_between(origin, destination)
        if not route:
            return None
        return {
            "distance_km": route["distance_km"],
            "duration_min": route["duration_min"],
            "polyline": route["path"],
        }

    async def distance_matrix(self, origins: list, destinations: list) -> Optional[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/distancematrix/json",
                params={
                    "origins": "|".join(origins),
                    "destinations": "|".join(destinations),
                    "key": self.api_key,
                },
            )
            return response.json()
