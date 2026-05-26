"""PromptLoader + skill metadata tests."""

from pathlib import Path

from muninn.prompts import PromptLoader, _parse_skill, ANALYSIS_JSON_INSTRUCTION, EXTRACTION_JSON_INSTRUCTION


def test_parse_skill_with_frontmatter(tmp_path):
    skill_file = tmp_path / "test-skill" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill for unit tests.\n---\n\n# Test Skill\n\nBody content here.\n"
    )
    fm, body = _parse_skill(str(skill_file))
    assert fm["name"] == "test-skill"
    assert fm["description"] == "A test skill for unit tests."
    assert "# Test Skill" in body
    assert "---" not in body


def test_parse_skill_no_frontmatter(tmp_path):
    skill_file = tmp_path / "bare" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("# No Frontmatter\n\nJust body.\n")
    fm, body = _parse_skill(str(skill_file))
    assert fm == {}
    assert "# No Frontmatter" in body


def test_skill_returns_body_only(tmp_path):
    skill_file = tmp_path / "my-skill" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("---\nname: my-skill\ndescription: desc\n---\n\nBody only.\n")
    loader = PromptLoader(skills_dir=tmp_path, schema_dir=tmp_path)
    body = loader.skill("my-skill")
    assert "Body only." in body
    assert "---" not in body


def test_skill_meta(tmp_path):
    skill_file = tmp_path / "my-skill" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("---\nname: my-skill\ndescription: A great skill.\n---\n\nBody.\n")
    loader = PromptLoader(skills_dir=tmp_path, schema_dir=tmp_path)
    meta = loader.skill_meta("my-skill")
    assert meta["name"] == "my-skill"
    assert meta["description"] == "A great skill."


def test_skill_meta_missing(tmp_path):
    loader = PromptLoader(skills_dir=tmp_path, schema_dir=tmp_path)
    assert loader.skill_meta("nonexistent") == {}


def test_all_skills(tmp_path):
    for name in ["skill-a", "skill-b", "skill-c"]:
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: desc for {name}\n---\n\nBody.\n")
    # non-skill directory (no SKILL.md)
    (tmp_path / "not-a-skill").mkdir()
    loader = PromptLoader(skills_dir=tmp_path, schema_dir=tmp_path)
    skills = loader.all_skills()
    assert len(skills) == 3
    names = {s["name"] for s in skills}
    assert names == {"skill-a", "skill-b", "skill-c"}
    assert all("_path" in s for s in skills)


def test_analysis_instruction_is_nonempty():
    assert len(ANALYSIS_JSON_INSTRUCTION) > 200
    assert "claims" in ANALYSIS_JSON_INSTRUCTION
    assert "provenance" in ANALYSIS_JSON_INSTRUCTION


def test_extraction_instruction_has_claims():
    assert "claims" in EXTRACTION_JSON_INSTRUCTION
    assert "provenance" in EXTRACTION_JSON_INSTRUCTION
    assert "page" in EXTRACTION_JSON_INSTRUCTION
