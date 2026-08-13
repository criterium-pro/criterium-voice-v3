# receptionist/transcript/metadata.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("receptionist")
LATENCY_TARGET_MS = 500


# Valid outcome labels. Membership-checked in lifecycle._add_outcome to prevent
# silent typos; new outcomes must be added here AND in the _OUTCOME_LABELS map
# in receptionist/email/templates.py for their human-readable display.
VALID_OUTCOMES = {
    "hung_up", "message_taken", "transferred", "appointment_booked", "agent_ended",
    "intake_submitted",
}


# Valid DTMF event status labels. Membership-checked in
# lifecycle.record_dtmf_event / update_dtmf_event_status to prevent a typo'd
# status string from landing silently in transcripts. Mirrors the
# VALID_OUTCOMES pattern. New statuses must be added here.
VALID_DTMF_STATUSES = {
    "pending", "executed", "failed", "unmapped",
    "duplicate_ignored", "suppressed_in_flight", "refused_intake_only",
    "intake_capture", "intake_capture_cleared", "intake_capture_timeout",
}


@dataclass
class InfoPacketSendRecord:
    packet_key: str
    packet_display_name: str
    channel: str
    destination: str
    status: str
    error: str | None = None
    sent_at: str = ""

    def __post_init__(self) -> None:
        if not self.sent_at:
            self.sent_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "packet_key": self.packet_key,
            "packet_display_name": self.packet_display_name,
            "channel": self.channel,
            "destination": self.destination,
            "status": self.status,
            "error": self.error,
            "sent_at": self.sent_at,
        }


@dataclass
class DtmfEventRecord:
    digit: str
    action: str | None
    target: str | None
    status: str
    error: str | None = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "digit": self.digit,
            "action": self.action,
            "target": self.target,
            "status": self.status,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class LatencyTurnRecord:
    """Provider-backed latency measurements for one assistant turn."""

    timestamp: float
    e2e_ms: float
    end_of_turn_ms: float | None = None
    llm_ttft_ms: float | None = None
    playback_ms: float | None = None
    provider_request_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "e2e_ms": round(self.e2e_ms, 1),
            "end_of_turn_ms": _round_optional(self.end_of_turn_ms),
            "llm_ttft_ms": _round_optional(self.llm_ttft_ms),
            "playback_ms": _round_optional(self.playback_ms),
            "under_500ms": self.e2e_ms <= LATENCY_TARGET_MS,
            "provider_request_ids": list(self.provider_request_ids),
        }


def _round_optional(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _latency_summary(turns: list[LatencyTurnRecord]) -> dict:
    values = sorted(turn.e2e_ms for turn in turns)
    if not values:
        return {
            "target_ms": LATENCY_TARGET_MS,
            "measured_turns": 0,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
            "under_target_percent": None,
        }

    def percentile_nearest_rank(percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(values)) - 1)
        return values[index]

    under_target = sum(value <= LATENCY_TARGET_MS for value in values)
    return {
        "target_ms": LATENCY_TARGET_MS,
        "measured_turns": len(values),
        "p50_ms": round(percentile_nearest_rank(0.50), 1),
        "p95_ms": round(percentile_nearest_rank(0.95), 1),
        "max_ms": round(values[-1], 1),
        "under_target_percent": round(100 * under_target / len(values), 1),
    }


@dataclass
class CallMetadata:
    call_id: str
    business_name: str
    caller_phone: str | None = None
    start_ts: str = ""
    end_ts: str | None = None
    duration_seconds: float | None = None
    outcomes: set[str] = field(default_factory=set)  # was `outcome: str | None`
    transfer_target: str | None = None
    message_taken: bool = False
    appointment_booked: bool = False  # NEW — convenience mirror of "appointment_booked" in outcomes
    appointment_details: dict | None = None  # NEW — {event_id, start_iso, end_iso, html_link}
    faqs_answered: list[str] = field(default_factory=list)
    languages_detected: set[str] = field(default_factory=set)
    recording_failed: bool = False
    recording_artifact: str | None = None
    # Free-form short label for *why* the agent ended the call (issues #10/#11).
    # Populated alongside the `agent_ended` outcome so call summaries, transcripts,
    # and dashboards can distinguish a polite goodbye from a silence-timeout or
    # unproductive-turn cap. Stays None when the agent did not initiate the
    # hangup (e.g. caller hung up first => outcome `hung_up`).
    agent_end_reason: str | None = None
    info_packet_sends: list[InfoPacketSendRecord] = field(default_factory=list)
    dtmf_events: list[DtmfEventRecord] = field(default_factory=list)
    latency_turns: list[LatencyTurnRecord] = field(default_factory=list)

    def __post_init__(self):
        if not self.start_ts:
            self.start_ts = datetime.now(timezone.utc).isoformat()

    def mark_finalized(self) -> None:
        if self.end_ts is None:
            self.end_ts = datetime.now(timezone.utc).isoformat()
        if not self.outcomes:
            self.outcomes.add("hung_up")
        try:
            start = datetime.fromisoformat(self.start_ts)
            end = datetime.fromisoformat(self.end_ts)
            self.duration_seconds = (end - start).total_seconds()
        except ValueError as e:
            logger.warning(
                "CallMetadata.mark_finalized: could not compute duration "
                "from start_ts=%r end_ts=%r: %s",
                self.start_ts, self.end_ts, e,
                extra={"call_id": self.call_id, "component": "transcript.metadata"},
            )

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "business_name": self.business_name,
            "caller_phone": self.caller_phone,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_seconds": self.duration_seconds,
            "outcomes": sorted(self.outcomes),  # sorted list for stable JSON
            "transfer_target": self.transfer_target,
            "message_taken": self.message_taken,
            "appointment_booked": self.appointment_booked,
            "appointment_details": self.appointment_details,
            "agent_end_reason": self.agent_end_reason,
            "faqs_answered": list(self.faqs_answered),
            "languages_detected": sorted(self.languages_detected),
            "recording_failed": self.recording_failed,
            "recording_artifact": self.recording_artifact,
            "info_packet_sends": [
                record.to_dict() for record in self.info_packet_sends
            ],
            "dtmf_events": [record.to_dict() for record in self.dtmf_events],
            "latency": {
                "summary": _latency_summary(self.latency_turns),
                "turns": [record.to_dict() for record in self.latency_turns],
            },
        }
