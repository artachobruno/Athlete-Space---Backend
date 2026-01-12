"""Philosophy configuration loader.

This module loads deterministic philosophy configs from YAML files.
These configs are the source of truth for planning constraints.

RAG RULE:
- Philosophy configs are loaded directly from disk (deterministic)
- RAG is called AFTER structure is fixed for explanations only
- RAG never overrides philosophy config values
- Philosophy configs are NOT embedded, chunked, or retrieved
- RAG is used for explanatory context only, never for structure decisions
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

# Path to philosophy config directory
PHILOSOPHY_CONFIG_DIR = Path(__file__).parent / "philosophy"


@dataclass(frozen=True)
class StructurePolicy:
    """Structure policy for a philosophy."""

    allow_doubles: bool
    max_quality_days: int
    long_run_required: bool
    back_to_back_longs: bool


@dataclass(frozen=True)
class IntensityPolicy:
    """Intensity policy for a philosophy."""

    threshold_bias: Literal["low", "moderate", "high"]
    vo2max_bias: Literal["low", "moderate", "high"]


@dataclass(frozen=True)
class VolumePolicy:
    """Volume policy for a philosophy."""

    min_weekly_km: float
    max_weekly_km: float


@dataclass(frozen=True)
class PhilosophyConstraints:
    """Constraints for a philosophy."""

    min_experience: Literal["beginner", "intermediate", "advanced"]


@dataclass(frozen=True)
class PhilosophyConfig:
    """Deterministic philosophy configuration.

    This is the source of truth for planning constraints.
    Loaded directly from disk, never from RAG.
    """

    id: str
    version: str
    structure_policy: StructurePolicy
    intensity_policy: IntensityPolicy
    volume_policy: VolumePolicy
    constraints: PhilosophyConstraints
    rag_context_id: str


def load_philosophy_config(philosophy_id: str) -> PhilosophyConfig:
    """Load philosophy config from YAML file.

    Args:
        philosophy_id: Philosophy identifier (e.g., "norwegian")

    Returns:
        PhilosophyConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config file is invalid
    """
    config_path = PHILOSOPHY_CONFIG_DIR / f"{philosophy_id}.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Philosophy config not found: {config_path}")

    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise TypeError(f"Invalid config format in {config_path}: expected dict")

    # Validate required fields
    required_fields = ["id", "version", "structure_policy", "intensity_policy", "volume_policy", "constraints", "rag_context_id"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field '{field}' in {config_path}")

    # Parse structure_policy
    structure_data = data["structure_policy"]
    if not isinstance(structure_data, dict):
        raise TypeError(f"Invalid structure_policy format in {config_path}")
    structure_policy = StructurePolicy(
        allow_doubles=structure_data.get("allow_doubles", False),
        max_quality_days=structure_data.get("max_quality_days", 2),
        long_run_required=structure_data.get("long_run_required", True),
        back_to_back_longs=structure_data.get("back_to_back_longs", False),
    )

    # Parse intensity_policy
    intensity_data = data["intensity_policy"]
    if not isinstance(intensity_data, dict):
        raise TypeError(f"Invalid intensity_policy format in {config_path}")
    threshold_bias = intensity_data.get("threshold_bias", "moderate")
    vo2max_bias = intensity_data.get("vo2max_bias", "moderate")
    if threshold_bias not in {"low", "moderate", "high"}:
        raise ValueError(f"Invalid threshold_bias '{threshold_bias}' in {config_path}")
    if vo2max_bias not in {"low", "moderate", "high"}:
        raise ValueError(f"Invalid vo2max_bias '{vo2max_bias}' in {config_path}")
    intensity_policy = IntensityPolicy(
        threshold_bias=threshold_bias,
        vo2max_bias=vo2max_bias,
    )

    # Parse volume_policy
    volume_data = data["volume_policy"]
    if not isinstance(volume_data, dict):
        raise TypeError(f"Invalid volume_policy format in {config_path}")
    volume_policy = VolumePolicy(
        min_weekly_km=float(volume_data.get("min_weekly_km", 50.0)),
        max_weekly_km=float(volume_data.get("max_weekly_km", 150.0)),
    )

    # Parse constraints
    constraints_data = data["constraints"]
    if not isinstance(constraints_data, dict):
        raise TypeError(f"Invalid constraints format in {config_path}")
    min_experience = constraints_data.get("min_experience", "intermediate")
    if min_experience not in {"beginner", "intermediate", "advanced"}:
        raise ValueError(f"Invalid min_experience '{min_experience}' in {config_path}")
    constraints = PhilosophyConstraints(min_experience=min_experience)

    return PhilosophyConfig(
        id=data["id"],
        version=data["version"],
        structure_policy=structure_policy,
        intensity_policy=intensity_policy,
        volume_policy=volume_policy,
        constraints=constraints,
        rag_context_id=data["rag_context_id"],
    )
