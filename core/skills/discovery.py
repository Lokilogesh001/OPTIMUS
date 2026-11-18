r"""core/skills/discovery.py — Multi-Root Skill Discovery Engine for OPTIMUS.

Discovers skills across configured ecosystem directories:
- ECC skills (`C:/Users/loges/ECC/skills`)
- gstack workflows & skills (`C:/Users/loges/.antigravity/skills/gstack/gstack-source`)
- User / Agent skills (`C:/Users/loges/.agents/skills`)
- Anti-Slop, Chisle & UI skills (`C:/Users/loges/.gemini/config/skills`)
- Built-in Antigravity skills (`C:/Users/loges/.gemini/antigravity/builtin/skills`)

Supports:
- YAML frontmatter in `SKILL.md`
- Standalone markdown skill specifications
- JSON skill manifests
- Non-destructive parsing (zero execution during discovery)
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("optimus.skills.discovery")

from core.skills.models import (
    SkillCategory,
    SkillManifest,
    SkillType,
)

DEFAULT_SEARCH_ROOTS: list[tuple[str, str]] = [
    (r"C:\Users\loges\.gemini\config\skills", "antislop_chisle_ui"),
    (r"C:\Users\loges\.agents\skills", "agents_skills"),
    (r"C:\Users\loges\.antigravity\skills\gstack\gstack-source", "gstack"),
    (r"C:\Users\loges\ECC\skills", "ecc"),
    (r"C:\Users\loges\.gemini\antigravity\builtin\skills", "builtin"),
]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter between '---' markers and return metadata and body."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        # Fallback: check if first line is markdown title
        return {}, text

    frontmatter_str = m.group(1)
    body = m.group(2).strip()

    metadata: dict[str, Any] = {}
    for line in frontmatter_str.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition(":")
        if sep:
            key = k.strip().lower()
            val = v.strip().strip("'\"")
            metadata[key] = val

    return metadata, body


def infer_category(name: str, description: str, provenance: str) -> SkillCategory:
    """Infer semantic skill category from name, description, and provenance."""
    combined = f"{name} {description} {provenance}".lower()

    if any(k in combined for k in ("security", "injection", "threat", "cso", "audit", "skillspector", "guard", "careful")):
        return SkillCategory.SECURITY
    if any(k in combined for k in ("slop", "quality", "clean", "hygiene", "regression", "review")):
        return SkillCategory.QUALITY
    if any(k in combined for k in ("ui", "layout", "color", "typography", "design", "css", "interface", "figma", "accessibility")):
        return SkillCategory.UI_DESIGN
    if any(k in combined for k in ("plan", "ceo", "architecture", "office-hours", "brainstorm", "autoplan")):
        return SkillCategory.PLANNING
    if any(k in combined for k in ("workflow", "ship", "deploy", "canary", "release", "swarm")):
        return SkillCategory.WORKFLOW
    if any(k in combined for k in ("code", "tdd", "dev", "debug", "ast", "python", "test", "ecc", "chisle")):
        return SkillCategory.ENGINEERING
    return SkillCategory.GENERAL


class SkillDiscoveryEngine:
    """Discovers, parses, and manifests skills from local ecosystem directories."""

    def __init__(self, search_roots: list[tuple[str | Path, str]] | None = None) -> None:
        self.search_roots = search_roots or DEFAULT_SEARCH_ROOTS

    def discover_all(self, max_skills: int = 200) -> list[SkillManifest]:
        """Scan all configured discovery roots and return discovered skill manifests."""
        manifests: list[SkillManifest] = []
        seen_ids: set[str] = set()

        for root_path_str, provenance in self.search_roots:
            p = Path(root_path_str)
            if not p.is_dir():
                continue

            found = self.discover_directory(p, provenance=provenance, max_skills=max_skills - len(manifests))
            for m in found:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    manifests.append(m)
                if len(manifests) >= max_skills:
                    return manifests

        return manifests

    def discover_directory(
        self,
        directory: Path | str,
        provenance: str = "local",
        max_skills: int = 100,
    ) -> list[SkillManifest]:
        """Scan a single directory for subdirectories containing SKILL.md or manifest files."""
        dir_path = Path(directory)
        manifests: list[SkillManifest] = []

        if not dir_path.is_dir():
            return manifests

        # Check if dir_path itself is a skill
        self_skill_md = dir_path / "SKILL.md"
        if self_skill_md.is_file():
            m = self.parse_skill_folder(dir_path, provenance=provenance)
            if m:
                manifests.append(m)
                return manifests

        # Iterate child folders
        try:
            for item in dir_path.iterdir():
                if not item.is_dir() or item.name.startswith("."):
                    continue

                skill_md = item / "SKILL.md"
                if skill_md.is_file():
                    m = self.parse_skill_folder(item, provenance=provenance)
                    if m:
                        manifests.append(m)
                if len(manifests) >= max_skills:
                    break
        except Exception as e:
            logger.debug("Error during skill discovery walk: %s", e)

        return manifests

    def parse_skill_folder(self, folder: Path, provenance: str = "local") -> SkillManifest | None:
        """Parse a skill directory containing SKILL.md into a SkillManifest."""
        skill_file = folder / "SKILL.md"
        if not skill_file.is_file():
            return None

        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
            meta, _body = parse_frontmatter(text)

            skill_id = str(meta.get("name") or folder.name).lower().strip()
            # Clean skill_id
            skill_id = re.sub(r"[^a-z0-9_-]", "-", skill_id)

            display_name = str(meta.get("name") or folder.name.replace("-", " ").title())
            description = str(meta.get("description") or f"OPTIMUS {display_name} capability.")

            # Compute sha256 of instruction text
            content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

            # Check for executable scripts or subfolders
            has_scripts = (folder / "scripts").is_dir() or any(folder.glob("*.py"))
            skill_type = SkillType.EXECUTABLE if has_scripts else SkillType.INSTRUCTION

            # Detect required tools mentioned in frontmatter or text
            required_tools: list[str] = []
            if "tools" in meta:
                raw_tools = meta["tools"]
                if isinstance(raw_tools, str):
                    required_tools = [t.strip() for t in raw_tools.split(",") if t.strip()]

            # Category inference
            category = infer_category(skill_id, description, provenance)

            # Derive tags
            tags = [skill_id, provenance, category.value.lower()]
            if has_scripts:
                tags.append("scripts")

            return SkillManifest(
                id=skill_id,
                name=display_name,
                description=description,
                version=str(meta.get("version", "1.0.0")),
                author=str(meta.get("author", "Ecosystem Developer")),
                license=str(meta.get("license", "Open")),
                source_path=str(folder),
                entrypoint_file="SKILL.md",
                skill_type=skill_type,
                category=category,
                tags=tags,
                required_tools=required_tools,
                instructions=text,
                content_sha256=content_sha,
                enabled=True,
                provenance=provenance,
            )
        except Exception as e:
            logger.debug("Failed to parse skill at '%s': %s", folder, e)
            return None
