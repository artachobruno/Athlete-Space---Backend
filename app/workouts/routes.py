"""Workout API routes.

Read-only endpoints for workout data.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.db.models import Activity
from app.db.session import get_session
from app.workouts.compliance_service import ComplianceService
from app.workouts.execution_models import StepCompliance, WorkoutComplianceSummary, WorkoutExecution
from app.workouts.export_service import WorkoutExportService
from app.workouts.interpretation_service import InterpretationService
from app.workouts.models import Workout, WorkoutExport, WorkoutStep
from app.workouts.parsing import parse_notes_stub
from app.workouts.schemas import (
    AttachActivityRequest,
    CreateExportRequest,
    ParseNotesRequest,
    ParseNotesResponse,
    StepComplianceSchema,
    StepInterpretationSchema,
    StructuredWorkoutComparison,
    StructuredWorkoutInfo,
    StructuredWorkoutResponse,
    WorkoutComplianceResponse,
    WorkoutExportResponse,
    WorkoutInterpretationResponse,
    WorkoutSchema,
    WorkoutStepGroup,
    WorkoutStepSchema,
    WorkoutStepsUpdateRequest,
    WorkoutTarget,
    WorkoutTimelineResponse,
)
from app.workouts.step_grouping import detect_repeating_patterns
from app.workouts.step_utils import infer_step_name
from app.workouts.timeline import build_workout_timeline

router = APIRouter(prefix="/workouts", tags=["workouts"])


def get_workout_or_404(session: Session, workout_id: UUID, user_id: str) -> Workout:
    """Get workout by ID or raise 404.

    Args:
        session: Database session
        workout_id: Workout UUID
        user_id: User ID for authorization

    Returns:
        Workout model instance

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
    """
    stmt = (
        select(Workout)
        .where(Workout.id == str(workout_id))
        .where(Workout.user_id == user_id)
    )
    result = session.execute(stmt)
    workout = result.scalar_one_or_none()

    if workout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workout {workout_id} not found",
        )

    return workout


def get_workout_steps(session: Session, workout_id: UUID) -> list[WorkoutStep]:
    """Get workout steps ordered by order.

    Args:
        session: Database session
        workout_id: Workout UUID

    Returns:
        List of WorkoutStep model instances ordered by order
    """
    stmt = (
        select(WorkoutStep)
        .where(WorkoutStep.workout_id == str(workout_id))
        .order_by(WorkoutStep.order)
    )
    result = session.execute(stmt)
    return list(result.scalars().all())


@router.post("/parse-notes", response_model=ParseNotesResponse)
def parse_notes(
    request: ParseNotesRequest,
    user_id: str = Depends(get_current_user_id),
) -> ParseNotesResponse:
    """Parse workout notes into structured steps.

    This endpoint parses free-form workout notes into structured workout steps.
    It is completely non-blocking and non-mutating - it does NOT persist any data.

    IMPORTANT: This endpoint never writes to the database, creates workouts,
    or triggers reconciliation. It only returns parsed structure.

    Args:
        request: Parse notes request with sport, session_type, notes, etc.
        user_id: Authenticated user ID (logged but not used in stub)

    Returns:
        ParseNotesResponse with parsed steps or unavailable status

    Status codes:
        - 200: Always returns 200 (never fails)
    """
    logger.info(
        "Workout notes parse requested",
        user_id=user_id,
        sport=request.sport,
        session_type=request.session_type,
        has_distance=request.total_distance_meters is not None,
        has_duration=request.total_duration_seconds is not None,
        notes_length=len(request.notes),
    )

    if not settings.workout_notes_parsing_enabled:
        return parse_notes_stub(request)

    return parse_notes_stub(request)


@router.get("/{workout_id}/structured", response_model=StructuredWorkoutResponse)
def get_structured_workout(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> StructuredWorkoutResponse:
    """Get structured workout data.

    Returns workout structure with steps array and comparison data.
    Never returns 404 - returns status "not_found" if workout doesn't exist.

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        Dictionary with status, workout, steps, and comparison data
    """
    with get_session() as session:
        stmt = (
            select(Workout)
            .where(Workout.id == str(workout_id))
            .where(Workout.user_id == user_id)
        )
        result = session.execute(stmt)
        workout = result.scalar_one_or_none()

        if not workout:
            return StructuredWorkoutResponse(
                status="not_found",
                workout_id=str(workout_id),
                workout=None,
                steps=[],
                groups=[],
                structured_available=False,
                comparison=None,
            )

        steps = get_workout_steps(session, workout_id)

        # Normalize step names - ensure every step has a name
        for step in steps:
            if not step.purpose and not step.instructions:
                # Infer name if missing
                inferred_name = infer_step_name(step, workout.raw_notes)
                step.purpose = inferred_name

        # Detect repeating patterns for grouping
        groups = detect_repeating_patterns(steps)
        step_id_to_group: dict[str, str] = {}
        for group in groups:
            for step_id in group.step_ids:
                step_id_to_group[step_id] = group.group_id

        # Build step dictionaries with enhanced schema
        step_dicts = []
        for step in steps:
            # Build target object if target data exists
            target: dict[str, str | float | None] | None = None
            if step.target_metric or step.target_min is not None or step.target_max is not None or step.target_value is not None:
                # Determine unit based on metric type
                unit = None
                if step.target_metric == "pace":
                    unit = "min/km"
                elif step.target_metric == "hr":
                    unit = "bpm"
                elif step.target_metric == "power":
                    unit = "W"
                elif step.target_metric == "rpe":
                    unit = "RPE"

                target = {
                    "type": step.target_metric,
                    "min": step.target_min,
                    "max": step.target_max,
                    "value": step.target_value,
                    "unit": unit,
                }

            step_name = step.purpose or infer_step_name(step, workout.raw_notes)
            step_kind = step.type  # Use type as kind for now
            step_intensity = step.intensity_zone  # Use intensity_zone as intensity

            step_dict = {
                "id": step.id,
                "order": step.order,
                "name": step_name,
                "type": step.type,
                "kind": step_kind,
                "intensity": step_intensity,
                "duration_seconds": step.duration_seconds,
                "distance_meters": step.distance_meters,
                "target": target,
                "target_metric": step.target_metric,
                "target_min": step.target_min,
                "target_max": step.target_max,
                "target_value": step.target_value,
                "repeat_group_id": step_id_to_group.get(step.id),
                "instructions": step.instructions,
                "purpose": step.purpose,
                "inferred": step.inferred,
            }
            step_dicts.append(step_dict)

        # Build groups list for response
        groups_list = [
            WorkoutStepGroup(
                group_id=group.group_id,
                repeat=group.repeat,
                step_ids=group.step_ids,
            )
            for group in groups
        ]

        # Get comparison data
        comparison_stmt = select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == str(workout_id))
        comparison_result = session.execute(comparison_stmt)
        comparison = comparison_result.scalar_one_or_none()

        comparison_dict: StructuredWorkoutComparison | None = None
        if comparison:
            comparison_dict = StructuredWorkoutComparison(
                score=comparison.overall_compliance_pct,
                completed=comparison.completed,
                summary_json={
                    "overall_compliance_pct": comparison.overall_compliance_pct,
                    "total_pause_seconds": comparison.total_pause_seconds,
                    "completed": comparison.completed,
                },
            )

        # Determine structured availability
        structured_available = bool(steps and len(steps) > 0)

        return StructuredWorkoutResponse(
            status="ok",
            workout=StructuredWorkoutInfo(
                id=workout.id,
                sport=workout.sport,
                source=workout.source,
                total_distance_meters=workout.total_distance_meters,
                total_duration_seconds=workout.total_duration_seconds,
                parse_status=workout.parse_status or "pending",
            ),
            steps=step_dicts,
            groups=groups_list,
            structured_available=structured_available,
            comparison=comparison_dict,
        )


@router.put("/{workout_id}/steps", response_model=StructuredWorkoutResponse)
def update_workout_steps(
    workout_id: UUID,
    request: WorkoutStepsUpdateRequest,
    user_id: str = Depends(get_current_user_id),
) -> StructuredWorkoutResponse:
    """Update workout steps.

    Replaces all steps for the workout with the provided steps.
    Steps are validated, persisted, and groups are recomputed.

    Args:
        workout_id: Workout UUID
        request: Steps update request
        user_id: Authenticated user ID

    Returns:
        Updated structured workout response with recomputed groups

    Raises:
        HTTPException: If workout not found, validation fails, or user mismatch
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        stmt = (
            select(Workout)
            .where(Workout.id == str(workout_id))
            .where(Workout.user_id == user_id)
        )
        result = session.execute(stmt)
        workout = result.scalar_one_or_none()

        if not workout:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workout {workout_id} not found",
            )

        # Validate steps
        errors: list[str] = []
        for idx, step in enumerate(request.steps):
            # Must have either duration or distance
            if step.duration_seconds is None and step.distance_meters is None:
                errors.append(f"Step {idx + 1} (order {step.order}): must have either duration_seconds or distance_meters")

            # Target validation
            if (
                step.target
                and step.target.min is not None
                and step.target.max is not None
                and step.target.min > step.target.max
            ):
                errors.append(f"Step {idx + 1} (order {step.order}): target min ({step.target.min}) > max ({step.target.max})")

            # Legacy target validation
            if (
                step.target_min is not None
                and step.target_max is not None
                and step.target_min > step.target_max
            ):
                errors.append(f"Step {idx + 1} (order {step.order}): target_min ({step.target_min}) > target_max ({step.target_max})")

        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"errors": errors},
            )

        # Delete existing steps
        delete_stmt = delete(WorkoutStep).where(WorkoutStep.workout_id == str(workout_id))
        session.execute(delete_stmt)

        # Create new steps from request
        new_steps: list[WorkoutStep] = []
        for step_update in request.steps:
            # Extract target data
            target_metric = step_update.target.type if step_update.target else step_update.target_metric
            target_min = step_update.target.min if step_update.target else step_update.target_min
            target_max = step_update.target.max if step_update.target else step_update.target_max
            target_value = step_update.target.value if step_update.target else step_update.target_value

            # Use purpose as name if provided, otherwise use name field
            step_name = step_update.purpose or step_update.name

            new_step = WorkoutStep(
                id=str(step_update.id),
                workout_id=str(workout_id),
                order=step_update.order,
                type=step_update.type,
                duration_seconds=step_update.duration_seconds,
                distance_meters=step_update.distance_meters,
                target_metric=target_metric,
                target_min=target_min,
                target_max=target_max,
                target_value=target_value,
                intensity_zone=step_update.intensity,
                instructions=step_update.instructions,
                purpose=step_name,
                inferred=step_update.inferred,
            )
            new_steps.append(new_step)
            session.add(new_step)

        session.commit()

        # Re-run name inference for steps that need it
        for step in new_steps:
            if not step.purpose:
                inferred_name = infer_step_name(step, workout.raw_notes)
                step.purpose = inferred_name

        session.commit()

        # Reload steps to get fresh data
        steps = get_workout_steps(session, workout_id)

        # Detect repeating patterns for grouping
        groups = detect_repeating_patterns(steps)
        step_id_to_group: dict[str, str] = {}
        for group in groups:
            for step_id in group.step_ids:
                step_id_to_group[step_id] = group.group_id

        # Build step dictionaries with enhanced schema
        step_dicts = []
        for step in steps:
            # Build target object if target data exists
            target: dict[str, str | float | None] | None = None
            if step.target_metric or step.target_min is not None or step.target_max is not None or step.target_value is not None:
                unit = None
                if step.target_metric == "pace":
                    unit = "min/km"
                elif step.target_metric == "hr":
                    unit = "bpm"
                elif step.target_metric == "power":
                    unit = "W"
                elif step.target_metric == "rpe":
                    unit = "RPE"

                target = {
                    "type": step.target_metric,
                    "min": step.target_min,
                    "max": step.target_max,
                    "value": step.target_value,
                    "unit": unit,
                }

            step_name = step.purpose or infer_step_name(step, workout.raw_notes)
            step_kind = step.type
            step_intensity = step.intensity_zone

            step_dict = {
                "id": step.id,
                "order": step.order,
                "name": step_name,
                "type": step.type,
                "kind": step_kind,
                "intensity": step_intensity,
                "duration_seconds": step.duration_seconds,
                "distance_meters": step.distance_meters,
                "target": target,
                "target_metric": step.target_metric,
                "target_min": step.target_min,
                "target_max": step.target_max,
                "target_value": step.target_value,
                "repeat_group_id": step_id_to_group.get(step.id),
                "instructions": step.instructions,
                "purpose": step.purpose,
                "inferred": step.inferred,
            }
            step_dicts.append(step_dict)

        # Build groups list for response
        groups_list = [
            WorkoutStepGroup(
                group_id=group.group_id,
                repeat=group.repeat,
                step_ids=group.step_ids,
            )
            for group in groups
        ]

        # Determine structured availability
        structured_available = bool(steps and len(steps) > 0)

        return StructuredWorkoutResponse(
            status="ok",
            workout=StructuredWorkoutInfo(
                id=workout.id,
                sport=workout.sport,
                source=workout.source,
                total_distance_meters=workout.total_distance_meters,
                total_duration_seconds=workout.total_duration_seconds,
                parse_status=workout.parse_status or "pending",
            ),
            steps=step_dicts,
            groups=groups_list,
            structured_available=structured_available,
            comparison=None,  # Comparison not recomputed on step update
        )


