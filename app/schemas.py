from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

class SupplierCreate(BaseModel):
    name: str = Field(min_length=2, max_length=250)
    country: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=150)
    website: HttpUrl | None = None

class SupplierOut(BaseModel):
    id: int
    name: str
    country: str | None
    category: str | None
    website: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class AssessmentRequest(BaseModel):
    supplier_id: int
    include_external_research: bool = False
