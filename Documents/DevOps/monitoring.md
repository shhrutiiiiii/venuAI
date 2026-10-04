# Monitoring — VenuAI

## Purpose
Documents the current analytics/logging (which is application-level only) and what real operational monitoring would add.

## Responsibilities
Describe what's observable today vs. what isn't.

## Scope
Operational monitoring only. Application-level query analytics (grounding scores, latency per query) is also described here since `analytics.py` is currently the closest thing to monitoring this system has.

## High-Level Overview
**Current state:**
- `VENUAI/analytics.py` logs every query (latency, answer length, grounding/hallucination scores, agent used, tools called, errors) — surfaced via `/analytics/dashboard` (admin-only, see [`../Backend/authentication.md`](../Backend/authentication.md)) and the frontend `/analytics` page.
- ~~Standard Python `logging.basicConfig`~~ — **upgraded (Phase 5).** `VENUAI/observability.py`'s `configure_logging()` now supports real structured JSON log output (`LOG_FORMAT=json`), each line a parseable JSON object (timestamp/level/logger/message plus any `extra=` fields a call site passed) — plain text remains the default for local development. This changes the *shape* of what goes to stdout/stderr; it does not ship logs anywhere — no log aggregation or shipping agent exists, see Future Improvements.
- **No infrastructure-level monitoring exists**: no Prometheus/Grafana, no uptime checks, no alerting, no tracing (OpenTelemetry or otherwise).

## Design Principles (for what should exist)
1. Application analytics (`analytics.py`) answers "is the AI performing well" — operational monitoring should separately answer "is the service up and fast," and the two should not be conflated.
2. Alerting should exist on failure signals that are currently only visible by someone manually opening `/analytics`: elevated hallucination scores, elevated guardrail-block rate, elevated error rate.

## Workflow
N/A — no monitoring pipeline exists to diagram yet.

## Inputs / Outputs
N/A.

## Dependencies
- [`../Backend/services.md`](../Backend/services.md) — the `analytics.py` service this document partly describes.

## Interactions with Other Services
N/A today.

## Security Considerations
`/analytics/dashboard` is admin-gated (see [`../Backend/authentication.md`](../Backend/authentication.md)) — any future monitoring dashboard should follow the same rule.

## Performance Considerations
N/A.

## Future Improvements
1. ~~Add structured logging (JSON logs)~~ — **done (Phase 5)**, see above. **Still open:** actually shipping those JSON lines to a real log aggregator (CloudWatch, Datadog, Loki) requires a configured shipping agent and/or a hosted service account — genuine infrastructure this environment doesn't have, not fabricable in code.
2. Add basic infra monitoring (uptime, latency, error rate) via whatever the eventual hosting platform provides, or Prometheus + Grafana if self-hosted.
3. Add alerting thresholds on hallucination-score and guardrail-block rate trends, not just raw display.

## Notes for Developers
Don't build infra monitoring inside `analytics.py` — keep "is the AI's output good" (application concern) separate from "is the service healthy" (ops concern), per Design Principle 1.
