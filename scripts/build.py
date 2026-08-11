#!/usr/bin/env python3
"""
Builds data.json + last_built.json for the TikTok Brain dashboard.

Reads (relative to BRAIN_WORKSPACE, default: this repo's demo-workspace/):
  live/INDEX.md      (active idea inbox)
  archive/INDEX.md   (archived ideas)
  public/status.json (triage status overlay)

In production the workspace points at the agent's real note tree, where a
watcher daemon transcribes and categorizes captured ideas before this script
runs.

Writes:
  public/data.json
  public/last_built.json
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("BRAIN_WORKSPACE", REPO_ROOT / "demo-workspace"))
LIVE_DIR = WORKSPACE / "live"
ARCHIVE_DIR = WORKSPACE / "archive"
OUT_DIR = REPO_ROOT / "public"
STATUS_PATH = OUT_DIR / "status.json"
DATA_PATH = OUT_DIR / "data.json"
BUILT_PATH = OUT_DIR / "last_built.json"

THEME_MAP = [
    ("ai building", ["agent os", "agent", "claude code", "claude-code", "automation",
                     "anthropic", "skill", "mcp", "memory", "prompting", "safety"]),
    ("coding",      ["coding", "developer", "front-end", "frontend", "engineer", "ui design"]),
    ("business",    ["business", "money", "monetiz", "affiliate", "strategy", "outreach",
                     "amazon", "ecommerce"]),
    ("marketing",   ["marketing", "tiktok", "pinterest", "social", "seo", "content",
                     "instagram", "youtube"]),
]

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*\[\[([^\]]+)\]\]\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")


def derive_theme(tags_str: str, title: str) -> str:
    haystack = (tags_str + " " + title).lower()
    for theme, keywords in THEME_MAP:
        if any(k in haystack for k in keywords):
            return theme
    return "other"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block = m.group(1)
    rest = text[m.end():]
    fm = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            v = v.strip().strip('"').strip("'")
            fm[k.strip()] = v
    return fm, rest


def parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        name = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name] = body[start:end].strip()
    return sections


def title_from_slug(slug: str) -> str:
    after_date = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", slug)
    return after_date.replace("-", " ").strip()


def load_idea(folder: Path, slug: str, fallback_date: str, fallback_tags: str,
              fallback_summary: str, source_folder: str) -> dict | None:
    # Try root, then archive/ subfolder. _trash/ is intentionally excluded.
    candidates = [folder / f"{slug}.md", folder / "archive" / f"{slug}.md"]
    md_path = next((p for p in candidates if p.exists()), None)
    if md_path is None:
        return None
    text = md_path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(text)
    sections = parse_sections(body)

    date = fm.get("date") or fallback_date
    tags_str = fm.get("tags") or fallback_tags
    summary = fm.get("summary") or fallback_summary
    source = fm.get("source") or ""

    title = title_from_slug(slug)

    return {
        "id": f"{source_folder}:{slug}",
        "slug": slug,
        "date": date,
        "title": title,
        "tags": [t.strip() for t in tags_str.split(",") if t.strip()],
        "summary": summary.strip(),
        "theme": derive_theme(tags_str, title),
        "source_url": source,
        "source_folder": source_folder,
        "key_ideas": sections.get("key ideas", ""),
        "why_it_matters": sections.get("why it matters", ""),
        "recommendations": sections.get("recommendations", ""),
        "transcript": sections.get("transcript", ""),
    }


def parse_index(index_path: Path, folder: Path, source_folder: str) -> tuple[list[dict], list[str]]:
    if not index_path.exists():
        return [], [f"INDEX.md missing at {index_path}"]
    ideas: list[dict] = []
    errors: list[str] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        date, slug, tags, summary, _status = m.groups()
        idea = load_idea(folder, slug, date, tags, summary, source_folder)
        if idea is None:
            errors.append(f"Missing transcript file: {folder}/{slug}.md")
            continue
        ideas.append(idea)
    return ideas, errors


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    status_overlay = {}
    if STATUS_PATH.exists():
        try:
            status_overlay = json.loads(STATUS_PATH.read_text())
        except json.JSONDecodeError as e:
            print(f"[build] status.json invalid: {e}", file=sys.stderr)

    live_ideas, live_errors = parse_index(LIVE_DIR / "INDEX.md", LIVE_DIR, "live")
    arch_ideas, arch_errors = parse_index(ARCHIVE_DIR / "INDEX.md", ARCHIVE_DIR, "archive")

    all_ideas = live_ideas + arch_ideas
    for idea in all_ideas:
        overlay = status_overlay.get(idea["id"], {})
        idea["status"] = overlay.get("status", "New")
        idea["clickup_task_id"] = overlay.get("clickup_task_id")
        idea["clickup_url"] = overlay.get("clickup_url")
        idea["notes"] = overlay.get("notes", "")
        idea["updated_at"] = overlay.get("updated_at")

    all_ideas.sort(key=lambda i: (i["date"], i["slug"]), reverse=True)

    DATA_PATH.write_text(json.dumps({
        "ideas": all_ideas,
        "schema_version": 1,
    }, indent=2, ensure_ascii=False))

    errors = live_errors + arch_errors
    BUILT_PATH.write_text(json.dumps({
        "built_at": datetime.now(timezone.utc).isoformat(),
        "idea_count": len(all_ideas),
        "live_count": len(live_ideas),
        "archive_count": len(arch_ideas),
        "errors": errors,
        "demo": "BRAIN_WORKSPACE" not in os.environ,
    }, indent=2))

    print(f"[build] {len(all_ideas)} ideas ({len(live_ideas)} live, {len(arch_ideas)} archive)")
    if errors:
        print(f"[build] {len(errors)} parse warnings (logged to last_built.json)", file=sys.stderr)
        for e in errors[:5]:
            print(f"  - {e}", file=sys.stderr)
    # Only fail if we parsed nothing at all (both INDEX files unreadable).
    return 1 if not all_ideas else 0


if __name__ == "__main__":
    sys.exit(main())
