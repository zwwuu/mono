from datetime import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel, ConfigDict


class WorkflowCreateRequest(BaseModel):
    workflow_type: str = "purchase_request"
    title: Optional[str] = None
    payload: Dict[str, Any] = {}

    model_config = ConfigDict(extra="allow")


class WorkflowStepActionRequest(BaseModel):
    action: str
    comment: Optional[str] = ""
    payload: Dict[str, Any] = {}


class WorkflowResponse(BaseModel):
    id: int
    tracking_code: str
    title: str
    workflow_type: str
    current_step: str
    status: str
    requester_id: int
    assigned_role: str
    form_data: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
