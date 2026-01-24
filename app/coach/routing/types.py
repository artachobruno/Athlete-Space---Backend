"""Routing types: RoutedTool carries name and mode (CREATE/MODIFY/PREVIEW)."""

from typing import Literal

from pydantic import BaseModel

RoutedMode = Literal["CREATE", "MODIFY", "PREVIEW"]


class RoutedTool(BaseModel):
    """Routed semantic tool with optional mode (CREATE vs MODIFY vs PREVIEW)."""

    name: str
    mode: RoutedMode | None = None
