"""Load SKILL.md files and the schema docs as system prompts.

The Python orchestrator uses the same SKILL.md content as Claude Code / Cursor / etc.
so behavior is consistent regardless of which agent runtime drives the pipeline.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SKILL_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    m = SKILL_FRONTMATTER_RE.match(text)
    return m.group(2) if m else text


@lru_cache(maxsize=64)
def _read(path_str: str) -> str:
    return _strip_frontmatter(Path(path_str).read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def _parse_skill(path_str: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text) from a SKILL.md file."""
    text = Path(path_str).read_text(encoding="utf-8")
    m = SKILL_FRONTMATTER_RE.match(text)
    if m:
        fm = yaml.safe_load(m.group(1)) or {}
        return fm, m.group(2)
    return {}, text


class PromptLoader:
    def __init__(self, skills_dir: Path, schema_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.schema_dir = schema_dir

    def skill(self, name: str) -> str:
        p = self.skills_dir / name / "SKILL.md"
        if not p.exists():
            raise FileNotFoundError(f"Skill not found: {p}")
        return _read(str(p))

    def skill_meta(self, name: str) -> dict[str, Any]:
        """Return parsed frontmatter metadata for a skill."""
        p = self.skills_dir / name / "SKILL.md"
        if not p.exists():
            return {}
        fm, _ = _parse_skill(str(p))
        return fm

    def all_skills(self) -> list[dict[str, Any]]:
        """Return metadata for all available skills (for agent auto-discovery)."""
        result = []
        if not self.skills_dir.exists():
            return result
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                fm, _ = _parse_skill(str(skill_file))
                fm["_path"] = str(skill_file)
                result.append(fm)
        return result

    def schema(self, name: str) -> str:
        p = self.schema_dir / f"{name}.md"
        if not p.exists():
            return ""
        return _read(str(p))

    def system_for_analysis(self) -> str:
        """System prompt for Pass 1 (chain-of-thought analysis)."""
        return (
            "# Agent role\n"
            "You are the Muninn analysis agent. Your job is to deeply analyze a source document "
            "and identify all knowledge worth extracting: entities, concepts, claims, relationships, "
            "contradictions, and uncertainties.\n\n"
            "Think carefully about each claim's evidence quality. Distinguish between:\n"
            "- extracted: directly stated in the source with evidence\n"
            "- inferred: you synthesized this from the source but it's not directly stated\n"
            "- ambiguous: the source is unclear or contradicts other knowledge\n\n"
            "Output JSON only when asked."
        )

    def system_for_ingest(self) -> str:
        """Compose the ingest system prompt: the ingest skill only."""
        parts = [
            "# Agent role",
            "You are the Muninn ingest agent. Output JSON only when asked.",
            "",
            "# The ingest protocol",
            self.skill("muninn-ingest"),
        ]
        return "\n".join(parts)

    def system_for_query(self) -> str:
        return (
            "You are Muninn, a knowledge assistant. Answer using the wiki context provided.\n\n"
            "- Write natural prose with [[Page Name]] woven into sentences.\n"
            "- Place a Sources section at the end listing pages you drew from.\n"
            "- When the wiki lacks coverage, say so and suggest ingesting a relevant source.\n"
            "- Source-summary pages (dated like 2026-05-24-browser-history-...) record reading history.\n"
        )


# ---- Pass 1: analysis JSON (chain-of-thought reasoning about the source) ----

ANALYSIS_JSON_INSTRUCTION = """Analyze this source carefully. Think step by step about what knowledge it contains.

Return a JSON object with this shape:

{
  "source_type_assessment": "<what kind of content is this? article, paper, social post, documentation, etc.>",
  "main_thesis": "<the central argument or topic, 1-2 sentences>",
  "entities_identified": [
    {
      "name": "<canonical name>",
      "kind": "person|org",
      "role_in_source": "<author, subject, mentioned, etc.>",
      "key_facts": ["<specific fact from the source>"]
    }
  ],
  "concepts_identified": [
    {
      "name": "<canonical name>",
      "what_it_is": "<brief definition>",
      "why_it_matters": "<relevance to the source's thesis>"
    }
  ],
  "claims": [
    {
      "statement": "<a specific factual claim made in the source>",
      "evidence": "<what evidence supports this? quote, data, citation, none>",
      "confidence": "high|medium|low",
      "provenance": "extracted|inferred|ambiguous",
      "entities_involved": ["<names of entities this claim is about>"]
    }
  ],
  "relationships": [
    {
      "source": "<entity/concept name>",
      "target": "<entity/concept name>",
      "type": "<relationship type>",
      "evidence": "<basis for this relationship>"
    }
  ],
  "contradictions": ["<claims that contradict common knowledge or other wiki content>"],
  "uncertainties": ["<things the source is vague about or that need verification>"],
  "schema_assessment": "<category path: tech/ai, finance/equities, etc.>"
}

Be thorough. Identify ALL entities, concepts, and claims. For each claim, explicitly assess the evidence quality. Output JSON only."""


# ---- Pass 2: extraction JSON (structured output using Pass 1 analysis) ----

EXTRACTION_JSON_INSTRUCTION = """Use your prior analysis (above) to produce richer, more specific extractions. Every claim from your analysis should be reflected in the entity/concept context fields or the page_body.

Return ONLY a JSON object with this exact shape:

{
  "summary": "<1-sentence summary>",
  "schema": "<category path: tech/ai, finance/equities, sports/cricket, personal/health, etc. 2-3 levels, lowercase, slash-separated.>",
  "entities": [
    { "name": "<canonical name>", "kind": "person|org", "context": "<1-2 paragraphs: who they are, what they did in this source, why it matters. Use [[Page Name]] wikilinks to reference other entities/concepts. Include specific facts, titles, roles, numbers. Write 200-400 words.>" }
  ],
  "concepts": [
    { "name": "<canonical name>", "context": "<1-2 paragraphs: what it is, how it works, why it matters. Use [[Page Name]] wikilinks to reference related concepts/entities. Include specific details, numbers, examples. Write 200-400 words.>" }
  ],
  "topics": [
    { "name": "<topic name>", "context": "<1-2 paragraphs: what this topic covers, key takeaways, connections to other ideas. Use [[Page Name]] wikilinks to reference related pages. Write 200-400 words.>" }
  ],
  "relationships": [
    { "source": "<name>", "target": "<name>", "type": "<supplies_to|competes_with|founded_by|works_at|part_of|uses>" }
  ],
  "claims": [
    {
      "statement": "<specific factual claim>",
      "confidence": "high|medium|low",
      "provenance": "extracted|inferred|ambiguous",
      "page": "<name of the entity/concept/topic this claim belongs to>",
      "evidence": "<brief description of supporting evidence>"
    }
  ],
  "open_questions": ["<question raised by the source>"],
  "log_summary": "<one-line, ≤140 chars>",
  "page_body": "<2-4 paragraphs for your future self. Include: (1) core insight and why it matters, (2) specific numbers/examples, (3) what to do differently or how it connects to broader themes. Use [[Page Name]] wikilinks for any existing pages you reference. Prose, not bullets. ≤600 words.>"
}

IMPORTANT: The "context" field for each entity/concept/topic is the MAIN CONTENT of its wiki page. Write substantive prose — not a one-line label. Include specific facts, numbers, quotes, and insights from the source. A good context reads like a mini-article.

EXAMPLE of a good entity context (this is the quality bar):
{ "name": "Jensen Huang", "kind": "person", "context": "Jensen Huang is the co-founder and CEO of [[NVIDIA]], who has led the company's transformation from a graphics chip maker into the dominant force in AI infrastructure. At the 2026 GTC keynote, he announced the Blackwell Ultra architecture delivering 2.5x inference throughput over Hopper, with pricing starting at $30,000 per GPU. He emphasized that 'the age of AI infrastructure is here' and projected $1 trillion in data center spending over the next decade. His leadership style — hands-on technical involvement combined with aggressive supply chain partnerships with [[TSMC]] and [[Samsung]] — has been credited with NVIDIA's 800% stock appreciation since 2023. Critics note the company's growing monopoly in AI training hardware, with over 80% market share in enterprise GPU compute." }

If any category is empty, return an empty array. Output JSON only, no prose."""
