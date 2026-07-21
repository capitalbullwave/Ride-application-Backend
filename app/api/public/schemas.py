from pydantic import BaseModel, Field


class PlaceSuggestion(BaseModel):
    id: str
    name: str
    address: str
    latitude: float | None = None
    longitude: float | None = None
    source: str = Field(description="google or nominatim")


class PlaceSearchResponse(BaseModel):
    query: str
    results: list[PlaceSuggestion]


class RoutePoint(BaseModel):
    lat: float
    lng: float
    address: str


class LatLngPoint(BaseModel):
    lat: float
    lng: float


class DirectionsResponse(BaseModel):
    pickup: RoutePoint
    dropoff: RoutePoint
    distance_km: float
    duration_min: float
    path: list[LatLngPoint]
    source: str = Field(description="google or osrm")
    stops: list[RoutePoint] = Field(default_factory=list)


class ReverseGeocodeResponse(BaseModel):
    address: str
    latitude: float
    longitude: float
    source: str = Field(description="google or nominatim")


class PlaceDetailsResponse(BaseModel):
    id: str
    name: str
    address: str
    latitude: float
    longitude: float
    source: str = Field(description="google or nominatim")


class AiChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=1200)


class AiChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1200)
    history: list[AiChatMessage] = Field(default_factory=list, max_length=8)


class AiChatResponse(BaseModel):
    reply: str
