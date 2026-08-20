"""Project-owned checks for durable public documentation."""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
ARCHITECTURE_ASSETS = DOCS_DIR / "assets" / "architecture"
REQUIRED = (
    DOCS_DIR / "README.md",
    DOCS_DIR / "explanation" / "README.md",
    DOCS_DIR / "explanation" / "architecture.md",
    DOCS_DIR / "explanation" / "decision-ledger.md",
    DOCS_DIR / "evaluations" / "prompt-style.md",
    DOCS_DIR / "how-to" / "README.md",
    DOCS_DIR / "how-to" / "inspect-history.md",
    DOCS_DIR / "how-to" / "publish-discord.md",
    DOCS_DIR / "reference" / "README.md",
    DOCS_DIR / "reference" / "cli.md",
    DOCS_DIR / "reference" / "configuration.md",
    DOCS_DIR / "tutorials" / "README.md",
    DOCS_DIR / "tutorials" / "local-qwen.md",
)


def _body(text: str) -> str:
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if text.startswith("---\n") and end >= 0 else ""


def test_durable_docs_have_frontmatter_ids_and_substantive_bodies() -> None:
    assert all(path.is_file() for path in REQUIRED)
    ids: set[str] = set()
    for path in REQUIRED:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n") and "\n---\n" in text
        match = re.search(r"^id:\s*(\S+)$", text, re.MULTILINE)
        assert match and match.group(1) not in ids
        ids.add(match.group(1))
        assert len(_body(text).strip()) > 80, path


def test_local_markdown_links_resolve() -> None:
    for path in [PROJECT_ROOT / "README.md", PROJECT_ROOT / "SPEC.md", *REQUIRED]:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]+)?\)", text):
            if "://" not in target and not target.startswith("mailto:"):
                assert (path.parent / target).resolve().exists(), (path, target)


def test_readme_has_a_source_install_and_safe_first_result() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/safurrier/agent-away-message.git" in readme
    assert (
        'UV_TOOL_BIN_DIR="$PWD/.bin" uv tool install --from . agent-away-message'
        in readme
    )
    assert "fixture inspect tests/fixtures/dogfood-events.jsonl" in readme
    assert '# {"active_agents": 2, "records": 2, "valid": true}' in readme
    assert "docs/assets/branding/discord-application-icon.png" in readme


def test_architecture_animation_bundle_is_valid_and_linked() -> None:
    stem = ARCHITECTURE_ASSETS / "agent-away-message-flow"
    gif = stem.with_suffix(".gif")
    mp4 = stem.with_suffix(".mp4")
    png = stem.with_suffix(".png")
    scene = stem.with_suffix(".excalidraw")

    assert gif.read_bytes()[:6] in {b"GIF87a", b"GIF89a"}
    assert b"ftyp" in mp4.read_bytes()[:32]
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert json.loads(scene.read_text(encoding="utf-8"))["type"] == "excalidraw"

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (DOCS_DIR / "explanation" / "architecture.md").read_text(
        encoding="utf-8"
    )
    for suffix in ("gif", "mp4", "png", "excalidraw"):
        assert f"docs/assets/architecture/agent-away-message-flow.{suffix}" in readme
        assert (
            f"../assets/architecture/agent-away-message-flow.{suffix}" in architecture
        )


def test_project_context_exists() -> None:
    assert (PROJECT_ROOT / "AGENTS.md").is_file()
    assert (DOCS_DIR / "AGENTS.md").is_file()
