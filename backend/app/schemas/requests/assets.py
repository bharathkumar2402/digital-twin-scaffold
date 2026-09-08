import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.models.asset import AssetStatus


class AssetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=100)
    x: float
    y: float
    status: AssetStatus = AssetStatus.OPERATIONAL
    installed_date: date | None = None
    manufacturer: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)


class AssetUpdateRequest(BaseModel):
    """All fields optional - also used to commit a drag-and-drop placement, which
    only ever sends `x`/`y`."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: str | None = Field(default=None, min_length=1, max_length=100)
    x: float | None = None
    y: float | None = None
    status: AssetStatus | None = None
    installed_date: date | None = None
    manufacturer: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    name: str
    type: str
    x: float
    y: float
    status: AssetStatus
    installed_date: date | None
    manufacturer: str | None
    model: str | None
