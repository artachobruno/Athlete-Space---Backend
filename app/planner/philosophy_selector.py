"""Training philosophy selection (B2.5).

This module implements deterministic, RAG-driven selection of exactly one
training philosophy for the entire plan. Selection is based on:
- User intent (maintain / build / explore / recover)
- Race distance
- Athlete audience
- RAG philosophy metadata (requires, prohibits, priority)
- Optional user override

After selection, the philosophy is locked and B3-B6 only search within
that philosophy's namespace.
"""

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.planner.enums import RaceDistance, TrainingIntent
from app.planner.errors import PlannerError
from app.planner.models import PhilosophySelection, PlanContext
from app.planner.philosophy_loader import PhilosophyDoc, load_philosophies

# Ultra distances
ULTRA_DISTANCES = {"50k", "50m", "100k", "100m"}


def _determine_domain(race_distance: RaceDistance | None) -> str:
    """Determine domain from race distance.

    Args:
        race_distance: Race distance enum or None

    Returns:
        Domain string ("ultra" | "running")
    """
    if race_distance is None:
        return "running"  # Default to running for season plans

    if race_distance.value in ULTRA_DISTANCES:
        return "ultra"

    return "running"


def _normalize_race_type_for_matching(race_value: str) -> list[str]:
    """Normalize race distance enum value to possible RAG race type values.

    Philosophy files use different race type formats than our enums.
    This function returns all possible matches.

    Args:
        race_value: Race distance enum value (e.g., "half_marathon", "5k")

    Returns:
        List of possible race type strings to match against
    """
    # Map enum values to RAG race type formats
    mapping: dict[str, list[str]] = {
        "half_marathon": ["half", "half_marathon"],
        "10_mile": ["10_mile", "10 mile"],
        "5k": ["5k", "5K"],
        "10k": ["10k", "10K"],
        "marathon": ["marathon"],
        "50k": ["50k", "ultra"],
        "50m": ["50m", "ultra"],
        "100k": ["100k", "ultra"],
        "100m": ["100m", "ultra"],
    }

    return mapping.get(race_value, [race_value])


def _validate_philosophy(
    philosophy: PhilosophyDoc,
    ctx: PlanContext,
    athlete_state: AthleteState,
) -> None:
    """Validate that a philosophy is compatible with context and athlete state.

    Args:
        philosophy: Philosophy document to validate
        ctx: Plan context
        athlete_state: Athlete state

    Raises:
        PlannerError: If philosophy is incompatible
    """
    # Check race distance compatibility
    if ctx.race_distance:
        race_value = ctx.race_distance.value
        possible_matches = _normalize_race_type_for_matching(race_value)

        # Check if any of the possible matches are in philosophy.race_types
        if not any(match in philosophy.race_types for match in possible_matches):
            raise PlannerError(
                f"Philosophy '{philosophy.id}' does not support race distance '{race_value}'"
            )

    # Check requires constraints
    for req in philosophy.requires:
        if req not in athlete_state.flags:
            raise PlannerError(
                f"Philosophy '{philosophy.id}' requires '{req}' but athlete does not have it"
            )

    # Check prohibits constraints
    for prohibited in philosophy.prohibits:
        if prohibited in athlete_state.flags:
            raise PlannerError(
                f"Philosophy '{philosophy.id}' prohibits '{prohibited}' but athlete has it"
            )


def _passes_constraints(
    philosophy: PhilosophyDoc,
    athlete_state: AthleteState,
) -> bool:
    """Check if philosophy passes constraint checks.

    Args:
        philosophy: Philosophy document
        athlete_state: Athlete state

    Returns:
        True if philosophy passes all constraints, False otherwise
    """
    # Check requires
    for req in philosophy.requires:
        if req not in athlete_state.flags:
            return False

    # Check prohibits
    return all(prohibited not in athlete_state.flags for prohibited in philosophy.prohibits)


def _determine_audience(athlete_state: AthleteState) -> str:
    """Determine athlete audience from state.

    Maps CTL to audience level matching philosophy file audience values.

    Args:
        athlete_state: Athlete state

    Returns:
        Audience string ("beginner" | "intermediate" | "advanced")
    """
    ctl = athlete_state.ctl

    if ctl < 30:
        return "beginner"
    if ctl < 50:
        return "intermediate"
    if ctl < 70:
        return "intermediate"  # "trained" maps to intermediate for philosophy matching
    return "advanced"


