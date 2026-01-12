#!/usr/bin/env python3
"""Generate missing session templates based on patterns and fallbacks."""


from collections import defaultdict
from pathlib import Path

import yaml

# Template patterns by session type
TEMPLATE_PATTERNS = {
    "easy": {
        "build": {
            "templates": [
                {
                    "id": "easy_continuous_v1",
                    "description_key": "{philosophy}_easy_continuous_v1",
                    "kind": "easy_continuous",
                    "params": {
                        "warmup_mi_range": [0.0, 0.0],
                        "cooldown_mi_range": [0.0, 0.0],
                        "easy_mi_range": [3.0, 10.0],
                    },
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["z2", "aerobic"],
                }
            ],
        },
        "taper": {
            "templates": [
                {
                    "id": "easy_continuous_taper_v1",
                    "description_key": "{philosophy}_easy_continuous_taper_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [3.0, 7.0]},
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["freshness", "low_stress"],
                }
            ],
        },
    },
    "easy_plus_strides": {
        "build": {
            "templates": [
                {
                    "id": "easy_with_strides_v1",
                    "description_key": "{philosophy}_easy_with_strides_v1",
                    "kind": "easy_with_strides",
                    "params": {
                        "easy_mi_range": [3.0, 8.0],
                        "strides_count_range": [4, 10],
                        "stride_seconds_range": [15, 25],
                        "stride_recovery_seconds_range": [45, 90],
                    },
                    "constraints": {"strides_max": 10, "intensity_minutes_max": 5},
                    "tags": ["strides", "economy"],
                }
            ],
        },
        "taper": {
            "templates": [
                {
                    "id": "easy_with_strides_taper_v1",
                    "description_key": "{philosophy}_easy_with_strides_taper_v1",
                    "kind": "easy_with_strides",
                    "params": {
                        "easy_mi_range": [3.0, 6.0],
                        "strides_count_range": [4, 6],
                        "stride_seconds_range": [15, 20],
                        "stride_recovery_seconds_range": [60, 90],
                    },
                    "constraints": {"strides_max": 6, "intensity_minutes_max": 3},
                    "tags": ["sharpen", "neuromuscular"],
                }
            ],
        },
    },
    "easy_or_shakeout": {
        "taper": {
            "templates": [
                {
                    "id": "easy_or_shakeout_taper_v1",
                    "description_key": "{philosophy}_easy_or_shakeout_taper_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [2.0, 5.0]},
                    "constraints": {"intensity_minutes_max": 0, "total_duration_max": 45},
                    "tags": ["freshness", "recovery", "optional_shakeout"],
                }
            ],
        },
    },
    "pre_race_shakeout": {
        "taper": {
            "templates": [
                {
                    "id": "pre_race_shakeout_v1",
                    "description_key": "{philosophy}_pre_race_shakeout_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [1.0, 3.0]},
                    "constraints": {"intensity_minutes_max": 0, "total_duration_max": 25},
                    "tags": ["pre_race", "shakeout", "very_light", "activation"],
                }
            ],
        },
    },
    "race_day": {
        "taper": {
            "templates": [
                {
                    "id": "race_{race}_v1",
                    "description_key": "{philosophy}_race_{race}_v1",
                    "kind": "race",
                    "params": {
                        "race_distance_km": {"5k": 5.0, "marathon": 42.2}.get("{race}", 5.0),
                        "warmup_mi_range": [1.0, 2.0],
                        "cooldown_mi_range": [0.5, 1.5],
                        "race_intensity": "R",
                    },
                    "constraints": {"total_duration_max": {"5k": 60, "marathon": 300}.get("{race}", 60)},
                    "tags": ["race", "{race}", "target_effort"],
                }
            ],
        },
    },
    "vo2_light": {
        "taper": {
            "templates": [
                {
                    "id": "vo2_light_sharpen_1to2min_v1",
                    "description_key": "{philosophy}_vo2_light_sharpen_1to2min_v1",
                    "kind": "vo2_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 2.5],
                        "cooldown_mi_range": [1.0, 2.0],
                        "reps_range": [3, 5],
                        "rep_minutes_range": [1, 2],
                        "recovery_minutes_range": [1, 2],
                        "intensity": "I",
                    },
                    "constraints": {"total_I_minutes_range": [4, 10], "hard_minutes_max": 12},
                    "tags": ["taper", "light", "sharp"],
                }
            ],
        },
    },
    "threshold_light": {
        "taper": {
            "templates": [
                {
                    "id": "threshold_light_taper_v1",
                    "description_key": "{philosophy}_threshold_light_taper_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 2.5],
                        "cooldown_mi_range": [1.0, 2.0],
                        "reps_range": [2, 4],
                        "rep_minutes_range": [4, 8],
                        "float_minutes_range": [1, 2],
                        "intensity": "T",
                    },
                    "constraints": {"total_T_minutes_range": [10, 20], "hard_minutes_max": 22},
                    "tags": ["taper", "keep_touch"],
                }
            ],
        },
    },
    "long": {
        "build": {
            "templates": [
                {
                    "id": "long_easy_v1",
                    "description_key": "{philosophy}_long_easy_v1",
                    "kind": "long_easy",
                    "params": {
                        "long_mi_range": {"5k": [8.0, 14.0], "marathon": [12.0, 22.0]}.get("{race}", [8.0, 14.0]),
                        "finish_pickup_mi_range": [0.0, 2.0],
                        "finish_intensity": "steady",
                    },
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["aerobic", "durability"],
                }
            ],
        },
        "taper": {
            "templates": [
                {
                    "id": "long_easy_taper_v1",
                    "description_key": "{philosophy}_long_easy_taper_v1",
                    "kind": "long_easy",
                    "params": {
                        "long_mi_range": {"5k": [6.0, 10.0], "marathon": [8.0, 14.0]}.get("{race}", [6.0, 10.0]),
                        "finish_pickup_mi_range": [0.0, 1.0],
                        "finish_intensity": "steady",
                    },
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["taper", "freshness"],
                }
            ],
        },
    },
    "threshold": {
        "build": {
            "templates": [
                {
                    "id": "cruise_intervals_v1",
                    "description_key": "{philosophy}_threshold_cruise_intervals_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [3, 6],
                        "rep_minutes_range": [5, 10],
                        "float_minutes_range": [1, 3],
                        "intensity": "T",
                    },
                    "constraints": {"total_T_minutes_range": [20, 40], "hard_minutes_max": 45},
                    "tags": ["threshold", "vdot"],
                }
            ],
        },
    },
    "vo2": {
        "build": {
            "templates": [
                {
                    "id": "vo2_intervals_v1",
                    "description_key": "{philosophy}_vo2_intervals_v1",
                    "kind": "vo2_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [2, 5],
                        "recovery_minutes_range": [2, 4],
                        "intensity": "I",
                    },
                    "constraints": {"total_I_minutes_range": [12, 24], "hard_minutes_max": 30},
                    "tags": ["vo2", "economy"],
                }
            ],
        },
    },
    # Additional session types with fallback patterns
    "aerobic": {
        "build": {
            "templates": [
                {
                    "id": "aerobic_continuous_v1",
                    "description_key": "{philosophy}_aerobic_continuous_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [4.0, 10.0]},
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["aerobic", "z2"],
                }
            ],
        },
    },
    "aerobic_plus_strides": {
        "taper": {
            "templates": [
                {
                    "id": "aerobic_with_strides_taper_v1",
                    "description_key": "{philosophy}_aerobic_with_strides_taper_v1",
                    "kind": "easy_with_strides",
                    "params": {
                        "easy_mi_range": [3.0, 6.0],
                        "strides_count_range": [4, 8],
                        "stride_seconds_range": [15, 20],
                        "stride_recovery_seconds_range": [60, 90],
                    },
                    "constraints": {"strides_max": 8, "intensity_minutes_max": 4},
                    "tags": ["aerobic", "strides", "taper"],
                }
            ],
        },
    },
    "easy_or_light_fartlek": {
        "taper": {
            "templates": [
                {
                    "id": "easy_or_light_fartlek_taper_v1",
                    "description_key": "{philosophy}_easy_or_light_fartlek_taper_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [3.0, 6.0]},
                    "constraints": {"intensity_minutes_max": 2},
                    "tags": ["taper", "optional_fartlek"],
                }
            ],
        },
    },
    "easy_or_marathon_touch": {
        "taper": {
            "templates": [
                {
                    "id": "easy_or_marathon_touch_taper_v1",
                    "description_key": "{philosophy}_easy_or_marathon_touch_taper_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [3.0, 6.0]},
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["taper", "marathon_prep"],
                }
            ],
        },
    },
    "easy_or_steady_short": {
        "taper": {
            "templates": [
                {
                    "id": "easy_or_steady_short_taper_v1",
                    "description_key": "{philosophy}_easy_or_steady_short_taper_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [3.0, 6.0]},
                    "constraints": {"intensity_minutes_max": 3},
                    "tags": ["taper", "optional_steady"],
                }
            ],
        },
    },
    "medium_easy": {
        "taper": {
            "templates": [
                {
                    "id": "medium_easy_taper_v1",
                    "description_key": "{philosophy}_medium_easy_taper_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [4.0, 8.0]},
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["taper", "moderate_volume"],
                }
            ],
        },
    },
    "recovery": {
        "build": {
            "templates": [
                {
                    "id": "recovery_easy_v1",
                    "description_key": "{philosophy}_recovery_easy_v1",
                    "kind": "easy_continuous",
                    "params": {"easy_mi_range": [2.0, 5.0]},
                    "constraints": {"intensity_minutes_max": 0},
                    "tags": ["recovery", "very_easy"],
                }
            ],
        },
    },
    "moderate_long": {
        "build": {
            "templates": [
                {
                    "id": "moderate_long_v1",
                    "description_key": "{philosophy}_moderate_long_v1",
                    "kind": "long_easy",
                    "params": {
                        "long_mi_range": {"5k": [6.0, 10.0], "marathon": [10.0, 18.0]}.get("{race}", [6.0, 10.0]),
                        "finish_pickup_mi_range": [0.0, 1.5],
                        "finish_intensity": "steady",
                    },
                    "constraints": {"intensity_minutes_max": 2},
                    "tags": ["moderate", "long"],
                }
            ],
        },
    },
    "medium_long": {
        "build": {
            "templates": [
                {
                    "id": "medium_long_v1",
                    "description_key": "{philosophy}_medium_long_v1",
                    "kind": "long_easy",
                    "params": {
                        "long_mi_range": {"5k": [6.0, 10.0], "marathon": [10.0, 18.0]}.get("{race}", [6.0, 10.0]),
                        "finish_pickup_mi_range": [0.0, 1.5],
                        "finish_intensity": "steady",
                    },
                    "constraints": {"intensity_minutes_max": 2},
                    "tags": ["medium", "long"],
                }
            ],
        },
    },
    "long_progressive": {
        "build": {
            "templates": [
                {
                    "id": "long_progressive_v1",
                    "description_key": "{philosophy}_long_progressive_v1",
                    "kind": "long_easy",
                    "params": {
                        "long_mi_range": {"5k": [8.0, 14.0], "marathon": [14.0, 22.0]}.get("{race}", [8.0, 14.0]),
                        "finish_pickup_mi_range": [2.0, 4.0],
                        "finish_intensity": "steady",
                    },
                    "constraints": {"intensity_minutes_max": 5},
                    "tags": ["progressive", "long"],
                }
            ],
        },
    },
    "marathon_pace": {
        "build": {
            "templates": [
                {
                    "id": "marathon_pace_blocks_v1",
                    "description_key": "{philosophy}_marathon_pace_blocks_v1",
                    "kind": "steady_T_block",
                    "params": {
                        "warmup_mi_range": [2.0, 3.0],
                        "cooldown_mi_range": [1.0, 2.0],
                        "continuous_T_minutes_range": [20, 40],
                        "intensity": "M",
                    },
                    "constraints": {"total_T_minutes_range": [20, 40], "hard_minutes_max": 45},
                    "tags": ["marathon_pace", "specificity"],
                }
            ],
        },
    },
    "marathon_pace_light": {
        "taper": {
            "templates": [
                {
                    "id": "marathon_pace_light_taper_v1",
                    "description_key": "{philosophy}_marathon_pace_light_taper_v1",
                    "kind": "steady_T_block",
                    "params": {
                        "warmup_mi_range": [1.5, 2.5],
                        "cooldown_mi_range": [1.0, 2.0],
                        "continuous_T_minutes_range": [10, 20],
                        "intensity": "M",
                    },
                    "constraints": {"total_T_minutes_range": [10, 20], "hard_minutes_max": 25},
                    "tags": ["taper", "marathon_pace", "light"],
                }
            ],
        },
    },
    "threshold_or_steady": {
        "build": {
            "templates": [
                {
                    "id": "threshold_or_steady_v1",
                    "description_key": "{philosophy}_threshold_or_steady_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [3, 5],
                        "rep_minutes_range": [5, 10],
                        "float_minutes_range": [1, 3],
                        "intensity": "T",
                    },
                    "constraints": {"total_T_minutes_range": [18, 35], "hard_minutes_max": 40},
                    "tags": ["threshold", "steady"],
                }
            ],
        },
    },
    "threshold_or_marathon": {
        "build": {
            "templates": [
                {
                    "id": "threshold_or_marathon_v1",
                    "description_key": "{philosophy}_threshold_or_marathon_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [2.0, 3.0],
                        "cooldown_mi_range": [1.0, 2.0],
                        "reps_range": [3, 5],
                        "rep_minutes_range": [5, 12],
                        "float_minutes_range": [1, 2],
                        "intensity": "T",
                    },
                    "constraints": {"total_T_minutes_range": [20, 40], "hard_minutes_max": 45},
                    "tags": ["threshold", "marathon_pace"],
                }
            ],
        },
    },
    "threshold_double": {
        "build": {
            "templates": [
                {
                    "id": "threshold_double_v1",
                    "description_key": "{philosophy}_threshold_double_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [2.0, 3.0],
                        "cooldown_mi_range": [1.0, 2.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [5, 10],
                        "float_minutes_range": [1, 2],
                        "intensity": "T",
                    },
                    "constraints": {"total_T_minutes_range": [30, 50], "hard_minutes_max": 55},
                    "tags": ["threshold", "double"],
                }
            ],
        },
    },
    "threshold_double_or_marathon": {
        "build": {
            "templates": [
                {
                    "id": "threshold_double_or_marathon_v1",
                    "description_key": "{philosophy}_threshold_double_or_marathon_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [2.0, 3.0],
                        "cooldown_mi_range": [1.0, 2.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [5, 12],
                        "float_minutes_range": [1, 2],
                        "intensity": "T",
                    },
                    "constraints": {"total_T_minutes_range": [30, 50], "hard_minutes_max": 55},
                    "tags": ["threshold", "double", "marathon"],
                }
            ],
        },
    },
    "threshold_or_speed_endurance": {
        "build": {
            "templates": [
                {
                    "id": "threshold_or_speed_endurance_v1",
                    "description_key": "{philosophy}_threshold_or_speed_endurance_v1",
                    "kind": "cruise_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [3, 6],
                        "rep_minutes_range": [3, 8],
                        "float_minutes_range": [1, 2],
                        "intensity": "I",
                    },
                    "constraints": {"total_T_minutes_range": [15, 30], "hard_minutes_max": 35},
                    "tags": ["threshold", "speed"],
                }
            ],
        },
    },
    "vo2_or_speed": {
        "build": {
            "templates": [
                {
                    "id": "vo2_or_speed_v1",
                    "description_key": "{philosophy}_vo2_or_speed_v1",
                    "kind": "vo2_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [1, 3],
                        "recovery_minutes_range": [2, 4],
                        "intensity": "I",
                    },
                    "constraints": {"total_I_minutes_range": [10, 20], "hard_minutes_max": 25},
                    "tags": ["vo2", "speed"],
                }
            ],
        },
    },
    "vo2_or_hill_reps": {
        "build": {
            "templates": [
                {
                    "id": "vo2_or_hill_reps_v1",
                    "description_key": "{philosophy}_vo2_or_hill_reps_v1",
                    "kind": "vo2_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [2, 5],
                        "recovery_minutes_range": [2, 4],
                        "intensity": "I",
                    },
                    "constraints": {"total_I_minutes_range": [12, 24], "hard_minutes_max": 30},
                    "tags": ["vo2", "hills"],
                }
            ],
        },
    },
    "speed_or_vo2": {
        "build": {
            "templates": [
                {
                    "id": "speed_or_vo2_v1",
                    "description_key": "{philosophy}_speed_or_vo2_v1",
                    "kind": "vo2_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [1, 3],
                        "recovery_minutes_range": [2, 4],
                        "intensity": "I",
                    },
                    "constraints": {"total_I_minutes_range": [10, 20], "hard_minutes_max": 25},
                    "tags": ["speed", "vo2"],
                }
            ],
        },
    },
    "hill_strength_or_fartlek": {
        "build": {
            "templates": [
                {
                    "id": "hill_strength_or_fartlek_v1",
                    "description_key": "{philosophy}_hill_strength_or_fartlek_v1",
                    "kind": "vo2_intervals",
                    "params": {
                        "warmup_mi_range": [1.5, 3.0],
                        "cooldown_mi_range": [1.0, 3.0],
                        "reps_range": [4, 8],
                        "rep_minutes_range": [2, 5],
                        "recovery_minutes_range": [2, 4],
                        "intensity": "I",
                    },
                    "constraints": {"total_I_minutes_range": [12, 24], "hard_minutes_max": 30},
                    "tags": ["hills", "fartlek"],
                }
            ],
        },
    },
    "marathon_specific_or_progression": {
        "build": {
            "templates": [
                {
                    "id": "marathon_specific_or_progression_v1",
                    "description_key": "{philosophy}_marathon_specific_or_progression_v1",
                    "kind": "steady_T_block",
                    "params": {
                        "warmup_mi_range": [2.0, 3.0],
                        "cooldown_mi_range": [1.0, 2.0],
                        "continuous_T_minutes_range": [20, 40],
                        "intensity": "M",
                    },
                    "constraints": {"total_T_minutes_range": [20, 40], "hard_minutes_max": 45},
                    "tags": ["marathon", "progression"],
                }
            ],
        },
    },
}


