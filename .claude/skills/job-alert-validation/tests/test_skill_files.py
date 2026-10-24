from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parents[2]
SKILL_NAMES = ["job-alert-validation", "resume-match"]


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    block = text.split("---\n", 2)[1]
    fields = {}
    for line in block.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter(name: str) -> None:
    fields = read_frontmatter(SKILLS_DIR / name / "SKILL.md")

    assert fields["name"] == name
    assert re.fullmatch(r"[a-z0-9-]+", fields["name"])
    assert 0 < len(fields["description"]) <= 1536


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_referenced_scripts_exist(name: str) -> None:
    skill_dir = SKILLS_DIR / name
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    scripts = set(re.findall(r"\$\{CLAUDE_SKILL_DIR\}/([\w./-]+\.py)", text))

    assert scripts
    for script in scripts:
        assert (skill_dir / script).resolve().is_file(), f"{name} references missing {script}"
