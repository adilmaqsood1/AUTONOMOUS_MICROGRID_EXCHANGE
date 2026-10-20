"""Safe structured JSON parser and schema validator for AI responses."""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class AIParseError(ValueError):
    """Raised when AI response cannot be parsed or fails schema validation."""
    def __init__(self, message: str, raw_text: str = ""):
        super().__init__(message)
        self.raw_text = raw_text


def extract_json(raw_text: str) -> str:
    """Strips markdown code fences and extracts raw JSON string."""
    cleaned = raw_text.strip()
    # Match ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def parse_and_validate(raw_text: str, schema: type[T]) -> T:
    """Parses raw LLM output into a validated Pydantic model instance.

    Raises AIParseError on malformed JSON or validation errors.
    """
    json_str = extract_json(raw_text)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise AIParseError(f"Malformed JSON from AI model: {exc}", raw_text=raw_text) from exc

    if not isinstance(data, dict):
        raise AIParseError("AI output must be a JSON object, not a list or scalar", raw_text=raw_text)

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise AIParseError(f"AI response failed schema validation: {exc}", raw_text=raw_text) from exc
