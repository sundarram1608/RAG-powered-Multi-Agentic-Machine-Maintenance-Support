"""
eval_llm.py — the eval judge, deliberately DECOUPLED from the app's LLMs.

Runs on OpenRouter (OpenAI-compatible) with an independent model (default
qwen/qwen3-next-80b-a3b-instruct:free — a third family, distinct from the Llama
diagnoser and Gemini verifier), so the LLM-judge evaluators never compete with the
app's Groq/Gemini quota. Reads OPENROUTER_API_KEY + EVAL_JUDGE_MODEL from .env.
(The intended DeepSeek free variant was retired to paid; override EVAL_JUDGE_MODEL
to a paid model for reliable judging.)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
EVAL_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")

_judge = None


def get_eval_judge():
    """A ChatOpenAI bound to OpenRouter (EVAL_JUDGE_MODEL; retry/backoff for the free tier)."""
    global _judge
    if _judge is None:
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set in .env — needed for the eval judge.")
        from langchain_openai import ChatOpenAI
        _judge = ChatOpenAI(
            model=EVAL_JUDGE_MODEL, api_key=key, base_url=OPENROUTER_BASE,
            temperature=0, max_retries=5, timeout=60)
    return _judge
