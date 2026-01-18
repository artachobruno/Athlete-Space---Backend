"""Training load simulation.

Project training load forward assuming current plan is followed.
Deterministic, pure projection with no modifications.
"""

from app.tools.interfaces import PlannedSession, TrainingMetrics


def simulate_training_load(
    planned_sessions: list[PlannedSession],
    current_metrics: TrainingMetrics,
    horizon_days: int = 14,
) -> dict:
    """Simulate forward CTL / ATL / TSB assuming planned sessions are executed.

    This function MUST NOT:
    - modify plans
    - adjust sessions
    - optimize load

    Args:
        planned_sessions: List of planned sessions (should be ordered chronologically)
        current_metrics: Current training metrics snapshot
        horizon_days: Number of days to project forward (default: 14)

    Returns:
        Dictionary with:
        - projected_ctl: List of projected CTL values
        - projected_atl: List of projected ATL values
        - projected_tsb: List of projected TSB values (CTL - ATL)
    """
    ctl = current_metrics.ctl
    atl = current_metrics.atl

    ctl_series = []
    atl_series = []

    # Only simulate up to horizon_days
    sessions_to_simulate = planned_sessions[:horizon_days]

    for session in sessions_to_simulate:
        load = session.target_load

        # Exponential moving average formulas
        # ATL: 7-day window (tau ~ 7) -> alpha = 1/7 ≈ 0.143, simplified to 0.1
        # CTL: 42-day window (tau ~ 42) -> alpha = 1/42 ≈ 0.024, simplified to 0.01
        atl = atl * 0.9 + load * 0.1
        ctl = ctl * 0.99 + load * 0.01

        atl_series.append(atl)
        ctl_series.append(ctl)

    # Calculate TSB as CTL - ATL
    tsb_series = [c - a for c, a in zip(ctl_series, atl_series, strict=False)]

    return {
        "projected_ctl": ctl_series,
        "projected_atl": atl_series,
        "projected_tsb": tsb_series,
    }
