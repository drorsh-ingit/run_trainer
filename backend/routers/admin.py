from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.models import TrainingPlan, User
from schemas import AdminPlanOut
from services.auth import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/plans", response_model=list[AdminPlanOut])
def admin_list_plans(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    rows = (
        db.query(TrainingPlan, User.username)
        .join(User, TrainingPlan.user_id == User.id)
        .order_by(TrainingPlan.created_at.desc())
        .all()
    )
    return [
        AdminPlanOut(
            id=p.id,
            username=u,
            goal_distance=p.goal_distance,
            goal_date=p.goal_date,
            plan_type=p.plan_type,
            plan_duration_weeks=p.plan_duration_weeks,
            created_at=p.created_at.isoformat() if p.created_at else "",
        )
        for p, u in rows
    ]
