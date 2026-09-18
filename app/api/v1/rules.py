"""Customer Service Rule Engine API。"""

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from app.rules import RuleContext, RuleEvaluationResult, default_rule_registry
from app.schemas.common import ApiResponse


NonBlankMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]


class RuleEvaluationRequest(BaseModel):
    """Customer Demo 到规则层的最小请求契约。"""

    message: NonBlankMessage
    session_id: str | None = Field(default=None, max_length=128)
    customer_id: str | None = Field(default=None, max_length=128)
    shop_id: str | None = Field(default=None, max_length=128)
    current_product_id: str | None = Field(default=None, max_length=64)
    service_stage: Literal["pre_sale", "post_sale", "general"] | None = None
    channel: str | None = Field(default=None, max_length=32)


router = APIRouter(tags=["rules"])


@router.post("/rules/evaluate", response_model=ApiResponse[RuleEvaluationResult])
async def evaluate_rules(request: RuleEvaluationRequest) -> ApiResponse[RuleEvaluationResult]:
    """执行确定性规则；不命中 Terminal 时留给后续 Router 继续。"""
    context = RuleContext(
        session_id=request.session_id,
        customer_id=request.customer_id,
        shop_id=request.shop_id,
        current_product_id=request.current_product_id,
        service_stage=request.service_stage,
        channel=request.channel,
    )
    result = default_rule_registry().evaluate(request.message, context)
    return ApiResponse(data=result)
