"""Required vehicle documents per captain vehicle category."""

from __future__ import annotations

# Document type constants
INSURANCE = "INSURANCE"
POLLUTION = "POLLUTION"
PERMIT = "PERMIT"
FITNESS = "FITNESS"
VEHICLE_FRONT = "VEHICLE_FRONT"
VEHICLE_BACK = "VEHICLE_BACK"
VEHICLE_SIDE = "VEHICLE_SIDE"

_VEHICLE_ALIASES: dict[str, str] = {
    "bike": "bike",
    "auto": "auto",
    "e-rickshaw": "e_rickshaw",
    "e_rickshaw": "e_rickshaw",
    "erickshaw": "e_rickshaw",
    "cab": "cab",
    "car": "cab",
    "sedan": "cab",
    "suv": "cab",
}

_REQUIREMENTS: dict[str, list[str]] = {
    "bike": [INSURANCE, VEHICLE_FRONT],
    "auto": [INSURANCE, POLLUTION, PERMIT, VEHICLE_FRONT, VEHICLE_BACK],
    "e_rickshaw": [INSURANCE, POLLUTION, PERMIT, VEHICLE_FRONT, VEHICLE_BACK],
    "cab": [INSURANCE, POLLUTION, PERMIT, FITNESS, VEHICLE_FRONT, VEHICLE_BACK, VEHICLE_SIDE],
}

_LABELS: dict[str, str] = {
    INSURANCE: "Vehicle Insurance",
    POLLUTION: "Pollution Certificate",
    PERMIT: "Commercial Permit",
    FITNESS: "Fitness Certificate",
    VEHICLE_FRONT: "Vehicle Front Photo",
    VEHICLE_BACK: "Vehicle Back Photo",
    VEHICLE_SIDE: "Vehicle Side Photo",
}


def normalize_vehicle_category(vehicle_type_name: str | None) -> str:
    if not vehicle_type_name:
        return "bike"
    key = vehicle_type_name.strip().lower().replace(" ", "_").replace("-", "_")
    return _VEHICLE_ALIASES.get(key, key if key in _REQUIREMENTS else "bike")


def required_document_types(vehicle_type_name: str | None) -> list[str]:
    category = normalize_vehicle_category(vehicle_type_name)
    return list(_REQUIREMENTS.get(category, _REQUIREMENTS["bike"]))


def document_label(doc_type: str) -> str:
    return _LABELS.get(doc_type, doc_type.replace("_", " ").title())


def missing_documents(
    vehicle_type_name: str | None,
    uploaded_types: set[str],
) -> list[str]:
    missing: list[str] = []
    for doc_type in required_document_types(vehicle_type_name):
        if doc_type not in uploaded_types:
            missing.append(doc_type)
    return missing
