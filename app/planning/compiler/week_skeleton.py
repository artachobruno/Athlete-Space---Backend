"""WeekSkeleton - Structure Guarantee.

This is the most important structural object.
It guarantees that a week has exactly the right structure before
any time allocation happens.

WeekSkeleton ensures:
- Exactly one long run
- At most N hard days
- Proper spacing
"""

from dataclasses import dataclass
from typing import Literal

Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DayRole = Literal["easy", "hard", "long", "rest"]


@dataclass(frozen=True)
class WeekSkeleton:
    """Week structure definition - guarantees proper session distribution.

    This is created BEFORE time allocation.
    It defines WHAT sessions exist and WHERE they are.

    Attributes:
        week_index: Zero-based week index in the plan
        days: Dictionary mapping days to day roles
    """

    week_index: int  # 0-based
    days: dict[Day, DayRole]
