import uuid

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    plan_tier: str = Field(min_length=1, max_length=50)


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    plan_tier: str | None = Field(default=None, min_length=1, max_length=50)


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    plan_tier: str

    model_config = {"from_attributes": True}
