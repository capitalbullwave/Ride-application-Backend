def normalize_phone(phone: str) -> str:
    cleaned = "".join(c for c in phone if c.isdigit() or c == "+")
    if cleaned.startswith("+91"):
        return cleaned
    if cleaned.startswith("91") and len(cleaned) == 12:
        return f"+{cleaned}"
    if len(cleaned) == 10:
        return f"+91{cleaned}"
    return cleaned


def phone_lookup_variants(phone: str) -> list[str]:
    """Return common stored formats for the same Indian mobile number."""
    normalized = normalize_phone(phone)
    digits = "".join(c for c in normalized if c.isdigit())
    variants: list[str] = [normalized]

    if digits.startswith("91") and len(digits) == 12:
        local = digits[2:]
        variants.extend([f"+{digits}", digits, local])
    elif len(digits) == 10:
        variants.extend([f"+91{digits}", f"91{digits}", digits])
    elif digits:
        variants.append(digits)
        if not normalized.startswith("+"):
            variants.append(f"+{digits}")

    seen: set[str] = set()
    ordered: list[str] = []
    for value in variants:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def format_phone_display(phone: str, dial_code: str = "+91") -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) >= 10:
        local = digits[-10:]
        return f"{dial_code} {local[:5]} {local[5:]}"
    return f"{dial_code} {phone}"
