import uuid

from pydantic import BaseModel


class FloorResponse(BaseModel):
    """One floor of a facility: its level, display name, newest finished map and asset count."""

    level: int
    name: str
    map_upload_id: uuid.UUID
    asset_count: int


class FacilityResponse(BaseModel):
    """One facility as shown in the facility picker."""

    id: uuid.UUID
    name: str
    # Newest floor-plan upload that finished tiling, or None if the facility has no map yet.
    # The map to open first: the ground floor's if there is one, else the lowest floor's.
    latest_map_upload_id: uuid.UUID | None
    asset_count: int
    floors: list[FloorResponse] = []
