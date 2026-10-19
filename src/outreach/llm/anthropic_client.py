from __future__ import annotations

import json
import re

import httpx

from outreach.types import ParsedPost

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MODEL = "claude-sonnet-5"

_EXPAND_SYSTEM = (
    "You expand a single job title into a short list of closely related job "
    "titles used for job-board search. Respond with strict JSON only: a JSON "
    'array of strings, e.g. ["Backend Engineer", "Software Engineer, Backend"]. '
    "No prose, no markdown fences, no commentary."
)

_PARSE_SYSTEM = (
    "You extract the hiring company and one job from a Hacker News 'Who is "
    "hiring?' post. Copy every field verbatim from the post text — never "
    "infer, translate or invent. company: the company name as written. domain: "
    "the company's own website domain as it appears in the post. "
    "ats_kind/ats_token: only if the post links jobs.ashbyhq.com, "
    "boards.greenhouse.io, job-boards.greenhouse.io or jobs.lever.co (kind is "
    "ashby, greenhouse or lever; token is the first path segment), else null. "
    "role: one role title as written. location: as written. work_mode: remote, "
    "hybrid, onsite or unknown. employment_type: full_time, other or unknown. "
    "Respond with strict JSON only: one object with exactly those keys, or null."
)

_WORK_MODES = frozenset({"remote", "hybrid", "onsite", "unknown"})
_EMPLOYMENT_TYPES = frozenset({"full_time", "other", "unknown"})
_ATS_KINDS = frozenset({"ashby", "greenhouse", "lever"})


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


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
    """The two-method LLM boundary, backed by the Anthropic Messages API.

    Exactly `expand_titles` and `parse_job_post` -- who or what gets bought
    is never decided here. `parse_job_post` only reads fields out of a post;
    its answer is checked against the post (`core.hnparse.validate_llm_post`)
    before anything uses it. Each call asks for strict JSON and retries once,
    with a stricter reminder, if the first response does not parse.
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
        # A blank title would match every posting (`"" in title.lower()`), and a JSON
        # null must not become the title "None": keep only non-blank strings.
        return [t.strip() for t in result if isinstance(t, str) and t.strip()]

    def parse_job_post(self, text: str) -> ParsedPost | None:
        result = self._call_json(_PARSE_SYSTEM, text)
        if not isinstance(result, dict):
            return None
        company, domain = _text(result.get("company")), _text(result.get("domain"))
        if not company or not domain:
            return None
        kind, token = _text(result.get("ats_kind")).lower(), _text(result.get("ats_token")).lower()
        mode = _text(result.get("work_mode")).lower()
        employment = _text(result.get("employment_type")).lower()
        return ParsedPost(
            company=company, domain=domain,
            ats=(kind, token) if kind in _ATS_KINDS and token else None,
            role=_text(result.get("role")), location=_text(result.get("location")),
            work_mode=mode if mode in _WORK_MODES else "unknown",
            employment_type=employment if employment in _EMPLOYMENT_TYPES else "unknown",
        )
