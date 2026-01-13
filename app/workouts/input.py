"""Input normalization for activity data.

This module handles ONLY field normalization (miles → meters, minutes → seconds).
No semantic interpretation of notes is performed here.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class SportType(str):
    """Sport type string."""

    pass


class ActivityInput(BaseModel):
    """Normalized activity input.

    All fields are normalized to canonical units:
    - Distance: meters
    - Duration: seconds
    - Notes: raw string (no interpretation)
    """

    sport: str = Field(description="Sport type (run, bike, swim, etc.)")
    total_distance_meters: int | None = Field(default=None, description="Total distance in meters")
    total_duration_seconds: int | None = Field(default=None, description="Total duration in seconds")
    notes: str | None = Field(default=None, description="Raw notes (semi-structured natural language)")

    @classmethod
    def from_raw(
        cls,
        sport: str,
        distance: str | int | float | None = None,
        duration: str | int | float | None = None,
        notes: str | None = None,
    ) -> ActivityInput:
        """Create ActivityInput from raw user input with automatic normalization.

        Converts:
        - miles → meters
        - km → meters
        - minutes → seconds
        - hours → seconds

        Args:
            sport: Sport type
            distance: Distance value (string with unit or numeric in meters)
            duration: Duration value (string with unit or numeric in seconds)
            notes: Raw notes

        Returns:
            Normalized ActivityInput

        Raises:
            ValueError: If distance or duration format is invalid
        """
        distance_meters = None
        if distance is not None:
            distance_meters = cls._normalize_distance(distance)

        duration_seconds = None
        if duration is not None:
            duration_seconds = cls._normalize_duration(duration)

        return cls(
            sport=sport,
            total_distance_meters=distance_meters,
            total_duration_seconds=duration_seconds,
            notes=notes,
        )

    @staticmethod
    def _normalize_distance(value: str | int | float) -> int:
        """Normalize distance to meters.

        Supports:
        - Numeric values (assumed to be in meters)
        - Strings with units: "5 miles", "10 km", "1600m", etc.

        Args:
            value: Distance value

        Returns:
            Distance in meters

        Raises:
            ValueError: If format is invalid
        """
        if isinstance(value, (int, float)):
            return int(value)

        if isinstance(value, str):
            value = value.strip().lower()

            # Extract number and unit
            match = re.match(r"([\d.]+)\s*([a-z]+)?", value)
            if not match:
                raise ValueError(f"Invalid distance format: {value}")

            number_str = match.group(1)
            unit = match.group(2) or "m"

            try:
                number = float(number_str)
            except ValueError as e:
                raise ValueError(f"Invalid distance number: {number_str}") from e

            # Convert to meters
            unit_lower = unit.lower()
            if unit_lower in {"m", "meter", "meters"}:
                return int(number)
            if unit_lower in {"km", "kilometer", "kilometers"}:
                return int(number * 1000)
            if unit_lower in {"mi", "mile", "miles"}:
                return int(number * 1609.34)
            if unit_lower in {"yd", "yard", "yards"}:
                return int(number * 0.9144)
            if unit_lower in {"ft", "foot", "feet"}:
                return int(number * 0.3048)

            raise ValueError(f"Unknown distance unit: {unit}")

        raise ValueError(f"Invalid distance type: {type(value)}")

    @staticmethod
    def _normalize_duration(value: str | int | float) -> int:
        """Normalize duration to seconds.

        Supports:
        - Numeric values (assumed to be in seconds)
        - Strings with units: "30 minutes", "1 hour", "45 min", etc.
        - Time format: "1:30:00" (hours:minutes:seconds)

        Args:
            value: Duration value

        Returns:
            Duration in seconds

        Raises:
            ValueError: If format is invalid
        """
        if isinstance(value, (int, float)):
            return int(value)

        if isinstance(value, str):
            value = value.strip().lower()

            # Try time format first (HH:MM:SS or MM:SS)
            time_match = re.match(r"(\d+):(\d+)(?::(\d+))?", value)
            if time_match:
                hours = int(time_match.group(1)) if time_match.group(3) is not None else 0
                minutes = int(time_match.group(2)) if time_match.group(3) is not None else int(time_match.group(1))
                seconds = int(time_match.group(3)) if time_match.group(3) is not None else int(time_match.group(2))
                return hours * 3600 + minutes * 60 + seconds

            # Extract number and unit
            match = re.match(r"([\d.]+)\s*([a-z]+)?", value)
            if not match:
                raise ValueError(f"Invalid duration format: {value}")

            number_str = match.group(1)
            unit = match.group(2) or "s"

            try:
                number = float(number_str)
            except ValueError as e:
                raise ValueError(f"Invalid duration number: {number_str}") from e

            # Convert to seconds
            unit_lower = unit.lower()
            if unit_lower in {"s", "sec", "second", "seconds"}:
                return int(number)
            if unit_lower in {"min", "minute", "minutes"}:
                return int(number * 60)
            if unit_lower in {"h", "hr", "hour", "hours"}:
                return int(number * 3600)

            raise ValueError(f"Unknown duration unit: {unit}")

        raise ValueError(f"Invalid duration type: {type(value)}")
