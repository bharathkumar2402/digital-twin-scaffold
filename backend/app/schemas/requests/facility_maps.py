import uuid

from pydantic import BaseModel, ConfigDict


class FacilityMapUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    original_filename: str
    format: str
    status: str
