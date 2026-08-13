# tests/transcript/test_capture.py
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from receptionist.transcript.capture import (
    TranscriptCapture, TranscriptSegment, SpeakerRole,
)
from receptionist.transcript.metadata import CallMetadata


class FakeEmitter:
    """Mimics the subset of livekit.agents.AgentSession.on() we use."""

    def __init__(self):
        self.handlers: dict[str, list] = {}

    def on(self, event: str, fn):
        self.handlers.setdefault(event, []).append(fn)
        return fn

    def emit(self, event: str, payload):
        for fn in self.handlers.get(event, []):
            fn(payload)


def test_capture_records_user_input():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    user_event = MagicMock(
        transcript="Hi, I'd like to book an appointment.",
        is_final=True,
        language="en",
        created_at=100.0,
    )
    emitter.emit("user_input_transcribed", user_event)

    assert len(capture.segments) == 1
    seg = capture.segments[0]
    assert seg.role == SpeakerRole.USER
    assert seg.text == "Hi, I'd like to book an appointment."
    assert seg.language == "en"


def test_capture_skips_non_final_user_segments():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    emitter.emit("user_input_transcribed", MagicMock(
        transcript="hi", is_final=False, language="en", created_at=100.0,
    ))
    assert capture.segments == []


def test_capture_records_assistant_messages():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    item = MagicMock(role="assistant", text_content="Sure, I can help.")
    event = MagicMock(item=item, created_at=101.0)
    emitter.emit("conversation_item_added", event)

    assert len(capture.segments) == 1
    assert capture.segments[0].role == SpeakerRole.ASSISTANT
    assert capture.segments[0].text == "Sure, I can help."


def test_capture_records_livekit_per_turn_latency():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    item = MagicMock(
        role="assistant",
        text_content="Claro, le ayudo.",
        metrics={
            "e2e_latency": 0.438,
            "end_of_turn_delay": 0.102,
            "llm_node_ttft": 0.281,
            "playback_latency": 0.003,
            "provider_request_ids": ["resp_123"],
        },
    )
    emitter.emit("conversation_item_added", MagicMock(item=item, created_at=101.0))

    assert len(md.latency_turns) == 1
    assert md.latency_turns[0].e2e_ms == 438.0
    assert md.latency_turns[0].provider_request_ids == ["resp_123"]
    assert md.to_dict()["latency"]["summary"]["under_target_percent"] == 100.0


def test_capture_records_realtime_audio_transcript_and_latency():
    """Realtime audio stores text on AudioContent.transcript, not text_content."""
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    item = SimpleNamespace(
        role="assistant",
        text_content=None,
        text=None,
        content=[SimpleNamespace(transcript="Buenas noches. Cuénteme.")],
        metrics={
            "e2e_latency": 0.912,
            "end_of_turn_delay": 0.301,
            "llm_node_ttft": 0.507,
            "playback_latency": 0.004,
            "provider_request_ids": ["resp_realtime"],
        },
    )
    emitter.emit("conversation_item_added", SimpleNamespace(item=item, created_at=101.0))

    assert capture.segments[0].text == "Buenas noches. Cuénteme."
    assert md.latency_turns[0].e2e_ms == 912.0
    assert md.latency_turns[0].end_of_turn_ms == 301.0


def test_capture_skips_assistant_turn_without_numeric_latency():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    item = MagicMock(role="assistant", text_content="Hola", metrics={})
    emitter.emit("conversation_item_added", MagicMock(item=item, created_at=101.0))

    assert md.latency_turns == []


def test_capture_records_tool_calls():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    call = MagicMock()
    call.name = "lookup_faq"
    call.arguments = '{"question": "hours"}'

    output = MagicMock()
    output.output = "We are open 8-5."

    event = MagicMock(
        function_calls=[call],
        function_call_outputs=[output],
        created_at=102.0,
    )
    emitter.emit("function_tools_executed", event)

    assert len(capture.segments) == 1
    seg = capture.segments[0]
    assert seg.role == SpeakerRole.TOOL
    assert seg.text == "lookup_faq"
    assert "hours" in (seg.tool_arguments or "")


def test_capture_updates_language_on_metadata():
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    emitter.emit("user_input_transcribed", MagicMock(
        transcript="Hola", is_final=True, language="es", created_at=100.0,
    ))
    emitter.emit("user_input_transcribed", MagicMock(
        transcript="Hello", is_final=True, language="en", created_at=101.0,
    ))
    assert md.languages_detected == {"es", "en"}


def test_capture_handler_exceptions_are_swallowed():
    """A malformed event must not propagate — the call must keep going."""
    emitter = FakeEmitter()
    md = CallMetadata(call_id="room-1", business_name="Acme")
    capture = TranscriptCapture(emitter, md)

    bad_event = object()
    emitter.emit("user_input_transcribed", bad_event)
    assert capture.segments == []
