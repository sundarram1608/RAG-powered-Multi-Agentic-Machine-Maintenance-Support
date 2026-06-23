"""
llms.py — LLM provider factory for the agent layer.

  get_reasoner() -> Groq Llama 3.3 70B   (reasoning / tool-calling: supervisor,
                    intake, diagnosis, deciders, actions, analytics, output)
  get_judge()    -> Gemini 2.5 Flash-Lite (independent verifier / future vision)

Both read their API keys from .env (loaded by config). Switching providers is a
one-line change here — every node calls these factories, not a provider directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
import config

from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI


def get_reasoner():
    """Groq Llama 3.3 70B — the primary reasoning / tool-calling model."""
    if not config.GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set in .env — required for the reasoning model. "
            "Get a free key at https://console.groq.com → API Keys."
        )
    return ChatGroq(model=config.REASONING_MODEL, temperature=config.REASONING_TEMPERATURE,
                    max_retries=config.LLM_MAX_RETRIES)


def get_judge():
    """Gemini 2.5 Flash-Lite — the independent verifier (a different model family)."""
    if not config.GOOGLE_API_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set in .env — required for the judge model. "
            "Get a free key at https://aistudio.google.com → Get API key."
        )
    return ChatGoogleGenerativeAI(model=config.JUDGE_MODEL, temperature=config.JUDGE_TEMPERATURE,
                                  max_retries=config.LLM_MAX_RETRIES)


def get_judge_structured(schema):
    """Independent judge bound to `schema`, with a Groq fallback.

    Gemini stays the PRIMARY judge (independent non-Groq family). If it is
    unavailable after retries — transient 503 demand spikes, or a hard daily-quota
    429 that retries can't clear — LangChain `.with_fallbacks` transparently fails
    over to Qwen-3 on Groq (also a different family than the Llama reasoner, so the
    independence property is preserved). Returns a Runnable that yields `schema`.
    """
    primary = get_judge().with_structured_output(schema)
    fallback = ChatGroq(
        model=config.JUDGE_FALLBACK_MODEL,
        temperature=config.JUDGE_TEMPERATURE,
        max_retries=config.LLM_MAX_RETRIES,
    ).with_structured_output(schema)
    return primary.with_fallbacks([fallback])
