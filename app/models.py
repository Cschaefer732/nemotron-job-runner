from pydantic import BaseModel, field_validator
from typing import Literal, Optional


class JobSubmit(BaseModel):
    type: str
    payload: dict = {}
    priority: int = 50
    trigger: Literal["manual", "cron", "agent", "hook"] = "manual"
    scheduled_at: Optional[str] = None

    @field_validator("priority")
    @classmethod
    def priority_in_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("priority must be between 0 (highest) and 100 (lowest)")
        return v


class JobPatch(BaseModel):
    priority: Optional[int] = None
    payload: Optional[dict] = None
    scheduled_at: Optional[str] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_cancelled(cls, v):
        if v is not None and v != "cancelled":
            raise ValueError("status can only be set to 'cancelled' via PATCH")
        return v


class HookRegister(BaseModel):
    job_type: str
    payload: dict = {}
