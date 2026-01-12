"""Plan progress stage definitions.

This module defines the stages of plan generation for progress tracking.
"""

from enum import Enum


class PlanProgressStage(str, Enum):
    """Plan generation progress stages.

    These stages represent the major phases of plan generation:
    - STRUCTURE: Planning overall structure (macro plan, philosophy, week structures)
    - WEEKS: Planning weeks (volume allocation, template selection)
    - WEEK_DETAIL: Planning individual week details (per week)
    - INSTRUCTIONS: Generating session instructions/text
    - DONE: Plan generation complete
    """

    STRUCTURE = "planning_structure"
    WEEKS = "planning_weeks"
    WEEK_DETAIL = "planning_week"
    INSTRUCTIONS = "generating_instructions"
    DONE = "done"
