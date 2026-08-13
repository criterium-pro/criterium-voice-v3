# receptionist/transcript/capture.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from receptionist.transcript.metadata import (
    LATENCY_TARGET_MS,
    CallMetadata,
    LatencyTurnRecord,
)

logger = logging.getLogger("receptionist")
_MAX_SEGMENTS = 5000


class SpeakerRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class TranscriptSegment:
    role: SpeakerRole
    text: str
    created_at: float
    language: str | None = None
    tool_arguments: str | None = None
    tool_output: str | None = None


class TranscriptCapture:
    """Subscribes to AgentSession events and accumulates TranscriptSegments.

    Event names verified against livekit-agents==1.5.6:
      - user_input_transcribed (UserInputTranscribedEvent)
      - conversation_item_added (ConversationItemAddedEvent) — assistant chat
      - function_tools_executed (FunctionToolsExecutedEvent)
    """

    def __init__(self, emitter: Any, metadata: CallMetadata) -> None:
        self.segments: list[TranscriptSegment] = []
        self.metadata = metadata
        emitter.on("user_input_transcribed", self._on_user_input)
        emitter.on("conversation_item_added", self._on_conversation_item)
        emitter.on("function_tools_executed", self._on_tools_executed)

    def _on_user_input(self, event: Any) -> None:
        try:
            if not getattr(event, "is_final", False):
                return
            text = event.transcript
            lang = getattr(event, "language", None)
            self.segments.append(TranscriptSegment(
                role=SpeakerRole.USER,
                text=text,
                created_at=event.created_at,
                language=lang,
            ))
            self._trim_segments()
            if lang:
                self.metadata.languages_detected.add(lang)
        except Exception:
            logger.exception("TranscriptCapture: error handling user_input_transcribed")

    def _on_conversation_item(self, event: Any) -> None:
        try:
            item = event.item
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None) or getattr(item, "text", None)
            if role != "assistant" or not text:
                return
            self.segments.append(TranscriptSegment(
                role=SpeakerRole.ASSISTANT,
                text=text,
                created_at=event.created_at,
            ))
            self._trim_segments()
            self._capture_latency(item, event.created_at)
        except Exception:
            logger.exception("TranscriptCapture: error handling conversation_item_added")

    def _capture_latency(self, item: Any, created_at: float) -> None:
        """Persist LiveKit's per-turn MetricsReport without transcript content."""
        report = getattr(item, "metrics", None)
        if not isinstance(report, dict):
            return
        e2e_seconds = report.get("e2e_latency")
        if not isinstance(e2e_seconds, (int, float)):
            return

        def milliseconds(key: str) -> float | None:
            value = report.get(key)
            if not isinstance(value, (int, float)):
                return None
            return float(value) * 1000

        request_ids = report.get("provider_request_ids", [])
        if not isinstance(request_ids, list):
            request_ids = []
        record = LatencyTurnRecord(
            timestamp=float(created_at),
            e2e_ms=float(e2e_seconds) * 1000,
            end_of_turn_ms=milliseconds("end_of_turn_delay"),
            llm_ttft_ms=milliseconds("llm_node_ttft"),
            playback_ms=milliseconds("playback_latency"),
            provider_request_ids=[str(value) for value in request_ids],
        )
        self.metadata.latency_turns.append(record)
        logger.info(
            "Assistant turn latency %.1f ms (target <= %d ms, met=%s)",
            record.e2e_ms,
            LATENCY_TARGET_MS,
            record.e2e_ms <= LATENCY_TARGET_MS,
            extra={
                "call_id": self.metadata.call_id,
                "component": "transcript.latency",
                "e2e_latency_ms": round(record.e2e_ms, 1),
                "latency_target_ms": LATENCY_TARGET_MS,
                "latency_target_met": record.e2e_ms <= LATENCY_TARGET_MS,
            },
        )

    def _on_tools_executed(self, event: Any) -> None:
        try:
            calls = event.function_calls or []
            outputs = event.function_call_outputs or []
            for i, call in enumerate(calls):
                out = outputs[i] if i < len(outputs) else None
                self.segments.append(TranscriptSegment(
                    role=SpeakerRole.TOOL,
                    text=call.name,
                    created_at=event.created_at,
                    tool_arguments=getattr(call, "arguments", None),
                    tool_output=(getattr(out, "output", None) if out is not None else None),
                ))
            self._trim_segments()
        except Exception:
            logger.exception("TranscriptCapture: error handling function_tools_executed")

    def _trim_segments(self) -> None:
        if len(self.segments) > _MAX_SEGMENTS:
            del self.segments[: len(self.segments) - _MAX_SEGMENTS]
