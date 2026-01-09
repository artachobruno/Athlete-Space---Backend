"""Activity file parser for FIT, GPX, and TCX formats.

Pure parsing module with no database access or business logic.
Converts file bytes into ParsedActivity model.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone

import fitparse
import gpxpy
import gpxpy.gpx
from loguru import logger
from lxml import etree
from pydantic import BaseModel, field_validator


class ParsedActivity(BaseModel):
    """Parsed activity data from file upload."""

    start_time: datetime
    duration_seconds: int
    distance_meters: float
    elevation_gain_meters: float | None
    activity_type: str

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        """Validate duration is positive."""
        if v <= 0:
            raise ValueError("duration_seconds must be > 0")
        return v

    @field_validator("distance_meters")
    @classmethod
    def validate_distance(cls, v: float) -> float:
        """Validate distance is positive."""
        if v <= 0:
            raise ValueError("distance_meters must be > 0")
        return v


# FIT sport type mapping
FIT_SPORT_MAP: dict[str, str] = {
    "running": "Run",
    "run": "Run",
    "cycling": "Ride",
    "bike": "Ride",
    "ride": "Ride",
    "swimming": "Swim",
    "swim": "Swim",
    "generic": "Run",  # Default to Run for generic activities
}


def parse_activity_file(file_bytes: bytes, filename: str) -> ParsedActivity:
    """Parse activity file (FIT, GPX, or TCX) into ParsedActivity.

    Args:
        file_bytes: Raw file bytes
        filename: Original filename (used to determine format)

    Returns:
        ParsedActivity with extracted data

    Raises:
        ValueError: If file format is unsupported or parsing fails
        ValueError: If required fields are missing or invalid
    """
    filename_lower = filename.lower()

    if filename_lower.endswith(".fit"):
        return _parse_fit(file_bytes)
    if filename_lower.endswith(".gpx"):
        return _parse_gpx(file_bytes)
    if filename_lower.endswith(".tcx"):
        return _parse_tcx(file_bytes)

    raise ValueError(f"Unsupported file format. Expected .fit, .gpx, or .tcx, got: {filename}")


def _extract_fit_start_time(record: fitparse.FitFile, current_start_time: datetime | None) -> datetime | None:
    """Extract start time from FIT file_id record."""
    for field in record:
        if field.name == "time_created" and field.value:
            start_time = field.value
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            return start_time
    return current_start_time


def _extract_fit_session_data(record: fitparse.FitFile) -> tuple[int | None, float | None, float | None, str | None]:
    """Extract session data from FIT session record."""
    total_timer_time: int | None = None
    total_distance: float | None = None
    total_ascent: float | None = None
    sport: str | None = None

    for field in record:
        if field.name == "total_timer_time":
            total_timer_time = int(field.value) if field.value is not None else None
        elif field.name == "total_distance":
            total_distance = float(field.value) if field.value is not None else None
        elif field.name == "total_ascent":
            total_ascent = float(field.value) if field.value is not None else None
        elif field.name == "sport":
            sport = str(field.value) if field.value is not None else None

    return total_timer_time, total_distance, total_ascent, sport


def _extract_fit_activity_timestamp(record: fitparse.FitFile, current_start_time: datetime | None) -> datetime | None:
    """Extract timestamp from FIT activity record."""
    if current_start_time:
        return current_start_time

    for field in record:
        if field.name == "timestamp" and field.value:
            start_time = field.value
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            return start_time
    return current_start_time


def _parse_fit(file_bytes: bytes) -> ParsedActivity:
    """Parse FIT file using fitparse.

    Args:
        file_bytes: Raw FIT file bytes

    Returns:
        ParsedActivity

    Raises:
        ValueError: If parsing fails or required fields are missing
    """
    try:
        fit_file = fitparse.FitFile(file_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse FIT file: {e}") from e

    # Extract data from FIT file
    start_time: datetime | None = None
    total_timer_time: int | None = None
    total_distance: float | None = None
    total_ascent: float | None = None
    sport: str | None = None

    for record in fit_file.get_messages():
        if record.name == "file_id":
            start_time = _extract_fit_start_time(record, start_time)
        elif record.name == "session":
            session_data = _extract_fit_session_data(record)
            total_timer_time = session_data[0]
            total_distance = session_data[1]
            total_ascent = session_data[2]
            sport = session_data[3]
        elif record.name == "activity":
            start_time = _extract_fit_activity_timestamp(record, start_time)

    # Validate required fields
    if not start_time:
        raise ValueError("FIT file missing start_time")
    if not total_timer_time or total_timer_time <= 0:
        raise ValueError("FIT file missing or invalid total_timer_time")
    if not total_distance or total_distance <= 0:
        raise ValueError("FIT file missing or invalid total_distance")

    # Normalize sport type
    activity_type = "Run"  # Default
    if sport:
        sport_lower = sport.lower()
        activity_type = FIT_SPORT_MAP.get(sport_lower, "Run")

    return ParsedActivity(
        start_time=start_time,
        duration_seconds=total_timer_time,
        distance_meters=total_distance,
        elevation_gain_meters=total_ascent,
        activity_type=activity_type,
    )


def _parse_gpx(file_bytes: bytes) -> ParsedActivity:
    """Parse GPX file using gpxpy.

    Args:
        file_bytes: Raw GPX file bytes

    Returns:
        ParsedActivity

    Raises:
        ValueError: If parsing fails or required fields are missing
    """
    try:
        gpx = gpxpy.parse(file_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse GPX file: {e}") from e

    if not gpx.tracks:
        raise ValueError("GPX file has no tracks")

    # Get first track
    track = gpx.tracks[0]
    if not track.segments:
        raise ValueError("GPX track has no segments")

    # Get all points from all segments
    all_points: list[gpxpy.gpx.GPXTrackPoint] = []
    for segment in track.segments:
        all_points.extend(segment.points)

    if not all_points:
        raise ValueError("GPX track has no points")

    # Get start time from first point
    first_point = all_points[0]
    if not first_point.time:
        raise ValueError("GPX file missing start_time (first point has no timestamp)")

    start_time = first_point.time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)

    # Get end time from last point
    last_point = all_points[-1]
    if not last_point.time:
        raise ValueError("GPX file missing end_time (last point has no timestamp)")

    end_time = last_point.time
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    # Calculate duration
    duration_delta = end_time - start_time
    duration_seconds = int(duration_delta.total_seconds())
    if duration_seconds <= 0:
        raise ValueError("GPX file has invalid duration (end_time <= start_time)")

    # Calculate distance using gpxpy
    distance_meters = track.length_2d()
    if distance_meters <= 0:
        raise ValueError("GPX file has invalid distance (<= 0)")

    # Calculate elevation gain (positive deltas)
    elevation_gain = 0.0
    prev_elevation: float | None = None
    for point in all_points:
        if point.elevation is not None:
            elevation = float(point.elevation)
            if prev_elevation is not None and elevation > prev_elevation:
                elevation_gain += elevation - prev_elevation
            prev_elevation = elevation

    # Default to Run if no activity type specified
    activity_type_str = track.type or "Run"
    activity_type_lower = activity_type_str.lower()
    activity_type = FIT_SPORT_MAP.get(activity_type_lower, "Run")

    return ParsedActivity(
        start_time=start_time,
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        elevation_gain_meters=elevation_gain if elevation_gain > 0 else None,
        activity_type=activity_type,
    )


def _parse_tcx(file_bytes: bytes) -> ParsedActivity:
    """Parse TCX file using lxml.

    Args:
        file_bytes: Raw TCX file bytes

    Returns:
        ParsedActivity

    Raises:
        ValueError: If parsing fails or required fields are missing
    """
    try:
        root = etree.fromstring(file_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse TCX file: {e}") from e

    # TCX namespace
    ns = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}

    # Find Activity element
    activity_elem = root.find(".//tcx:Activity", namespaces=ns)
    if activity_elem is None:
        raise ValueError("TCX file missing Activity element")

    # Get sport type
    sport_attr = activity_elem.get("Sport", "Running")
    sport_lower = sport_attr.lower()
    activity_type = FIT_SPORT_MAP.get(sport_lower, "Run")

    # Get first Lap (TCX files typically have one lap per activity)
    lap_elem = activity_elem.find(".//tcx:Lap", namespaces=ns)
    if lap_elem is None:
        raise ValueError("TCX file missing Lap element")

    # Get start time
    start_time_str = lap_elem.get("StartTime")
    if not start_time_str:
        raise ValueError("TCX file missing StartTime in Lap")

    try:
        # TCX timestamps are ISO format, may or may not have timezone
        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
    except Exception as e:
        raise ValueError(f"TCX file has invalid StartTime format: {e}") from e

    # Get TotalTimeSeconds
    total_time_elem = lap_elem.find("tcx:TotalTimeSeconds", namespaces=ns)
    if total_time_elem is None or total_time_elem.text is None:
        raise ValueError("TCX file missing TotalTimeSeconds")

    try:
        duration_seconds = int(float(total_time_elem.text))
    except (ValueError, TypeError) as e:
        raise ValueError(f"TCX file has invalid TotalTimeSeconds: {e}") from e

    if duration_seconds <= 0:
        raise ValueError("TCX file has invalid duration (TotalTimeSeconds <= 0)")

    # Get DistanceMeters
    distance_elem = lap_elem.find("tcx:DistanceMeters", namespaces=ns)
    if distance_elem is None or distance_elem.text is None:
        raise ValueError("TCX file missing DistanceMeters")

    try:
        distance_meters = float(distance_elem.text)
    except (ValueError, TypeError) as e:
        raise ValueError(f"TCX file has invalid DistanceMeters: {e}") from e

    if distance_meters <= 0:
        raise ValueError("TCX file has invalid distance (DistanceMeters <= 0)")

    # Get elevation gain from AltitudeMeters (if available)
    elevation_gain: float | None = None
    track_elem = lap_elem.find(".//tcx:Track", namespaces=ns)
    if track_elem is not None:
        altitude_points: list[float] = []
        for point in track_elem.findall(".//tcx:AltitudeMeters", namespaces=ns):
            if point.text is not None:
                with suppress(ValueError, TypeError):
                    altitude_points.append(float(point.text))

        if altitude_points:
            # Calculate positive elevation gain
            elevation_gain = 0.0
            prev_altitude: float | None = None
            for altitude in altitude_points:
                if prev_altitude is not None and altitude > prev_altitude:
                    elevation_gain += altitude - prev_altitude
                prev_altitude = altitude

            if elevation_gain <= 0:
                elevation_gain = None

    return ParsedActivity(
        start_time=start_time,
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        elevation_gain_meters=elevation_gain,
        activity_type=activity_type,
    )
