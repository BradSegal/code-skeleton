import json
import os
from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import ResolutionLevel
from anatomize.formats import OutputFormat, write_skeleton
from anatomize.generators.main import SkeletonGenerator

pytestmark = pytest.mark.integration


def test_write_json_includes_schemas_and_relative_paths(tmp_path: Path) -> None:
    root = Path(__file__).parent.parent / "fixtures" / "project_src" / "src"
    out = tmp_path / "out"

    gen = SkeletonGenerator(sources=[root], exclude=["excluded"], symlinks=SymlinkPolicy.FORBID, workers=0)
    skeleton = gen.generate(level=ResolutionLevel.MODULES)
    write_skeleton(skeleton, out, formats=[OutputFormat.JSON])

    assert (out / "schemas" / "hierarchy.schema.json").exists()
    assert (out / "schemas" / "module.schema.json").exists()
    assert (out / "manifest.json").exists()

    hierarchy = json.loads((out / "hierarchy.json").read_text(encoding="utf-8"))
    assert hierarchy["$schema"] == "./schemas/hierarchy.schema.json"
    sources = hierarchy["metadata"]["sources"]
    assert isinstance(sources, list)
    assert len(sources) == 1
    assert sources[0] == Path(os.path.relpath(root.resolve(), out.resolve())).as_posix()

    pkg = json.loads((out / "modules" / "pkg.json").read_text(encoding="utf-8"))
    assert pkg["$schema"] == "../schemas/module.schema.json"
    mod = pkg["modules"]["mod"]
    assert mod["path"] == "pkg/mod.py"
    assert mod["source"] == 0