def select_philosophy(
    ctx: PlanContext,
    athlete_state: AthleteState,
    user_preference: str | None = None,
) -> PhilosophySelection:
    """Select exactly one training philosophy for the entire plan.

    Selection algorithm (MANDATED ORDER):
    1. Explicit user override (highest priority) - if provided, validate and return
    2. Domain filtering (ultra vs running)
    3. Race distance filtering
    4. Audience filtering
    5. Intent compatibility (soft filter - not implemented yet, only ranking)
    6. Constraint enforcement (hard gates - requires/prohibits)
    7. Final selection (highest priority, then version)

    Args:
        ctx: Plan context with intent and race_distance
        athlete_state: Athlete state with flags and metrics
        user_preference: Optional explicit philosophy ID override

    Returns:
        PhilosophySelection with selected philosophy

    Raises:
        PlannerError: If no valid philosophy is found or validation fails
    """
    logger.info(
        "Selecting training philosophy",
        intent=ctx.intent.value,
        race_distance=ctx.race_distance.value if ctx.race_distance else None,
        user_preference=user_preference,
    )

    # Load all philosophies
    philosophies = load_philosophies()

    # STEP 1: Explicit user override (highest priority)
    if user_preference:
        match = next((p for p in philosophies if p.id == user_preference), None)
        if not match:
            raise PlannerError(f"Unknown philosophy '{user_preference}'")

        try:
            _validate_philosophy(match, ctx, athlete_state)
        except PlannerError as e:
            raise PlannerError(f"User-selected philosophy '{user_preference}' is invalid: {e}") from e

        logger.info(
            "Selected philosophy via user override",
            philosophy_id=match.id,
            domain=match.domain,
            audience=match.audience,
        )

        return PhilosophySelection(
            philosophy_id=match.id,
            domain=match.domain,
            audience=match.audience,
        )

    # STEP 2: Domain filtering
    domain = _determine_domain(ctx.race_distance)
    candidates = [p for p in philosophies if p.domain == domain]

    logger.debug(f"After domain filtering ({domain}): {len(candidates)} candidates")

    # STEP 3: Race distance filtering
    if ctx.race_distance:
        race_value = ctx.race_distance.value
        possible_matches = _normalize_race_type_for_matching(race_value)

        candidates = [
            p
            for p in candidates
            if any(match in p.race_types for match in possible_matches)
        ]

        logger.debug(f"After race distance filtering ({race_value}): {len(candidates)} candidates")

    # STEP 4: Audience filtering
    athlete_audience = _determine_audience(athlete_state)
    # Handle "all" audience - it matches any audience
    valid_audiences = {athlete_audience, "all"}
    candidates = [
        p
        for p in candidates
        if p.audience in valid_audiences
    ]

    logger.debug(f"After audience filtering ({athlete_audience}): {len(candidates)} candidates")

    # STEP 5: Intent compatibility (soft filter - not implemented, only affects ranking)
    # This is a placeholder for future intent-based ranking
    # For now, we don't filter by intent, only use it for ranking

    # STEP 6: Constraint enforcement (hard gates)
    candidates = [p for p in candidates if _passes_constraints(p, athlete_state)]

    logger.debug(f"After constraint filtering: {len(candidates)} candidates")

    if not candidates:
        raise PlannerError(
            f"No valid training philosophy found for "
            f"domain={domain}, race={ctx.race_distance.value if ctx.race_distance else 'none'}, "
            f"audience={athlete_audience}"
        )

    # STEP 7: Final selection (highest priority, then version)
    best = sorted(
        candidates,
        key=lambda p: (p.priority, p.version),
        reverse=True,
    )[0]

    logger.info(
        "Selected philosophy",
        philosophy_id=best.id,
        domain=best.domain,
        audience=best.audience,
        priority=best.priority,
    )

    return PhilosophySelection(
        philosophy_id=best.id,
        domain=best.domain,
        audience=best.audience,
    )