@router.get("/{workout_id}", response_model=WorkoutSchema)
def get_workout(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkoutSchema:
    """Get a workout by ID.

    Returns workout with steps ordered by step.order.
    Only returns workouts belonging to the authenticated user.
    """
    with get_session() as session:
        # Query workout
        stmt = (
            select(Workout)
            .where(Workout.id == str(workout_id))
            .where(Workout.user_id == user_id)
        )
        result = session.execute(stmt)
        workout = result.scalar_one_or_none()

        if workout is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workout {workout_id} not found",
            )

        # Query steps ordered by order
        steps_stmt = (
            select(WorkoutStep)
            .where(WorkoutStep.workout_id == str(workout_id))
            .order_by(WorkoutStep.order)
        )
        steps_result = session.execute(steps_stmt)
        steps = steps_result.scalars().all()

        # Convert to schemas
        step_schemas = [
            WorkoutStepSchema(
                id=UUID(step.id),
                order=step.order,
                type=step.type,
                duration_seconds=step.duration_seconds,
                distance_meters=step.distance_meters,
                target_metric=step.target_metric,
                target_min=step.target_min,
                target_max=step.target_max,
                target_value=step.target_value,
                instructions=step.instructions,
                purpose=step.purpose,
                inferred=step.inferred,
            )
            for step in steps
        ]

        return WorkoutSchema(
            id=UUID(workout.id),
            sport=workout.sport,
            source=workout.source,
            total_duration_seconds=workout.total_duration_seconds,
            total_distance_meters=workout.total_distance_meters,
            steps=step_schemas,
        )


