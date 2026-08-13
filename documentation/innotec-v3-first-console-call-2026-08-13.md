# InnoTec V3 — first Agent Console call

Date: 2026-08-13  
LiveKit room: `console-58265089`  
Room session ID: `RM_QYiTQNg7hSrd`  
Pre-fix revision: `bd69653591efbd97d332f596bd3537dae201b3f5`  
Post-fix revision: `bcf3c97263e356dc46c0b19fa756e0e5d0f65833`

## Verdict

The first browser call failed the latency and reliability acceptance gate. It
proved that the isolated LiveKit Cloud worker path was operational, but it did
not prove the product target of a semantic response within 500 ms.

## Evidence

The LiveKit recording is stereo Opus, 48 kHz, 53.61 seconds. Latency below is
measured from the physical end of caller speech to the first semantic agent
audio on that recording. Provider TTFT comes from the corresponding LiveKit
Agent Insights span.

| Turn | Physical voice-to-voice | OpenAI TTFT | Result |
|---|---:|---:|---|
| Opening greeting | not a caller turn | 441 ms | Complete, 199 output tokens |
| “Hola, buenas noches” | ~1,770 ms | 707 ms | Wrong time-of-day reply; interrupted and capped at 220 tokens |
| Short/blank user event (~370 ms) | not accepted as a valid semantic turn | 567 ms | Spurious reply capped at 220 tokens |

The two generative replies raised `response incomplete: max_output_tokens`.
The local transcript also failed during finalization because the non-root
container user could not create `transcripts/innotec-v3` in the host bind
mount. The worker itself remained healthy with zero restarts.

## Root causes

1. `max_response_output_tokens: 220` included both text and audio tokens and
   was too small for a normal Spanish commercial reply.
2. Semantic VAD at high eagerness accepted a very short non-semantic sound as
   a complete turn.
3. The effective prompt was approximately 1,415 tokens before tool schemas,
   and all eleven generic receptionist tools were sent to OpenAI although only
   five were relevant to InnoTec.
4. The prompt had no hard rule to mirror a caller's time-of-day greeting.
5. The container UID could read but could not write the GID-1000 runtime
   directories.

## Deployed correction

- Compact InnoTec sales prompt: ~629 approximate tokens.
- Explicit tool allow-list: `lookup_faq`, `get_business_hours`,
  `take_message`, `transfer_call`, and `end_call`.
- Server VAD: threshold 0.65, 200 ms prefix and 300 ms silence.
- Response cap raised to 480 total output tokens.
- Shorter one-time greeting and explicit time-of-day concordance.
- Realtime audio transcript and metrics capture no longer depends on
  `text_content`.
- Supplemental runtime group 1000 grants write access without running the
  process as root.

Verification: 658 automated tests, local and VPS Docker builds, configuration
smoke, exact tool registry smoke, bind-mount write smoke, healthy container,
zero restarts, and successful registration in LiveKit Germany 2.

## Rollback

Server snapshot: `/home/criterium/criterium-voice-v3-rollback/20260813-first-call`.
It contains the prior compose file, business config, private environment file,
and prior Docker image ID. Restore those files and recreate only the V3 agent;
V1 and V2 are independent and must not be touched.

## Remaining acceptance gate

Run a second comparable Agent Console conversation after the deployment. Save
the dual recording and publish every valid caller turn, p50, p95, maximum and
percentage at or below 500 ms. Do not claim the sub-500 target from provider
TTFT or a single greeting.
