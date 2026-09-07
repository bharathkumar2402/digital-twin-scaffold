import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    tenant_id: uuid.UUID
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    tenant_id: uuid.UUID
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str

    model_config = {"from_attributes": True}
