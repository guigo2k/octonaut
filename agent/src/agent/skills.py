import pathlib

# agent/src/agent/skills.py -> agent/skills/ (vendored kraken-cli SKILL.md packs)
_DEFAULT_SKILLS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "skills"

_TYPE_DIRS = {"DCA": "dca", "GRID": "grid", "TWAP": "twap"}


def _load_dir(base: pathlib.Path, dirname: str) -> list[str]:
    return [p.read_text() for p in sorted((base / dirname).glob("*.md"))]


def load_skills(strategy_type: str, base: pathlib.Path = _DEFAULT_SKILLS_DIR) -> str:
    """Deterministically select Core + Market Data + the one strategy-type skill.

    Selection is a fixed lookup by ``strategy_type`` (already validated by
    ``agent.config.Strategy``), not a semantic search -- there's nothing to
    embed here. See ``agent.memory`` for the part of RAG that's actually
    semantic (recalling past trade rationales).
    """
    docs = (
        _load_dir(base, "core")
        + _load_dir(base, "market-data")
        + _load_dir(base, _TYPE_DIRS[strategy_type])
    )
    return "\n\n---\n\n".join(docs)
