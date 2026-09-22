from __future__ import annotations

import json
import re
from typing import Sequence

import httpx

from outreach.llm.base import RawClaim

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MODEL = "claude-3-5-sonnet-20241022"

_EXPAND_SYSTEM = (
    "You expand a single job title into a short list of closely related job "
    "titles used for job-board search. Respond with strict JSON only: a JSON "
    'array of strings, e.g. ["Backend Engineer", "Software Engineer, Backend"]. '
    "No prose, no markdown fences, no commentary."
)

_EXTRACT_SYSTEM = (
    "You extract factual claims about engineering-team pain points (scaling "
    "bottlenecks, manual toil, operational strain) from the given page text. "
    "Every quote you return MUST be copied verbatim, character-for-character, "
    "from the supplied text -- never paraphrased or invented. Respond with "
    "strict JSON only: a JSON array of objects, each with exactly the keys "
    '"claim" (a short paraphrase), "quote" (the verbatim excerpt), and "theme" '
    "(a short slug grouping related claims). Return an empty array if there is "
    "nothing relevant. No prose, no markdown fences, no commentary."
)

_SUMMARY_SYSTEM = (
    "You write a two-to-three sentence, neutral, factual summary of a "
    "corroborated engineering bottleneck, grounded only in the claim and "
    "quotes given to you. Respond with strict JSON only: a JSON object with "
    'exactly the key "summary" holding the summary text. No prose, no '
    "markdown fences, no commentary."
)


class LLMFormatError(RuntimeError):
    """The model's response could not be parsed as the requested JSON shape."""


def _extract_json_text(payload: dict) -> str:
    content = payload.get("content", [])
    parts = [block.get("text", "") for block in content if isinstance(block, dict)
             and block.get("type") == "text"]
    return "".join(parts)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


class AnthropicLLM:
    """The three-method LLM boundary, backed by the Anthropic Messages API.

    Exactly `expand_titles`, `extract_claims` and `write_summary` -- the
    pass/fail call about evidence stays in the deterministic gate, never
    here. Each call asks for strict JSON and retries once, with a stricter
    reminder, if the first response does not parse.
    """

    def __init__(self, api_key: str, client: httpx.Client | None = None,
                 model: str = _MODEL) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.Client(timeout=60.0)

    def _call(self, system: str, user: str, max_tokens: int = 1024) -> str:
        response = self._client.post(
            _API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        response.raise_for_status()
        return _extract_json_text(response.json())

    def _call_json(self, system: str, user: str, max_tokens: int = 1024) -> object:
        """Ask for strict JSON, retrying once with a sharper reminder on a parse failure."""
        text = self._call(system, user, max_tokens)
        try:
            return json.loads(_strip_fences(text))
        except json.JSONDecodeError:
            pass

        retry_user = (
            f"{user}\n\nYour previous reply did not parse as JSON. Respond with "
            "ONLY valid JSON matching the requested shape -- nothing else."
        )
        text = self._call(system, retry_user, max_tokens)
        try:
            return json.loads(_strip_fences(text))
        except json.JSONDecodeError as exc:
            raise LLMFormatError(
                f"model reply did not parse as JSON after one retry: {text!r}") from exc

    def expand_titles(self, role_title: str) -> list[str]:
        result = self._call_json(_EXPAND_SYSTEM, f"Job title: {role_title!r}")
        if not isinstance(result, list):
            raise LLMFormatError(f"expected a JSON array of titles, got: {result!r}")
        return [str(t) for t in result]

    def extract_claims(self, text: str) -> list[RawClaim]:
        result = self._call_json(_EXTRACT_SYSTEM, text, max_tokens=2048)
        if not isinstance(result, list):
            raise LLMFormatError(f"expected a JSON array of claims, got: {result!r}")
        claims: list[RawClaim] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            claim = item.get("claim")
            quote = item.get("quote")
            theme = item.get("theme")
            if not (isinstance(claim, str) and isinstance(quote, str)
                    and isinstance(theme, str)):
                continue
            claims.append(RawClaim(claim=claim, quote=quote, theme=theme))
        return claims

    def write_summary(self, claim: str, quotes: Sequence[str]) -> str:
        user = (
            f"Claim: {claim}\n\nSupporting quotes:\n"
            + "\n".join(f"- {q}" for q in quotes)
        )
        result = self._call_json(_SUMMARY_SYSTEM, user)
        if not isinstance(result, dict) or not isinstance(result.get("summary"), str):
            raise LLMFormatError(f"expected a JSON object with a summary, got: {result!r}")
        return result["summary"]
