"""Alert conditions for planner observability (B10.8).

This module documents the alert conditions that should be configured
in your monitoring system (e.g., Prometheus, Datadog, CloudWatch).

These alerts ensure that planner failures are detected before users complain.
"""

# Hard Alerts (Critical - Page on-call)
# ======================================
#
# 1. planner_macro_failure > 1%
#    - Description: More than 1% of macro plan generations are failing
#    - Severity: Critical
#    - Action: Investigate LLM issues, context problems, or validation failures
#
# 2. planner_persist_failure > 0.1%
#    - Description: More than 0.1% of persistence operations are failing
#    - Severity: Critical
#    - Action: Check database connectivity, constraint violations, or data corruption
#
# 3. planner_stage_missing (missing start/success pair)
#    - Description: A stage started but never completed (no success or fail event)
#    - Severity: Critical
#    - Action: Check for crashes, timeouts, or unhandled exceptions
#    - Detection: Track stages that have "start" but no "success" or "fail" within 5 minutes
#
# Soft Alerts (Warning - Log and investigate)
# ============================================
#
# 4. planner latency > 2s p95
#    - Description: 95th percentile of planner execution time exceeds 2 seconds
#    - Severity: Warning
#    - Action: Investigate slow stages, optimize bottlenecks, check LLM latency
#    - Metric: planner_timing duration_seconds (p95 across all stages)
#
# 5. text_generation_fallback_rate > 5%
#    - Description: More than 5% of session text generations fall back to deterministic generation
#    - Severity: Warning
#    - Action: Check LLM availability, prompt issues, or validation failures
#    - Detection: Count sessions with computed.generated_by == "fallback" / total sessions
#
# Alert Configuration Example (Prometheus)
# =========================================
#
# Example Prometheus alert rules:
# - Hard Alert: Macro failure rate > 1%
# - Hard Alert: Persist failure rate > 0.1%
# - Soft Alert: High latency p95 > 2s
#
# See monitoring documentation for full Prometheus configuration examples.
#
# Metrics to Export
# ==================
#
# Counters (increment on event):
# - planner_macro_success_total
# - planner_macro_failure_total
# - planner_philosophy_success_total
# - planner_philosophy_failure_total
# - planner_structure_success_total
# - planner_structure_failure_total
# - planner_volume_success_total
# - planner_volume_failure_total
# - planner_template_success_total
# - planner_template_failure_total
# - planner_text_success_total
# - planner_text_failure_total
# - planner_persist_success_total
# - planner_persist_failure_total
#
# Histograms (timing):
# - planner_timing_duration_seconds (labels: metric)
#
# Gauges (stage tracking):
# - planner_stage_active (labels: stage, plan_id) - 1 if stage started, 0 if completed
