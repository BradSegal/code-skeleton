from __future__ import annotations

import pytest

from anatomize.evidence import ProviderRunStatus
from anatomize.research import extract_r_repository
from anatomize.research import r as r_inventory
from anatomize.research.r import RCall, RFunction, RFunctionKind, RNamespaceKind
from anatomize.semantic import (
    LspSemanticArtifact,
    SemanticCapability,
    normalize_lsp_semantic_artifact,
    unavailable_lsp_semantic_envelope,
)
from tests.unit.test_semantic_artifacts import API_SOURCE, CORE_SOURCE, _artifact, _baseline


def _sources() -> dict[str, str]:
    return {
        "DESCRIPTION": """Package: trialtools
Version: 0.2.0
Title: Trial tools
Imports: stats (>= 4.0), readr
Suggests: testthat
Config/testthat/edition: 3
""",
        "NAMESPACE": """export(normalise)
importFrom(stats, median)
S3method(print, trial_result)
""",
        "R/normalise.R": """#' Normalise a value
#' @param x input
#' @export
normalise <- function(x, trim = TRUE) {
  raw <- readr::read_csv("data/input.csv")
  helper <- get("trimws")
  helper(x)
}

print.trial_result <- function(x, ...) {
  print(x$value)
}

setMethod("show", "trial_result", function(object) object)
""",
        "tests/testthat/test-normalise.R": """test_that("normalise trims", {
  local_options(list(warn = 2))
  expect_equal(normalise(" A "), "A")
})
""",
        "analysis.R": "cohort <- readRDS(" + '"private/cohort.rds"' + ")\ndata(mtcars)\n",
    }


def test_r_inventory_covers_package_namespace_functions_calls_tests_docs_and_data() -> None:
    artifact = extract_r_repository(
        _sources(),
        repository_id="repository:trialtools",
        source_state_id="state:r",
    )

    assert artifact.package is not None
    assert artifact.package.package == "trialtools"
    assert artifact.package.dependencies == {
        "imports": ["readr", "stats"],
        "suggests": ["testthat"],
    }
    assert {item.kind for item in artifact.namespace} == {
        RNamespaceKind.EXPORT,
        RNamespaceKind.IMPORT_FROM,
        RNamespaceKind.S3_METHOD,
    }
    functions = {item.name: item for item in artifact.functions}
    assert functions["normalise"].exported is True
    assert functions["normalise"].parameters == ["x", "trim"]
    assert functions["normalise"].roxygen[-1] == "@export"
    assert functions["normalise"].dynamic is True
    assert functions["print.trial_result"].kind is RFunctionKind.S3_METHOD_CANDIDATE
    assert functions["show,trial_result"].kind is RFunctionKind.S4_METHOD_DECLARATION
    assert {item.target for item in artifact.calls} >= {"readr::read_csv", "get", "helper", "print"}
    assert artifact.tests[0].framework == "testthat"
    assert {item.target for item in artifact.data_declarations} == {
        "data/input.csv",
        "private/cohort.rds",
        "mtcars",
    }
    assert any("dynamic R constructs" in item for item in artifact.limitations)


def test_r_and_python_language_identities_do_not_collide() -> None:
    artifact = extract_r_repository(
        {"R/core.R": "answer <- function() 42\n"},
        repository_id="pkg",
        source_state_id="state:mixed",
    )
    r_identity = artifact.functions[0].function_id
    python_identity = "python:pkg.answer@src/pkg/core.py"

    assert r_identity != python_identity
    assert artifact.functions[0].qualified_name == "pkg::answer"


def test_repeated_same_line_r_calls_are_one_relationship_fact() -> None:
    artifact = extract_r_repository(
        {"R/core.R": "answer <- function(x) helper(helper(x))\n"},
        repository_id="pkg",
        source_state_id="state:r",
    )

    helper_calls = [item for item in artifact.calls if item.target == "helper"]
    assert len(helper_calls) == 1
    assert artifact.functions[0].calls == ["helper"]


def test_r_file_failure_is_isolated_and_valid_files_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = r_inventory._parse_r_source

    def fail_one_source(
        source: str,
        *,
        path: str,
        source_state_id: str,
        package_name: str,
        explicit_exports: set[str],
    ) -> tuple[list[RFunction], list[RCall]]:
        if path == "R/bad.R":
            raise ValueError("fixture parse failure")
        return original(
            source,
            path=path,
            source_state_id=source_state_id,
            package_name=package_name,
            explicit_exports=explicit_exports,
        )

    monkeypatch.setattr(r_inventory, "_parse_r_source", fail_one_source)
    artifact = extract_r_repository(
        {
            "R/good.R": "answer <- function() 42\n",
            "R/bad.R": "broken <- function() 0\n",
        },
        repository_id="pkg",
        source_state_id="state:r",
    )

    assert [item.name for item in artifact.functions] == ["answer"]
    assert "R/bad.R: source inventory unavailable (fixture parse failure)" in artifact.limitations


def test_captured_r_lsp_uses_common_semantic_provider_contract() -> None:
    generic = _artifact()
    artifact = LspSemanticArtifact.model_validate(
        generic.model_copy(
            update={
                "languages": ["r"],
                "documents": [item.model_copy(update={"language": "r"}) for item in generic.documents],
                "symbols": [item.model_copy(update={"language": "r"}) for item in generic.symbols],
            }
        ).model_dump(mode="json")
    )
    envelope = normalize_lsp_semantic_artifact(
        artifact,
        expected_state=artifact.source_state,
        baseline=_baseline(),
        sources={"src/api.py": API_SOURCE, "src/core.py": CORE_SOURCE},
        expected_configuration_digest=artifact.configuration_digest,
        provider_id="r.languageserver",
        provider_version="captured/1",
    )

    assert envelope.provider_id == "r.languageserver"
    assert envelope.languages == ["r"]
    assert SemanticCapability.DEFINITIONS.value in envelope.capabilities
    assert envelope.payload.observations


def test_missing_r_tooling_retains_inventory_and_explicit_unavailability() -> None:
    inventory = extract_r_repository(
        {"script.R": "answer <- function() 42\n"},
        repository_id="repository:fixture",
        source_state_id=_artifact().source_state.state_id,
    )
    absent = unavailable_lsp_semantic_envelope(
        expected_state=_artifact().source_state,
        baseline=_baseline(),
        configuration_digest="configuration:r-unavailable",
        requested_paths=["script.R"],
        reason="R languageserver is not installed in the clean consumer environment.",
    )

    assert inventory.functions[0].name == "answer"
    assert absent.status is ProviderRunStatus.UNAVAILABLE
    assert absent.payload.limitations[0].code == "provider_unavailable"
