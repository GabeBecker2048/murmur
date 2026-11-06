"""Request bodies. Responses are plain JSON built from store rows."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from murmur.jobs import JOB_TYPES

JobType = Literal[JOB_TYPES]  # type: ignore[valid-type]


class JobCreate(BaseModel):
    type: JobType = Field(description="One of the job types; see GET /jobs/types.")
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = Field(False, description="Required true for destructive job types.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"type": "run", "params": {"date": "2026-11-05"}},
                {"type": "news", "params": {"from": "2026-10-01", "to": "2026-10-31"}},
                {"type": "analyze", "params": {"all": True}},
                {"type": "delete_analysis", "params": {"date": "2026-11-05"}, "confirm": True},
            ]
        }
    }


class SettingsPatch(BaseModel):
    """TOML-shaped partial settings. Tables merge key by key, ``universe.static``
    replaces wholesale, ``null`` removes an override."""

    model_config = {"extra": "allow"}
