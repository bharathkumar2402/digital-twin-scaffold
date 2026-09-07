from pydantic import BaseModel, EmailStr, Field

from app.models.user import Role


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    role: Role
