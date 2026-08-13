"""Model-based task intent classification for automatic agent mode selection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


VALID_MODES = {"general", "coding"}
VALID_CONFIDENCE = {"low", "medium", "high"}


@dataclass(frozen=True)
class TaskIntent:
    mode: str
    confidence: str
    reason: str
    source: str = "model"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class TaskIntentClassifier:
    """Ask the configured model to classify intent without keyword heuristics."""

    def __init__(self, model_client, *, max_new_tokens: int = 256):
        self.model_client = model_client
        self.max_new_tokens = max_new_tokens

    def classify(self, user_request: str, conversation_summary: str = "") -> TaskIntent:
        prompt = self._prompt(user_request, conversation_summary)
        last_error = ""
        for attempt in range(2):
            request = prompt
            if attempt:
                request += (
                    "\n\nYour previous response was invalid. Return only one valid JSON object "
                    "with exactly mode, confidence, and reason."
                )
            raw = self.model_client.complete(request, self.max_new_tokens)
            try:
                return self._parse(raw)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
        raise ValueError(f"intent classification failed after two attempts: {last_error}")

    @staticmethod
    def _prompt(user_request: str, conversation_summary: str) -> str:
        return (
            "Classify the user's requested outcome for a coding agent.\n"
            "GENERAL means inspect, explain, summarize, review, or diagnose without changing the repository.\n"
            "CODING means the requested outcome requires creating, editing, deleting, refactoring, or testing repository files.\n"
            "Infer semantics from the complete request and conversation context. Do not classify by keyword matching.\n"
            "Return only strict JSON with exactly these fields:\n"
            '{"mode":"general|coding","confidence":"low|medium|high","reason":"brief explanation"}\n'
            f"Conversation summary:\n{conversation_summary.strip() or '(none)'}\n"
            f"Current request:\n{user_request.strip()}"
        )

    @staticmethod
    def _parse(raw: str) -> TaskIntent:
        value = json.loads(str(raw).strip())
        if not isinstance(value, dict) or set(value) != {"mode", "confidence", "reason"}:
            raise ValueError("classification must contain exactly mode, confidence, and reason")
        mode = str(value["mode"]).strip().lower()
        confidence = str(value["confidence"]).strip().lower()
        reason = str(value["reason"]).strip()
        if mode not in VALID_MODES:
            raise ValueError("classification mode must be general or coding")
        if confidence not in VALID_CONFIDENCE:
            raise ValueError("classification confidence must be low, medium, or high")
        if not reason:
            raise ValueError("classification reason must not be empty")
        return TaskIntent(mode=mode, confidence=confidence, reason=reason)