def generate_template_content(philosophy: str, race: str, audience: str, phase: str, session_type: str) -> str:
    """Generate template file content."""
    pattern = TEMPLATE_PATTERNS.get(session_type, {}).get(phase)

    if not pattern:
        # Use fallback - try to find similar session type
        if session_type.endswith("_light"):
            base_type = session_type.replace("_light", "")
            pattern = TEMPLATE_PATTERNS.get(base_type, {}).get(phase)
        elif "or" in session_type:
            # Try first part
            base_type = session_type.split("_or_", maxsplit=1)[0]
            pattern = TEMPLATE_PATTERNS.get(base_type, {}).get(phase)

    if not pattern:
        # Default fallback - use easy pattern
        pattern = TEMPLATE_PATTERNS.get("easy", {}).get(phase) or TEMPLATE_PATTERNS.get("easy", {}).get("build")

    if not pattern:
        raise ValueError(f"No pattern found for {session_type} in {phase}")

    # Format templates
    formatted_templates = []
    for template in pattern["templates"]:
        formatted_template = template.copy()
        # Replace placeholders
        for key, value in formatted_template.items():
            if isinstance(value, str):
                formatted_template[key] = value.replace("{philosophy}", philosophy).replace("{race}", race)
            elif isinstance(value, dict):
                formatted_template[key] = {
                    k: (v.replace("{philosophy}", philosophy).replace("{race}", race) if isinstance(v, str) else v)
                    for k, v in value.items()
                }
        formatted_templates.append(formatted_template)

    # Generate YAML frontmatter
    frontmatter = f"""---
doc_type: session_template_set
domain: running
philosophy_id: {philosophy}

race_types: [{race}]
audience: {audience}
phase: {phase}
session_type: {session_type}

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
"""
    # Use yaml.dump with proper settings
    template_dict = {"templates": formatted_templates}
    template_spec_yaml = yaml.dump(template_dict, default_flow_style=False, sort_keys=False, indent=2, allow_unicode=True)

    return frontmatter + template_spec_yaml + "```\n"