@router.get("/{workout_id}/timeline", response_model=WorkoutTimelineResponse)
def get_workout_timeline(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkoutTimelineResponse:
    """Get workout timeline with time-aligned segments.

    Returns contiguous time-aligned segments for rendering:
    - intensity target bands
    - step boundaries
    - purpose ribbon

    Only returns workouts belonging to the authenticated user.
    Requires duration-based steps (distance-based steps not supported in Phase 2).

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        WorkoutTimelineResponse with time-aligned segments

    Raises:
        HTTPException: 404 if workout not found
        ValueError: If any step has None duration_seconds
    """
    with get_session() as session:
        workout = get_workout_or_404(session, workout_id, user_id)
        steps = get_workout_steps(session, workout_id)

        return build_workout_timeline(workout, steps)


@router.post("/{workout_id}/exports", response_model=WorkoutExportResponse, status_code=status.HTTP_201_CREATED)
def create_workout_export(
    workout_id: UUID,
    request: CreateExportRequest,
    user_id: str = Depends(get_current_user_id),
) -> WorkoutExportResponse:
    """Create a new workout export.

    Creates an export record and immediately processes it (inline execution).
    Returns the export with status (queued, building, ready, or failed).

    Args:
        workout_id: Workout UUID
        request: Export creation request with export_type
        user_id: Authenticated user ID

    Returns:
        WorkoutExportResponse with export details

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
        HTTPException: 400 if export_type is invalid
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        # Create export
        export = WorkoutExportService.create_export(session, str(workout_id), request.export_type)

        # Run export inline (MVP - can be moved to background task later)
        export = WorkoutExportService.run_export(session, export.id)

        return WorkoutExportResponse(
            id=UUID(export.id),
            workout_id=UUID(export.workout_id),
            export_type=export.export_type,
            status=export.status,
            file_path=export.file_path,
            error_message=export.error_message,
            created_at=export.created_at.isoformat(),
        )


@router.get("/{workout_id}/exports", response_model=list[WorkoutExportResponse])
def list_workout_exports(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> list[WorkoutExportResponse]:
    """List all exports for a workout.

    Returns all exports for the workout, ordered by creation date (newest first).

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        List of WorkoutExportResponse

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        # List exports
        exports = WorkoutExportService.list_exports(session, str(workout_id))

        return [
            WorkoutExportResponse(
                id=UUID(export.id),
                workout_id=UUID(export.workout_id),
                export_type=export.export_type,
                status=export.status,
                file_path=export.file_path,
                error_message=export.error_message,
                created_at=export.created_at.isoformat(),
            )
            for export in exports
        ]


@router.get("/exports/{export_id}/download")
def download_workout_export(
    export_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> FileResponse:
    """Download a workout export file.

    Only returns files for exports that are ready and belong to workouts owned by the user.

    Args:
        export_id: Export UUID
        user_id: Authenticated user ID

    Returns:
        FileResponse with export file

    Raises:
        HTTPException: 404 if export not found
        HTTPException: 403 if export doesn't belong to user's workout
        HTTPException: 400 if export is not ready
    """
    with get_session() as session:
        # Load export
        export = WorkoutExportService.get_export(session, str(export_id))
        if export is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Export {export_id} not found",
            )

        # Verify workout belongs to user
        workout_stmt = select(Workout).where(Workout.id == export.workout_id).where(Workout.user_id == user_id)
        workout_result = session.execute(workout_stmt)
        workout = workout_result.scalar_one_or_none()

        if workout is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Export {export_id} does not belong to your workouts",
            )

        # Check export status
        if export.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Export {export_id} is not ready (status: {export.status})",
            )

        if not export.file_path:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Export {export_id} is ready but file_path is missing",
            )

        # Verify file exists
        file_path = Path(export.file_path)
        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Export file not found: {export.file_path}",
            )

        # Determine content type based on export type
        content_type_map: dict[str, str] = {
            "fit": "application/octet-stream",
        }
        content_type = content_type_map.get(export.export_type, "application/octet-stream")

        # Generate filename
        filename = f"workout_{export.workout_id[:8]}.{export.export_type}"

        return FileResponse(
            path=str(file_path),
            media_type=content_type,
            filename=filename,
        )


