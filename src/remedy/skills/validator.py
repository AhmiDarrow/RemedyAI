"""Skill validator -- automated validation and testing of imported skills.

Checks:
- Metadata completeness (required fields present, version is valid SemVer)
- Scripts reference actual files
- Dependencies are declared
- Optional: run the skill's test suite if present
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from packaging.version import Version, InvalidVersion

from remedy.models import Skill, SkillStatus
from remedy.skills.executor import SkillExecutor


class ValidationResult:
    """Aggregated result of a skill validation pass."""

    def __init__(self, skill_name: str) -> None:
        self.skill_name = skill_name
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.test_results: list[dict] = []
        self.passed: bool = False
        self.score: float = 0.0

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class SkillValidator:
    """Validates skills against the agentskills.io specification and optional
    runtime tests."""

    def __init__(self, executor: Optional[SkillExecutor] = None) -> None:
        self.executor = executor or SkillExecutor()

    def validate_metadata(self, skill: Skill) -> ValidationResult:
        """Validate SKILL.md metadata completeness."""
        result = ValidationResult(skill.manifest.name)
        m = skill.manifest

        if not m.name or not m.name.strip():
            result.add_error("Skill name is missing or empty")
        if not m.description or len(m.description) < 10:
            result.add_error("Description must be at least 10 characters")
        if not m.version:
            result.add_error("Version is required")

        try:
            Version(m.version)
        except InvalidVersion:
            result.add_warning(f"Version '{m.version}' is not valid SemVer")

        if m.status == SkillStatus.DISCOVERED:
            result.add_warning("Skill is only in 'discovered' state; activate to use")

        if not skill.instructions or len(skill.instructions) < 20:
            result.add_warning("Skill instructions are very short; consider expanding")

        if m.author is None:
            result.add_warning("No author specified; consider adding one")

        if not m.tags:
            result.add_warning("No tags specified; tags help with discoverability")

        return result

    def validate_dependencies(self, skill: Skill) -> ValidationResult:
        """Check declared Python dependencies."""
        result = ValidationResult(skill.manifest.name)

        for dep in skill.manifest.requires:
            if not dep.strip():
                continue
            # Check if importable (basic check)
            package = dep.split(">=")[0].split("==")[0].split("<")[0].strip()
            try:
                __import__(package)
            except ImportError:
                result.add_warning(f"Dependency '{dep}' not installed in current environment")

        return result

    def validate_scripts(self, skill: Skill) -> ValidationResult:
        """Verify referenced scripts exist on disk."""
        result = ValidationResult(skill.manifest.name)

        if skill.source_skill_dir:
            base = Path(skill.source_skill_dir)
            for script_rel in skill.scripts:
                full = base / script_rel
                if not full.is_file():
                    result.add_error(f"Referenced script does not exist: {script_rel}")

            for ref_rel in skill.references:
                full = base / ref_rel
                if not full.is_file():
                    result.add_warning(f"Referenced file does not exist: {ref_rel}")

        return result

    async def run_tests(self, skill: Skill) -> ValidationResult:
        """Run a skill's test suite if present."""
        result = ValidationResult(skill.manifest.name)

        if not skill.source_skill_dir:
            result.add_warning("No source directory; skipping tests")
            return result

        base = Path(skill.source_skill_dir)
        test_files = list(base.glob("test_*.py")) + list(base.glob("*_test.py"))
        test_files += [base / "tests" / p for p in ["__init__.py", "test_*.py"] if (base / "tests" / p).parent.is_dir()]

        for tf in test_files:
            if tf.is_file():
                exec_result = await self.executor.run_script(tf)
                result.test_results.append({
                    "file": str(tf.relative_to(base)),
                    "success": exec_result.success,
                    "exit_code": exec_result.exit_code,
                    "error": exec_result.error,
                })
                if not exec_result.success:
                    result.add_error(f"Test failed: {tf.name} (exit code {exec_result.exit_code})")

        if not result.test_results:
            result.add_warning("No test files found (looked for test_*.py, *_test.py, tests/)")

        return result

    def compute_score(self, results: list[ValidationResult]) -> float:
        """Aggregate validation results into a 0-1 compliance score."""
        total_checks = len(results)
        if total_checks == 0:
            return 1.0

        passing = sum(1 for r in results if r.is_valid)
        warning_penalty = sum(min(len(r.warnings) * 0.02, 0.3) for r in results)
        return max(0.0, min(1.0, (passing / total_checks) - warning_penalty))
