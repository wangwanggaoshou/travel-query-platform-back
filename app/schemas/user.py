from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)
    email: str = Field(...)


class UserInfoResponse(BaseModel):
    id: int
    username: str
    email: str
    avatar: Optional[str] = None
    nickname: Optional[str] = None
    location: Optional[str] = None
    preferences: Optional[dict] = None
    visaInfo: Optional[dict] = None
    createdAt: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    avatar: Optional[str] = None
    nickname: Optional[str] = None


class PreferencesRequest(BaseModel):
    budget: Optional[str] = None
    travelStyle: Optional[list[str]] = None
    seasonPreference: Optional[str] = None
    interests: Optional[list[str]] = None


class VisaRequest(BaseModel):
    hasVisa: list[str] = Field(default_factory=list)
    passportType: Optional[str] = None


class LocationRequest(BaseModel):
    location: str = Field(..., min_length=1)
