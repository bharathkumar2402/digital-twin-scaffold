import uuid

from pydantic import BaseModel, ConfigDict


class FacilityMapUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    original_filename: str
    format: str
    status: str


class FacilityMapUploadStatusResponse(FacilityMapUploadResponse):
    """Adds tile info once the sandbox pipeline finishes (or fails). `tile_url_template`
    is only set once `status == "tiled"` — before that there's no tile pyramid to point
    at yet."""

    tile_prefix: str | None = None
    tile_url_template: str | None = None
