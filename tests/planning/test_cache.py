from app.planning.cache import clear_cache, get_cached_session, set_cached_session
from app.planning.schema.session_output import SessionBlock, SessionPlan
from app.planning.schema.session_spec import Intensity, SessionSpec, SessionType, Sport


def test_cache_set_and_get():
    clear_cache()

    spec = SessionSpec(
        sport=Sport.RUN,
        session_type=SessionType.TEMPO,
        intensity=Intensity.TEMPO,
        target_distance_km=10.0,
        goal="tempo development",
        phase="base",
        week_number=1,
        day_of_week=0,
    )

    plan = SessionPlan(
        title="Tempo Run",
        structure=[
            SessionBlock(
                type="warmup",
                distance_km=2.0,
                intensity="easy",
            ),
            SessionBlock(
                type="steady",
                distance_km=6.0,
                intensity="tempo",
            ),
            SessionBlock(
                type="cooldown",
                distance_km=2.0,
                intensity="easy",
            ),
        ],
        notes="Stay controlled",
    )

    set_cached_session(spec, plan)
    cached = get_cached_session(spec)

    assert cached is not None
    assert cached.title == plan.title
    assert len(cached.structure) == len(plan.structure)


def test_cache_miss():
    clear_cache()

    spec = SessionSpec(
        sport=Sport.RUN,
        session_type=SessionType.EASY,
        intensity=Intensity.EASY,
        target_distance_km=5.0,
        goal="easy run",
        phase="base",
        week_number=1,
        day_of_week=1,
    )

    cached = get_cached_session(spec)
    assert cached is None
