"""Sample models module for testing.

This module demonstrates various Python constructs that anatomize
should be able to extract.
"""

from dataclasses import dataclass
from typing import Any

# Module-level constant
MAX_SIZE = 100


@dataclass
class SimpleDataclass:
    """A simple dataclass for testing.

    Attributes
    ----------
    name
        The name of the entity.
    value
        The numeric value.
    """

    name: str
    value: int = 0


class BaseModel:
    """A base model class.

    This class demonstrates inheritance and methods.
    """

    def __init__(self, id: int) -> None:
        """Initialize the model.

        Parameters
        ----------
        id
            Unique identifier.
        """
        self.id = id

    def get_id(self) -> int:
        """Return the model's ID.

        Returns
        -------
        int
            The unique identifier.
        """
        return self.id


class DerivedModel(BaseModel):
    """A derived model with additional functionality."""

    def __init__(self, id: int, name: str) -> None:
        """Initialize the derived model.

        Parameters
        ----------
        id
            Unique identifier.
        name
            Model name.
        """
        super().__init__(id)
        self.name = name

    def get_name(self) -> str:
        """Return the model's name."""
        return self.name

    @property
    def display_name(self) -> str:
        """Return display name combining ID and name."""
        return f"{self.id}: {self.name}"


def process_data(items: list[Any], *, max_items: int = 10) -> list[Any]:
    """Process a list of items.

    Parameters
    ----------
    items
        Input items to process.
    max_items
        Maximum number of items to return.

    Returns
    -------
    list[Any]
        Processed items.
    """
    return items[:max_items]


async def fetch_data(url: str) -> dict[str, Any]:
    """Fetch data from a URL asynchronously.

    Parameters
    ----------
    url
        The URL to fetch from.

    Returns
    -------
    dict[str, Any]
        The fetched data.
    """
    return {"url": url, "data": None}
