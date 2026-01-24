"""Cold stress computation module (v2.0).

Computes wind chill and cold stress index.

Wind chill formula (NOAA, LOCKED):
V_kmh = wind_mps * 3.6
WCT = 13.12 + 0.6215 * T - 11.37 * V^0.16 + 0.3965 * T * V^0.16

Cold stress index (CSI):
CSI = clamp((10 - wind_chill_c) / 25, 0.0, 1.0)
- >=10°C → no cold stress
- <=-15°C → max cold stress
"""

from __future__ import annotations

import math


def compute_wind_chill_c(temp_c: float, wind_mps: float) -> float:
    """Compute wind chill temperature in Celsius (NOAA formula).

    Formula (LOCKED):
    V_kmh = wind_mps * 3.6
    WCT = 13.12 + 0.6215 * T - 11.37 * V^0.16 + 0.3965 * T * V^0.16

    Validity guard (NOAA):
    - Only valid when temp <= 10°C and wind >= 1.3 m/s (~4.7 km/h)
    - Returns raw temperature if conditions invalid

    Args:
        temp_c: Air temperature in Celsius
        wind_mps: Wind speed in meters per second

    Returns:
        Wind chill temperature in Celsius (or raw temp if formula invalid)
    """
    # NOAA validity guard: wind chill only valid when temp <= 10°C and wind >= 1.3 m/s
    if temp_c > 10.0 or wind_mps < 1.3:
        return temp_c

    # Convert wind speed to km/h
    v_kmh = wind_mps * 3.6

    # Apply NOAA wind chill formula
    v_power = math.pow(v_kmh, 0.16)
    return 13.12 + (0.6215 * temp_c) - (11.37 * v_power) + (0.3965 * temp_c * v_power)


def compute_cold_stress_index(wind_chill_c: float) -> float:
    """Compute cold stress index from wind chill.

    Formula (LOCKED):
    CSI = clamp((10 - wind_chill_c) / 25, 0.0, 1.0)

    Interpretation:
    - >=10°C → no cold stress (CSI = 0.0)
    - <=-15°C → max cold stress (CSI = 1.0)

    Args:
        wind_chill_c: Wind chill temperature in Celsius

    Returns:
        Cold stress index (0.0-1.0)
    """
    csi = (10.0 - wind_chill_c) / 25.0
    return max(0.0, min(1.0, csi))
