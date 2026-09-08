import uuid

from pydantic import BaseModel, ConfigDict


class AssetDependencyCreateRequest(BaseModel):
    parent_asset_id: uuid.UUID
    child_asset_id: uuid.UUID


class AssetDependencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    parent_asset_id: uuid.UUID
    child_asset_id: uuid.UUID
