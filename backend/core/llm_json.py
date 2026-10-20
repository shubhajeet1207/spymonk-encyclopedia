"""One parser for JSON objects returned by the chat model."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMOutputError(ValueError):
    """The model reply was not the JSON object the prompt asked for."""


def parse_llm_json(raw: object) -> dict:
    """Return the outermost JSON object in a model reply, or raise LLMOutputError."""
    if not isinstance(raw, str):
        raise LLMOutputError("Model output was not text")
    text = _JSON_FENCE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMOutputError("Model output did not contain a JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMOutputError("Model output contained invalid JSON") from exc
    if not isinstance(data, dict):
        raise LLMOutputError("Model output must be a JSON object")
    return data


def parse_llm_model(raw: object, model: type[ModelT]) -> ModelT:
    """Parse and validate a model reply against a Pydantic schema."""
    data = parse_llm_json(raw)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise LLMOutputError("Model output did not match the expected schema") from exc
