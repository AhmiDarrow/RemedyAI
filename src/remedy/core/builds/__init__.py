"""Machine-native build orchestration (build reducer)."""

from remedy.core.builds.reducer import (
    BuildResult,
    BuildSpec,
    OracleError,
    PytestOracle,
    Signature,
    SymbolRegistry,
    UnitFailure,
    UnitSpec,
    build_project,
    demo_model,
    demo_weak_model,
    extract_markdown_fence,
    local_llama_model,
    materialize,
    run_oracle,
    run_project_tests,
)

__all__ = [
    "BuildResult",
    "BuildSpec",
    "OracleError",
    "Signature",
    "SymbolRegistry",
    "PytestOracle",
    "UnitFailure",
    "UnitSpec",
    "build_project",
    "demo_model",
    "demo_weak_model",
    "extract_markdown_fence",
    "local_llama_model",
    "materialize",
    "run_oracle",
    "run_project_tests",
]
