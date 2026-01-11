from datetime import date, datetime, timezone
from typing import Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from loguru import logger
from sqlalchemy import select

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import AthleteProfileData, CoachDeps, RaceProfileData, TrainingPreferencesData
from app.coach.config.models import USER_FACING_MODEL
from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.mcp_client import MCPError, call_tool
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.coach.utils.context_management import save_context
from app.coach.utils.schemas import (
    ActionStepResponse,
    CoachChatRequest,
    CoachChatResponse,
    ProgressEventResponse,
    ProgressResponse,
)
from app.core.conversation_id import get_conversation_id
from app.core.conversation_ownership import validate_conversation_ownership
from app.core.message import Message, normalize_message
from app.core.observe import set_association_properties, trace
from app.core.redis_conversation_store import write_message
from app.core.trace_metadata import get_trace_metadata
from app.db.message_repository import persist_message
from app.db.models import AthleteProfile, CoachMessage, CoachProgressEvent, StravaAccount, StravaAuth, UserSettings
from app.db.session import get_session
from app.state.api_helpers import get_training_data, get_user_id_from_athlete_id
from app.upload.activity_handler import upload_activity_from_chat
from app.upload.plan_handler import upload_plan_from_chat
from app.upload.upload_detector import is_activity_upload, is_plan_upload

router = APIRouter(prefix="/coach", tags=["coach"])


def _get_athlete_id() -> int | None:
    """Get athlete ID from the first StravaAuth entry.

    Returns:
        Athlete ID or None if no Strava auth exists
    """
    with get_session() as db:
        result = db.execute(select(StravaAuth)).first()
        if not result:
            return None
        return result[0].athlete_id


def _is_history_empty(athlete_id: int | None = None) -> bool:
    """Check if coach chat history is empty for an athlete.

    Args:
        athlete_id: Optional athlete ID. If None, checks the first athlete from StravaAuth.

    Returns:
        True if history is empty (cold start), False otherwise.
    """
    if athlete_id is None:
        athlete_id = _get_athlete_id()
        if athlete_id is None:
            logger.debug("No athlete_id found, treating as cold start")
            return True

    # Convert athlete_id to user_id
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.debug("No user_id found for athlete_id, treating as cold start", athlete_id=athlete_id)
        return True

    with get_session() as db:
        message_count = db.query(CoachMessage).filter(CoachMessage.user_id == user_id).count()
        logger.debug(
            "Checking coach message history",
            athlete_id=athlete_id,
            user_id=user_id,
            message_count=message_count,
            is_empty=message_count == 0,
        )

        # Also check what user_ids actually exist in the table for debugging
        if message_count == 0:
            existing_user_ids = db.query(CoachMessage.user_id).distinct().all()
            existing_ids_list = [row[0] for row in existing_user_ids] if existing_user_ids else []
            logger.debug(
                "No messages found for user_id, checking existing user_ids in table",
                searched_athlete_id=athlete_id,
                searched_user_id=user_id,
                existing_user_ids=existing_ids_list,
                total_messages_in_table=db.query(CoachMessage).count(),
            )

        return message_count == 0


def _is_simple_acknowledgment(message: str) -> bool:
    """Check if message is a simple activity acknowledgment that doesn't need agent processing.

    Args:
        message: User's message

    Returns:
        True if message is a simple acknowledgment that should be handled via fast-path
    """
    normalized = message.strip().lower()
    simple_acks = {
        "i ran yesterday",
        "i ran today",
        "i worked out",
        "i trained today",
        "ran yesterday",
        "ran today",
        "worked out",
        "trained today",
    }
    return normalized in simple_acks


