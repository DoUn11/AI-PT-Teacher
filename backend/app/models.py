"""모든 ORM 모델을 import해 Base.metadata에 등록한다."""
from app.modules.auth.models import User  # noqa: F401
from app.modules.workouts.models import RepRecord, WorkoutSession  # noqa: F401
