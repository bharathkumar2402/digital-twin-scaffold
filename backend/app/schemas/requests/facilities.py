import uuid

from pydantic import BaseModel


class FacilityResponse(BaseModel):
    """One facility as shown in the facility picker."""

    id: uuid.UUID
    name: str
    # Newest floor-plan upload that finished tiling, or None if the facility has no map yet.
    latest_map_upload_id: uuid.UUID | None
    asset_count: int
