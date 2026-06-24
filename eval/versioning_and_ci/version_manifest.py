"""
version_manifest.py — snapshot the exact config that produced an eval result.

Collects prompt versions (prompts/*.py *_VERSION), model ids (app + eval judge), and
the tuned dials (RERANK_CANDIDATES, MAX_DIAGNOSIS_REQUERIES, …) into one dict. run_eval
attaches it to every LangSmith experiment's metadata, so each score is attributable to
a known config — git + this manifest are our prompt/version record (no Prompt Hub).
"""

import importlib
import pkgutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("agents", "rag", "eval"):
    sys.path.insert(0, str(ROOT / p))


def _prompt_versions() -> dict:
    out = {}
    try:
        import prompts as pkg
        for m in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"prompts.{m.name}")
            ver = next((getattr(mod, a) for a in dir(mod) if a.endswith("_VERSION")), None)
            if ver:
                out[m.name] = ver
    except Exception as e:
        out["_error"] = str(e)[:80]
    return out


def get_manifest() -> dict:
    import config
    manifest = {"models": {}, "dials": {}, "prompts": _prompt_versions()}
    manifest["models"] = {
        "reasoning": config.REASONING_MODEL,
        "judge": config.JUDGE_MODEL,
        "judge_fallback": config.JUDGE_FALLBACK_MODEL,
    }
    try:
        from eval_llm import EVAL_JUDGE_MODEL
        manifest["models"]["eval_judge"] = EVAL_JUDGE_MODEL
    except Exception:
        pass
    try:
        import retriever
        manifest["dials"].update(
            RERANK_CANDIDATES=retriever.RERANK_CANDIDATES,
            MANUAL_K=retriever.MANUAL_K, SAFETY_K=retriever.SAFETY_K)
    except Exception:
        pass
    manifest["dials"].update(
        MAX_DIAGNOSIS_REQUERIES=getattr(config, "MAX_DIAGNOSIS_REQUERIES", None),
        VERIFY_MAX_ATTEMPTS=getattr(config, "VERIFY_MAX_ATTEMPTS", None),
        ANALYTICS_MAX_ATTEMPTS=getattr(config, "ANALYTICS_MAX_ATTEMPTS", None))
    return manifest


if __name__ == "__main__":
    import json
    print(json.dumps(get_manifest(), indent=2))
