"""
Thin client for the Groq chat-completions API (OpenAI-compatible).

Kept deliberately small: one function, chat(), plus a JSON helper. Uses
`requests` (already a project dependency) so there is nothing new to
install for the LLM call itself.
"""
import json
import logging
import re

import requests

from config import GROQ_API_KEY, GROQ_MODEL, GROQ_BASE_URL, AI_LLM_TIMEOUT_SECONDS
from exceptions import AppError

logger = logging.getLogger("app.ai.llm")


class AIUnavailableError(AppError):
    """The LLM isn't configured or couldn't be reached. Maps to HTTP 503."""
    status_code = 503
    message = "The AI service is currently unavailable."


def is_configured() -> bool:
    return bool(GROQ_API_KEY)


def chat(messages: list[dict], temperature: float = 0.0, json_mode: bool = False,
         max_tokens: int = 2500) -> str:
    """Sends a list of {"role","content"} messages and returns the reply text."""
    if not GROQ_API_KEY:
        raise AIUnavailableError(
            "AI is not configured on the server. Set GROQ_API_KEY in the .env file."
        )

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=AI_LLM_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.error("LLM request failed: %s", exc)
        raise AIUnavailableError("Could not reach the AI service. Please try again shortly.") from exc

    if resp.status_code == 400 and json_mode:
        # Some models reject response_format; retry once without it. chat_json()
        # already copes with JSON wrapped in prose or code fences.
        logger.info("Model rejected JSON mode, retrying without it: %s", resp.text[:200])
        payload.pop("response_format", None)
        try:
            resp = requests.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json=payload, timeout=AI_LLM_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise AIUnavailableError("Could not reach the AI service. Please try again shortly.") from exc

    if resp.status_code == 429:
        raise AIUnavailableError("The AI service is rate-limited right now. Please try again in a moment.")
    if resp.status_code >= 400:
        # Log the provider's body for debugging, but never return it to the caller.
        logger.error("LLM error %s: %s", resp.status_code, resp.text[:500])
        raise AIUnavailableError("The AI service returned an error. Please try again.")

    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("Unexpected LLM response shape: %s", resp.text[:500])
        raise AIUnavailableError("The AI service returned an unexpected response.") from exc


def chat_json(messages: list[dict], **kwargs) -> dict:
    """Like chat(), but parses the reply as a JSON object. Tolerates models
    that wrap JSON in code fences or add a sentence around it."""
    text = chat(messages, json_mode=True, **kwargs).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    logger.error("LLM did not return valid JSON: %s", text[:500])
    raise AIUnavailableError("The AI service returned a malformed response. Please rephrase and try again.")