@router.post("/{workout_id}/attach-activity", status_code=status.HTTP_201_CREATED)
def attach_activity(
    workout_id: UUID,
    request: AttachActivityRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Attach an activity to a workout.

    Creates a WorkoutExecution record linking the workout to the executed activity.
    Does NOT compute compliance - use POST /workouts/{id}/compute-compliance for that.

    Args:
        workout_id: Workout UUID
        request: Request with activity_id
        user_id: Authenticated user ID

    Returns:
        Success message with execution ID

    Raises:
        HTTPException: 404 if workout or activity not found
        HTTPException: 403 if activity doesn't belong to user
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        # Verify activity exists and belongs to user
        activity_stmt = select(Activity).where(Activity.id == str(request.activity_id)).where(Activity.user_id == user_id)
        activity_result = session.execute(activity_stmt)
        activity = activity_result.scalar_one_or_none()

        if activity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {request.activity_id} not found or doesn't belong to user",
            )

        # Create execution
        execution = WorkoutExecution(
            workout_id=str(workout_id),
            activity_id=str(request.activity_id),
        )
        session.add(execution)
        session.commit()

        return {
            "message": "Activity attached successfully",
            "execution_id": execution.id,
        }


@router.post("/{workout_id}/compute-compliance")
def compute_compliance(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Compute compliance metrics for a workout execution.

    Computes deterministic compliance metrics by comparing planned workout steps
    with executed activity samples using time-aligned matching.

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        Success message

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
        HTTPException: 400 if no execution found or computation fails
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        try:
            ComplianceService.compute_and_persist(session, str(workout_id))
            session.commit()
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e

        return {
            "message": "Compliance computed successfully",
        }


@router.get("/{workout_id}/compliance", response_model=WorkoutComplianceResponse)
def get_workout_compliance(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkoutComplianceResponse:
    """Get compliance metrics for a workout.

    Returns compliance metrics including overall compliance percentage,
    step-level compliance, and completion status.

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        WorkoutComplianceResponse with compliance metrics

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
        HTTPException: 404 if no compliance data found
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        # Get summary
        summary_stmt = select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == str(workout_id))
        summary_result = session.execute(summary_stmt)
        summary = summary_result.scalar_one_or_none()

        if summary is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No compliance data found for workout {workout_id}. Compute compliance first.",
            )

        # Get step compliance records
        steps = get_workout_steps(session, workout_id)
        step_compliance_stmt = select(StepCompliance).where(StepCompliance.workout_step_id.in_([s.id for s in steps]))
        step_compliance_result = session.execute(step_compliance_stmt)
        step_compliance_records = list(step_compliance_result.scalars().all())

        # Build step_id -> compliance map
        step_compliance_map = {record.workout_step_id: record for record in step_compliance_records}

        # Build step compliance schemas ordered by step order
        step_schemas: list[StepComplianceSchema] = []
        for step in steps:
            compliance = step_compliance_map.get(step.id)
            if compliance:
                step_schemas.append(
                    StepComplianceSchema(
                        order=step.order,
                        compliance_pct=compliance.compliance_pct,
                        time_in_range_seconds=compliance.time_in_range_seconds,
                        overshoot_seconds=compliance.overshoot_seconds,
                        undershoot_seconds=compliance.undershoot_seconds,
                        pause_seconds=compliance.pause_seconds,
                    )
                )

        return WorkoutComplianceResponse(
            overall_compliance_pct=summary.overall_compliance_pct,
            total_pause_seconds=summary.total_pause_seconds,
            completed=summary.completed,
            steps=step_schemas,
        )


@router.post("/{workout_id}/interpret", status_code=status.HTTP_200_OK)
async def interpret_workout(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Trigger LLM interpretation for a workout execution.

    Generates coaching feedback for the workout and its steps.
    Requires compliance data to exist first.

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        Success message

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
        HTTPException: 400 if no compliance data found or interpretation fails
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        try:
            service = InterpretationService()
            await service.interpret_workout(session, str(workout_id))
            session.commit()
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Interpretation failed: {e!s}",
            ) from e

        return {
            "message": "Interpretation generated successfully",
        }


@router.get("/{workout_id}/interpretation", response_model=WorkoutInterpretationResponse)
def get_workout_interpretation(
    workout_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkoutInterpretationResponse:
    """Get LLM interpretation for a workout.

    Returns coaching feedback including verdict, summary, and step-level feedback.

    Args:
        workout_id: Workout UUID
        user_id: Authenticated user ID

    Returns:
        WorkoutInterpretationResponse with interpretation data

    Raises:
        HTTPException: 404 if workout not found or doesn't belong to user
        HTTPException: 404 if no interpretation data found
    """
    with get_session() as session:
        # Verify workout exists and belongs to user
        get_workout_or_404(session, workout_id, user_id)

        # Get summary
        summary_stmt = select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == str(workout_id))
        summary_result = session.execute(summary_stmt)
        summary = summary_result.scalar_one_or_none()

        if summary is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No compliance data found for workout {workout_id}. Compute compliance first.",
            )

        # Check if interpretation exists
        if summary.llm_verdict is None and summary.llm_summary is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No interpretation found for workout {workout_id}. Generate interpretation first.",
            )

        # Get step compliance records with interpretations
        steps = get_workout_steps(session, workout_id)
        step_compliance_stmt = select(StepCompliance).where(StepCompliance.workout_step_id.in_([s.id for s in steps]))
        step_compliance_result = session.execute(step_compliance_stmt)
        step_compliance_records = list(step_compliance_result.scalars().all())

        # Build step_id -> compliance map
        step_compliance_map = {record.workout_step_id: record for record in step_compliance_records}

        # Build step interpretation schemas ordered by step order
        step_schemas: list[StepInterpretationSchema] = []
        for step in steps:
            compliance = step_compliance_map.get(step.id)
            if compliance:
                step_schemas.append(
                    StepInterpretationSchema(
                        order=step.order,
                        rating=compliance.llm_rating,
                        summary=compliance.llm_summary,
                        coaching_tip=compliance.llm_tip,
                        confidence=compliance.llm_confidence,
                    )
                )

        return WorkoutInterpretationResponse(
            verdict=summary.llm_verdict,
            summary=summary.llm_summary,
            steps=step_schemas,
        )
