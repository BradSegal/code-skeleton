def prepare(values: list[int]) -> list[int]:
    selected = [value for value in values if value > 0]
    ordered = sorted(selected)
    total = sum(ordered)
    return [value + total for value in ordered]
