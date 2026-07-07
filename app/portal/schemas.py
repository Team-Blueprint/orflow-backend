from pydantic import BaseModel, Field

class VerifyAccessRequest(BaseModel):
    token_slug: str = Field(..., description="The unique token slug for portal access")
    pin: str = Field(..., description="The clear-text PIN")

class VerifyAccessResponse(BaseModel):
    access_token: str

class UpdatePinRequest(BaseModel):
    current_pin: str
    new_pin: str = Field(..., min_length=6)
