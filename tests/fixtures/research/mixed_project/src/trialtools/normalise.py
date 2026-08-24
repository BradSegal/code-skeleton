def normalise_group(value: str) -> str:
    stripped = value.strip()
    folded = stripped.casefold()
    return folded.replace(" ", "-")
