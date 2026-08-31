from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r'^[A-Za-z0-9_.-]+$')
    # bcrypt truncates silently past 72 bytes, so reject rather than mislead.
    password: str = Field(min_length=8, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime
