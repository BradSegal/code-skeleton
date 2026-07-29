"""Shared test fixtures for anatomize tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from anatomize.core.types import (
    AttributeInfo,
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    PackageInfo,
    ParameterInfo,
    ResolutionLevel,
    Skeleton,
    SkeletonMetadata,
)

if TYPE_CHECKING:
    pass


@pytest.fixture
def minimal_skeleton() -> Skeleton:
    """Create a minimal valid Skeleton for testing."""
    return Skeleton(
        metadata=SkeletonMetadata(
            generator_version="1.0.0",
            sources=["/test/src"],
            resolution=ResolutionLevel.MODULES,
            total_packages=1,
            total_modules=1,
            total_classes=0,
            total_functions=0,
            token_estimate=100,
        ),
        packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["mod"])},
        modules={
            "pkg.mod": ModuleInfo(
                path="pkg/mod.py",
                name="pkg.mod",
                source=0,
                doc="A test module.",
            )
        },
    )


@pytest.fixture
def skeleton_with_classes() -> Skeleton:
    """Create a Skeleton with sample classes and functions."""
    return Skeleton(
        metadata=SkeletonMetadata(
            generator_version="1.0.0",
            sources=["/test/src"],
            resolution=ResolutionLevel.SIGNATURES,
            total_packages=1,
            total_modules=1,
            total_classes=2,
            total_functions=1,
            token_estimate=500,
        ),
        packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["models"])},
        modules={
            "pkg.models": ModuleInfo(
                path="pkg/models.py",
                name="pkg.models",
                source=0,
                doc="Models module.",
                imports=["from dataclasses import dataclass", "from typing import Optional"],
                constants=[
                    AttributeInfo(name="VERSION", line=5, annotation="str", default='"1.0"'),
                ],
                classes=[
                    ClassInfo(
                        name="User",
                        line=10,
                        doc="A user model.",
                        bases=["BaseModel"],
                        decorators=["dataclass"],
                        is_dataclass=True,
                        attributes=[
                            AttributeInfo(name="name", line=11, annotation="str"),
                            AttributeInfo(name="age", line=12, annotation="int", default="0"),
                        ],
                        methods=[
                            FunctionInfo(
                                name="greet",
                                line=14,
                                signature="(self) -> str",
                                returns="str",
                                parameters=[ParameterInfo(name="self", kind="positional_or_keyword")],
                            )
                        ],
                    ),
                    ClassInfo(
                        name="Admin",
                        line=20,
                        bases=["User"],
                        decorators=[],
                        methods=[
                            FunctionInfo(
                                name="promote",
                                line=22,
                                is_async=True,
                                signature="(self, user: User) -> None",
                                decorators=["staticmethod"],
                            )
                        ],
                    ),
                ],
                functions=[
                    FunctionInfo(
                        name="create_user",
                        line=30,
                        signature="(name: str, age: int = 0) -> User",
                        returns="User",
                        parameters=[
                            ParameterInfo(name="name", annotation="str", kind="positional_or_keyword"),
                            ParameterInfo(name="age", annotation="int", default="0", kind="positional_or_keyword"),
                        ],
                    )
                ],
            )
        },
    )


@pytest.fixture
def sample_module_info() -> ModuleInfo:
    """Create a sample ModuleInfo for testing formatters."""
    return ModuleInfo(
        path="pkg/sample.py",
        name="pkg.sample",
        source=0,
        doc="Sample module docstring.",
        imports=["import os", "from typing import List"],
        constants=[
            AttributeInfo(name="CONSTANT", line=5, annotation="int", default="42"),
        ],
        classes=[
            ClassInfo(
                name="SampleClass",
                line=10,
                doc="A sample class.",
                bases=["Base"],
                decorators=["dataclass"],
                attributes=[
                    AttributeInfo(name="value", line=11, annotation="int"),
                ],
                methods=[
                    FunctionInfo(name="method", line=13, signature="(self) -> None"),
                ],
            )
        ],
        functions=[
            FunctionInfo(
                name="sample_func",
                line=20,
                doc="A sample function.",
                signature="(x: int, y: int = 0) -> int",
                returns="int",
                is_async=False,
                decorators=[],
            ),
            FunctionInfo(
                name="async_func",
                line=25,
                signature="() -> None",
                is_async=True,
                decorators=["cached"],
            ),
        ],
    )


@pytest.fixture
def fixtures_path() -> Path:
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"