def main():
    """Generate all missing templates."""
    # Parse missing templates from the analysis
    missing = [
        # 5k_speed
        ("5k_speed", "5k", "intermediate", "build", "easy"),
        ("5k_speed", "5k", "intermediate", "build", "easy_plus_strides"),
        ("5k_speed", "5k", "intermediate", "build", "long"),
        ("5k_speed", "5k", "intermediate", "build", "threshold_or_speed_endurance"),
        ("5k_speed", "5k", "intermediate", "build", "vo2_or_speed"),
        ("5k_speed", "5k", "intermediate", "taper", "easy"),
        ("5k_speed", "5k", "intermediate", "taper", "easy_or_shakeout"),
        ("5k_speed", "5k", "intermediate", "taper", "easy_plus_strides"),
        ("5k_speed", "5k", "intermediate", "taper", "pre_race_shakeout"),
        ("5k_speed", "5k", "intermediate", "taper", "race_day"),
        ("5k_speed", "5k", "intermediate", "taper", "vo2_light"),
        # 8020_polarized
        ("8020_polarized", "5k", "intermediate", "build", "easy"),
        ("8020_polarized", "5k", "intermediate", "build", "easy_plus_strides"),
        ("8020_polarized", "5k", "intermediate", "build", "long"),
        ("8020_polarized", "5k", "intermediate", "build", "vo2_or_hill_reps"),
        ("8020_polarized", "5k", "intermediate", "taper", "easy"),
        ("8020_polarized", "5k", "intermediate", "taper", "easy_or_shakeout"),
        ("8020_polarized", "5k", "intermediate", "taper", "easy_plus_strides"),
        ("8020_polarized", "5k", "intermediate", "taper", "pre_race_shakeout"),
        ("8020_polarized", "5k", "intermediate", "taper", "race_day"),
        ("8020_polarized", "5k", "intermediate", "taper", "vo2_light"),
        # hansons
        ("hansons", "5k", "intermediate", "build", "easy"),
        ("hansons", "5k", "intermediate", "build", "moderate_long"),
        ("hansons", "5k", "intermediate", "build", "speed_or_vo2"),
        ("hansons", "5k", "intermediate", "build", "threshold"),
        ("hansons", "5k", "intermediate", "taper", "easy"),
        ("hansons", "5k", "intermediate", "taper", "easy_or_shakeout"),
        ("hansons", "5k", "intermediate", "taper", "easy_plus_strides"),
        ("hansons", "5k", "intermediate", "taper", "pre_race_shakeout"),
        ("hansons", "5k", "intermediate", "taper", "race_day"),
        ("hansons", "5k", "intermediate", "taper", "vo2_light"),
        # lydiard
        ("lydiard", "5k", "intermediate", "build", "aerobic"),
        ("lydiard", "5k", "intermediate", "build", "easy"),
        ("lydiard", "5k", "intermediate", "build", "hill_strength_or_fartlek"),
        ("lydiard", "5k", "intermediate", "build", "long"),
        ("lydiard", "5k", "intermediate", "taper", "aerobic_plus_strides"),
        ("lydiard", "5k", "intermediate", "taper", "easy"),
        ("lydiard", "5k", "intermediate", "taper", "easy_or_light_fartlek"),
        ("lydiard", "5k", "intermediate", "taper", "easy_plus_strides"),
        ("lydiard", "5k", "intermediate", "taper", "race_day"),
        # marathon_specificity
        ("marathon_specificity", "marathon", "intermediate", "build", "easy"),
        ("marathon_specificity", "marathon", "intermediate", "build", "long"),
        ("marathon_specificity", "marathon", "intermediate", "build", "marathon_pace"),
        ("marathon_specificity", "marathon", "intermediate", "build", "medium_long"),
        ("marathon_specificity", "marathon", "intermediate", "build", "threshold_or_steady"),
        ("marathon_specificity", "marathon", "intermediate", "taper", "easy"),
        ("marathon_specificity", "marathon", "intermediate", "taper", "easy_or_steady_short"),
        ("marathon_specificity", "marathon", "intermediate", "taper", "marathon_pace_light"),
        ("marathon_specificity", "marathon", "intermediate", "taper", "pre_race_shakeout"),
        ("marathon_specificity", "marathon", "intermediate", "taper", "race_day"),
        # norwegian
        ("norwegian", "5k", "intermediate", "build", "easy"),
        ("norwegian", "5k", "intermediate", "build", "long"),
        ("norwegian", "5k", "intermediate", "build", "threshold_double"),
        ("norwegian", "5k", "intermediate", "taper", "easy"),
        ("norwegian", "5k", "intermediate", "taper", "easy_or_shakeout"),
        ("norwegian", "5k", "intermediate", "taper", "easy_plus_strides"),
        ("norwegian", "5k", "intermediate", "taper", "pre_race_shakeout"),
        ("norwegian", "5k", "intermediate", "taper", "race_day"),
        ("norwegian", "5k", "intermediate", "taper", "threshold_light"),
        ("norwegian", "marathon", "intermediate", "build", "easy"),
        ("norwegian", "marathon", "intermediate", "build", "long_progressive"),
        ("norwegian", "marathon", "intermediate", "build", "threshold_double"),
        ("norwegian", "marathon", "intermediate", "build", "threshold_double_or_marathon"),
        ("norwegian", "marathon", "intermediate", "taper", "easy"),
        ("norwegian", "marathon", "intermediate", "taper", "easy_or_marathon_touch"),
        ("norwegian", "marathon", "intermediate", "taper", "pre_race_shakeout"),
        ("norwegian", "marathon", "intermediate", "taper", "race_day"),
        ("norwegian", "marathon", "intermediate", "taper", "threshold_light"),
        # pfitzinger
        ("pfitzinger", "marathon", "intermediate", "build", "easy"),
        ("pfitzinger", "marathon", "intermediate", "build", "long"),
        ("pfitzinger", "marathon", "intermediate", "build", "marathon_specific_or_progression"),
        ("pfitzinger", "marathon", "intermediate", "build", "medium_long"),
        ("pfitzinger", "marathon", "intermediate", "build", "recovery"),
        ("pfitzinger", "marathon", "intermediate", "build", "threshold"),
        ("pfitzinger", "marathon", "intermediate", "taper", "easy"),
        ("pfitzinger", "marathon", "intermediate", "taper", "medium_easy"),
        ("pfitzinger", "marathon", "intermediate", "taper", "pre_race_shakeout"),
        ("pfitzinger", "marathon", "intermediate", "taper", "race_day"),
        ("pfitzinger", "marathon", "intermediate", "taper", "threshold_light"),
        # threshold_heavy
        ("threshold_heavy", "marathon", "intermediate", "build", "easy"),
        ("threshold_heavy", "marathon", "intermediate", "build", "long"),
        ("threshold_heavy", "marathon", "intermediate", "build", "threshold"),
        ("threshold_heavy", "marathon", "intermediate", "build", "threshold_or_marathon"),
        ("threshold_heavy", "marathon", "intermediate", "taper", "easy"),
        ("threshold_heavy", "marathon", "intermediate", "taper", "easy_or_marathon_touch"),
        ("threshold_heavy", "marathon", "intermediate", "taper", "pre_race_shakeout"),
        ("threshold_heavy", "marathon", "intermediate", "taper", "race_day"),
        ("threshold_heavy", "marathon", "intermediate", "taper", "threshold_light"),
    ]

    templates_dir = Path("data/rag/planning/templates/running")
    created = 0

    for philosophy, race, audience, phase, session_type in missing:
        template_dir = templates_dir / philosophy
        template_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{philosophy}__{race}__{audience}__{phase}__{session_type}__v1.md"
        filepath = template_dir / filename

        if filepath.exists():
            print(f"SKIP: {filename} (already exists)")
            continue

        try:
            content = generate_template_content(philosophy, race, audience, phase, session_type)
            filepath.write_text(content)
            print(f"CREATED: {filename}")
            created += 1
        except Exception as e:
            print(f"ERROR: {filename} - {e}")

    print(f"\nCreated {created} templates")


if __name__ == "__main__":
    main()