def get_or_create_athlete_id(db, user_id: str) -> int | None:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        db: Database session
        user_id: User ID to resolve athlete_id for

    Returns:
        Athlete ID as integer or None if not found
    """
    result = db.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
    if not result:
        return None
    return int(result[0].athlete_id)


@router.post("/chat", response_model=CoachChatResponse)
async def coach_chat(
    req: CoachChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(validate_conversation_ownership),
) -> CoachChatResponse:
    """Handle coach chat request using orchestrator agent."""
    # Get conversation_id from request context (set by middleware)
    # Ownership is validated by validate_conversation_ownership dependency
    conversation_id = get_conversation_id(request)

    # Normalize user message before processing
    try:
        normalized_user_message = normalize_message(
            raw_input=req.message,
            conversation_id=conversation_id,
            user_id=user_id,
            role="user",
        )
        # Write normalized user message to Redis (B26)
        # This happens after normalization and token counting
        write_message(normalized_user_message)

        # Persist normalized user message to Postgres (B29)
        # This happens asynchronously and never blocks the request
        background_tasks.add_task(persist_message, normalized_user_message)
    except ValueError as e:
        logger.error(
            "Failed to normalize user message",
            conversation_id=conversation_id,
            user_id=user_id,
            error=str(e),
        )
        return CoachChatResponse(
            intent="error",
            reply="Invalid message format. Please try again.",
            conversation_id=conversation_id,
            response_type="explanation",
            show_plan=False,
            plan_items=None,
        )

    logger.info(
        "Coach chat request",
        message=normalized_user_message.content,
        conversation_id=conversation_id,
    )

    # Get athlete ID
    athlete_id = _get_athlete_id()
    athlete_id_type: str = type(athlete_id).__name__ if athlete_id is not None else "None"
    logger.debug(
        "Retrieved athlete_id for coach chat",
        conversation_id=conversation_id,
        athlete_id=athlete_id,
        athlete_id_type=athlete_id_type,
    )
    if athlete_id is None:
        logger.warning(
            "No athlete ID found, cannot process coach chat",
            conversation_id=conversation_id,
        )
        return CoachChatResponse(
            intent="error",
            reply="Please connect your Strava account first.",
            conversation_id=conversation_id,
            response_type="explanation",
            show_plan=False,
            plan_items=None,
        )

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty(athlete_id)
    logger.debug(
        "Cold start check result",
        conversation_id=conversation_id,
        athlete_id=athlete_id,
        history_empty=history_empty,
    )

    # Get user_id from athlete_id
    # athlete_id is guaranteed to be non-None here due to check above
    if athlete_id is None:
        raise RuntimeError("athlete_id is None after validation check")
    resolved_user_id = get_user_id_from_athlete_id(athlete_id)
    if resolved_user_id is None:
        logger.warning(
            "Cannot find user_id for athlete_id",
            conversation_id=conversation_id,
            athlete_id=athlete_id,
        )
        return CoachChatResponse(
            intent="error",
            reply="Unable to find user account. Please reconnect your Strava account.",
            conversation_id=conversation_id,
            response_type="explanation",
            show_plan=False,
            plan_items=None,
        )
    # After None check, resolved_user_id is guaranteed to be str
    # Use it for all subsequent operations to ensure consistency with athlete_id
    user_id = resolved_user_id

    # Handle cold start
    if history_empty:
        logger.info(
            "Cold start detected - providing welcome message",
            conversation_id=conversation_id,
        )
        try:
            training_data = get_training_data(user_id=user_id, days=req.days)
            athlete_state = build_athlete_state(
                ctl=training_data.ctl,
                atl=training_data.atl,
                tsb=training_data.tsb,
                daily_load=training_data.daily_load,
                days_to_race=req.days_to_race,
            )
            logger.debug(
                "Cold start with training data",
                conversation_id=conversation_id,
                athlete_id=athlete_id,
                ctl=athlete_state.ctl,
                atl=athlete_state.atl,
                tsb=athlete_state.tsb,
                confidence=athlete_state.confidence,
                load_trend=athlete_state.load_trend,
                flags=athlete_state.flags,
            )
            reply = welcome_new_user(athlete_state)
        except RuntimeError as e:
            logger.warning(
                "Cold start with no training data available",
                conversation_id=conversation_id,
                error=str(e),
            )
            reply = welcome_new_user(None)

        # Normalize assistant message before saving
        try:
            normalized_assistant_message = normalize_message(
                raw_input=reply,
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
            )
            # Write normalized assistant message to Redis (B26)
            if normalized_assistant_message:
                write_message(normalized_assistant_message)

                # Persist normalized assistant message to Postgres (B29)
                # This happens asynchronously and never blocks the request
                background_tasks.add_task(persist_message, normalized_assistant_message)
        except ValueError as e:
            logger.error(
                "Failed to normalize assistant message for cold start",
                conversation_id=conversation_id,
                user_id=user_id,
                error=str(e),
            )
            # Continue with unnormalized message for now (will be fixed in context_management)
            normalized_assistant_message = None

        # Save conversation history for cold start
        save_context(
            athlete_id=athlete_id,
            model_name=USER_FACING_MODEL,
            user_message=normalized_user_message.content,
            assistant_message=normalized_assistant_message.content if normalized_assistant_message else reply,
            conversation_id=conversation_id,
        )

        return CoachChatResponse(
            intent="cold_start",
            reply=reply,
            conversation_id=conversation_id,
            response_type="greeting",
            show_plan=False,
            plan_items=None,
        )

    # Handle upload requests (activities or plans)
    message_content = normalized_user_message.content
    if is_activity_upload(message_content):
        logger.info(
            "Detected activity upload request",
            conversation_id=conversation_id,
            athlete_id=athlete_id,
        )
        try:
            _activity_ids, created_count = upload_activity_from_chat(user_id=user_id, content=message_content)
            if created_count > 0:
                reply = f"Great! I've logged {created_count} activity/activities to your calendar. Your training data has been updated."
            else:
                reply = "I found those activities, but they appear to be duplicates of existing entries. No new activities were added."
        except ValueError as e:
            logger.warning(f"Activity upload failed: {e}", conversation_id=conversation_id)
            reply = f"I had trouble parsing that activity. Could you try again? Error: {e!s}"
        except Exception as e:
            logger.error(f"Activity upload error: {e}", exc_info=True, conversation_id=conversation_id)
            reply = "I encountered an error processing your activity upload. Please try again."

        # Normalize and save assistant message
        try:
            normalized_assistant_message = normalize_message(
                raw_input=reply,
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
            )
            if normalized_assistant_message:
                write_message(normalized_assistant_message)
                background_tasks.add_task(persist_message, normalized_assistant_message)
        except ValueError as e:
            logger.error(f"Failed to normalize assistant message for upload: {e}", conversation_id=conversation_id)

        return CoachChatResponse(
            intent="upload_activity",
            reply=reply,
            conversation_id=conversation_id,
            response_type="explanation",
            show_plan=False,
            plan_items=None,
        )

    if is_plan_upload(message_content):
        logger.info(
            "Detected plan upload request",
            conversation_id=conversation_id,
            athlete_id=athlete_id,
        )
        try:
            _saved_count, summary = upload_plan_from_chat(
                user_id=user_id,
                athlete_id=athlete_id,
                content=message_content,
            )
            reply = summary
        except ValueError as e:
            logger.warning(f"Plan upload failed: {e}", conversation_id=conversation_id)
            reply = f"I had trouble parsing that training plan. Could you try again? Error: {e!s}"
        except Exception as e:
            logger.error(f"Plan upload error: {e}", exc_info=True, conversation_id=conversation_id)
            reply = "I encountered an error processing your training plan upload. Please try again."

        # Normalize and save assistant message
        try:
            normalized_assistant_message = normalize_message(
                raw_input=reply,
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
            )
            if normalized_assistant_message:
                write_message(normalized_assistant_message)
                background_tasks.add_task(persist_message, normalized_assistant_message)
        except ValueError as e:
            logger.error(f"Failed to normalize assistant message for upload: {e}", conversation_id=conversation_id)

        return CoachChatResponse(
            intent="upload_plan",
            reply=reply,
            conversation_id=conversation_id,
            response_type="explanation",
            show_plan=False,
            plan_items=None,
        )

    # Fast-path: Handle simple activity acknowledgments without invoking agent
    # This prevents internal looping in pydantic_ai for trivial conversational inputs
    if _is_simple_acknowledgment(req.message):
        logger.info(
            "Fast-path: Handling simple acknowledgment without agent",
            conversation_id=conversation_id,
            message=req.message,
            athlete_id=athlete_id,
        )
        # Resolve athlete_id from user_id before fast-path return
        # This ensures athlete_id is always non-null when save_context is called
        with get_session() as db:
            resolved_athlete_id = get_or_create_athlete_id(db=db, user_id=user_id)
            if not resolved_athlete_id:
                raise RuntimeError("athlete_id could not be resolved in coach_chat fast-path")
            athlete_id = resolved_athlete_id

        reply = "Nice work 👍 Want feedback on recovery, pacing, or tomorrow's plan?"

        # Normalize assistant message before saving
        try:
            normalized_assistant_message = normalize_message(
                raw_input=reply,
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
            )
            # Write normalized assistant message to Redis (B26)
            if normalized_assistant_message:
                write_message(normalized_assistant_message)

                # Persist normalized assistant message to Postgres (B29)
                # This happens asynchronously and never blocks the request
                background_tasks.add_task(persist_message, normalized_assistant_message)
        except ValueError as e:
            logger.error(
                "Failed to normalize assistant message for fast-path",
                conversation_id=conversation_id,
                user_id=user_id,
                error=str(e),
            )
            normalized_assistant_message = None

        # Save conversation history for fast-path responses
        save_context(
            athlete_id=athlete_id,
            model_name=USER_FACING_MODEL,
            user_message=normalized_user_message.content,
            assistant_message=normalized_assistant_message.content if normalized_assistant_message else reply,
            conversation_id=conversation_id,
        )
        return CoachChatResponse(
            intent="activity_ack",
            reply=reply,
            response_type="explanation",
            show_plan=False,
            plan_items=None,
        )

    # Build athlete state
    try:
        training_data = get_training_data(user_id=user_id, days=req.days)
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
            days_to_race=req.days_to_race,
        )
    except RuntimeError:
        logger.warning(
            "No training data available for orchestrator",
            conversation_id=conversation_id,
        )
        athlete_state = None

    # Load athlete profile, training preferences, and race profile
    athlete_profile = None
    training_preferences = None
    race_profile = None
    with get_session() as db:
        profile = db.query(AthleteProfile).filter_by(user_id=user_id).first()
        if profile:
            # Calculate age from date_of_birth
            age = None
            if profile.date_of_birth:
                today = datetime.now(timezone.utc).date()
                dob = profile.date_of_birth.date()
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

            # Round weight_lbs and height_in to 1 decimal place
            weight_lbs_rounded = None
            if profile.weight_lbs is not None:
                weight_lbs_rounded = round(float(profile.weight_lbs), 1)
            height_in_rounded = None
            if profile.height_in is not None:
                height_in_rounded = round(float(profile.height_in), 1)

            athlete_profile = AthleteProfileData(
                gender=profile.gender,
                age=age,
                weight_lbs=weight_lbs_rounded,
                height_in=height_in_rounded,
                unit_system=profile.unit_system or "imperial",
            )

            # Load race profile from extracted_race_attributes
            if profile.extracted_race_attributes and isinstance(profile.extracted_race_attributes, dict):
                race_attrs = profile.extracted_race_attributes
                race_profile = RaceProfileData(
                    event_name=race_attrs.get("event_name"),
                    event_type=race_attrs.get("event_type"),
                    event_date=race_attrs.get("event_date"),
                    target_time=race_attrs.get("target_time"),
                    distance=race_attrs.get("distance"),
                    location=race_attrs.get("location"),
                    raw_text=race_attrs.get("raw_text"),
                )

        # Load training preferences from UserSettings
        settings = db.query(UserSettings).filter_by(user_id=user_id).first()
        if settings:
            training_preferences = TrainingPreferencesData(
                training_consistency=settings.consistency,
                years_structured=settings.years_of_training,
                primary_sports=settings.primary_sports or [],
                available_days=settings.available_days or [],
                weekly_training_hours=settings.weekly_hours,
                primary_training_goal=settings.goal,
                training_focus=settings.training_focus,
                injury_flag=settings.injury_history or False,
            )

    # Create dependencies
    deps = CoachDeps(
        athlete_id=athlete_id,
        user_id=user_id,
        athlete_state=athlete_state,
        athlete_profile=athlete_profile,
        training_preferences=training_preferences,
        race_profile=race_profile,
        days=req.days,
        days_to_race=req.days_to_race,
    )

    # Set association properties for tracing
    trace_meta = get_trace_metadata(
        conversation_id=conversation_id,
        user_id=user_id,
    )
    set_association_properties(trace_meta)

    # Get decision from orchestrator (use normalized content, pass conversation_id for slot persistence)
    # Wrap in conversation-level trace (root span)
    with trace(
        name="conversation.turn",
        metadata={
            **trace_meta,
            "intent": "unknown",  # Will be updated after decision
        },
    ):
        decision = await run_conversation(
            user_input=normalized_user_message.content,
            deps=deps,
            conversation_id=conversation_id,
        )

    # CRITICAL: Emit planned events ONLY if action is EXECUTE
    # NO_ACTION must be pure - no side effects, no events, no DB writes
    if decision.action == "EXECUTE" and decision.action_plan:
        logger.info(
            "Emitting planned events for action plan",
            conversation_id=conversation_id,
            step_count=len(decision.action_plan.steps),
        )
        for step in decision.action_plan.steps:
            try:
                await call_tool(
                    "emit_progress_event",
                    {
                        "conversation_id": conversation_id,
                        "step_id": step.id,
                        "label": step.label,
                        "status": "planned",
                    },
                )
                logger.info(
                    "Planned event emitted",
                    conversation_id=conversation_id,
                    step_id=step.id,
                    step_label=step.label,
                )
            except MCPError as e:
                logger.warning(
                    f"Failed to emit planned event for step {step.id}: {e.code}: {e.message}",
                    conversation_id=conversation_id,
                    step_id=step.id,
                )

    # Execute action if needed
    reply = await CoachActionExecutor.execute(decision, deps, conversation_id=conversation_id)

    # Normalize assistant response before returning
    try:
        normalized_assistant_message = normalize_message(
            raw_input=reply,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
        )
        # Write normalized assistant message to Redis (B26)
        write_message(normalized_assistant_message)

        # Persist normalized assistant message to Postgres (B29)
        # This happens asynchronously and never blocks the request
        background_tasks.add_task(persist_message, normalized_assistant_message)

        # Use normalized content for response
        reply_content = normalized_assistant_message.content
    except ValueError as e:
        logger.error(
            "Failed to normalize assistant response",
            conversation_id=conversation_id,
            user_id=user_id,
            error=str(e),
        )
        # Fallback to original reply if normalization fails
        reply_content = reply

    return CoachChatResponse(
        intent=decision.intent,
        reply=reply_content,
        conversation_id=conversation_id,
        response_type=decision.response_type,
        show_plan=decision.show_plan,
        plan_items=decision.plan_items,
    )


@router.get("/conversations/{conversation_id}/progress", response_model=ProgressResponse)
async def get_conversation_progress(
    conversation_id: str,
    request: Request,
    _user_id: str = Depends(validate_conversation_ownership),
) -> ProgressResponse:
    """Get progress events for a conversation.

    Args:
        conversation_id: Conversation ID from path parameter
        request: FastAPI request object (for context validation)
        _user_id: Authenticated user ID (from ownership validation, unused but required for validation)

    Returns:
        ProgressResponse with steps and events
    """
    # Validate that path parameter matches context (optional validation)
    # Ownership is validated by validate_conversation_ownership dependency
    context_conversation_id = get_conversation_id(request)
    if context_conversation_id != conversation_id:
        logger.warning(
            "Conversation ID mismatch between path and context",
            path_conversation_id=conversation_id,
            context_conversation_id=context_conversation_id,
        )
    logger.info(
        "Fetching conversation progress",
        conversation_id=conversation_id,
        context_conversation_id=context_conversation_id,
    )
    with get_session() as db:
        # Fetch all events for this conversation
        events_query = (
            db.query(CoachProgressEvent)
            .filter(CoachProgressEvent.conversation_id == conversation_id)
            .order_by(CoachProgressEvent.timestamp)
        )

        events = events_query.all()

        logger.info(
            "Retrieved progress events",
            conversation_id=conversation_id,
            event_count=len(events),
        )

        # Extract unique steps from events
        steps_dict: dict[str, str] = {}
        for event in events:
            if event.step_id not in steps_dict:
                steps_dict[event.step_id] = event.label

        # Build response
        steps = [ActionStepResponse(id=step_id, label=label) for step_id, label in steps_dict.items()]

        # Type-safe status values
        valid_statuses = {"planned", "in_progress", "completed", "failed", "skipped"}
        event_responses = []
        for event in events:
            # Validate and cast status to Literal type
            if event.status in valid_statuses:
                event_responses.append(
                    ProgressEventResponse(
                        conversation_id=event.conversation_id,
                        step_id=event.step_id,
                        label=event.label,
                        status=cast(
                            Literal["planned", "in_progress", "completed", "failed", "skipped"],
                            event.status,
                        ),
                        timestamp=event.timestamp,
                        message=event.message,
                    )
                )
            else:
                logger.warning(
                    "Invalid status value in progress event",
                    conversation_id=event.conversation_id,
                    step_id=event.step_id,
                    status=event.status,
                )

        return ProgressResponse(steps=steps, events=event_responses)
