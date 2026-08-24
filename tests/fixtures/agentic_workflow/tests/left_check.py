from src.left import normalize


def check_normalize_orders_and_offsets() -> None:
    assert normalize([2, -1, 1]) == [4, 5]
