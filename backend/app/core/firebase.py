"""Firebase(Firestore) 동기화. 선택 기능이다.

서비스 계정 키(`FIREBASE_CREDENTIALS`, 기본 app/core/serviceAccountkey.json)나
`firebase_admin` 패키지가 없으면 조용히 건너뛴다. 이때 데이터는 로컬 DB(SQLite)에만 저장된다.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CRED_PATH = Path(os.getenv("FIREBASE_CREDENTIALS", Path(__file__).parent / "serviceAccountkey.json"))
_client = None
_disabled = False


def _get_client():
    """Firestore 클라이언트를 처음 필요할 때 만든다. 쓸 수 없으면 None."""
    global _client, _disabled
    if _client is not None or _disabled:
        return _client
    try:
        if not _CRED_PATH.is_file():
            raise FileNotFoundError(_CRED_PATH)
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(str(_CRED_PATH)))
        _client = firestore.client()
    except Exception as exc:  # 키/패키지 없음, 잘못된 키 등
        _disabled = True
        logger.warning("Firebase 동기화 비활성화 (로컬 DB만 사용): %s", exc)
    return _client


#Sending the user's info to firebase
def save_user_to_firestore(user_uuid:str, email:str,nickname:str,created_at:str):
    client = _get_client()
    if client is None:
        return
    user_data = {
        "email": email,
        "nickname": nickname,
        "created_at": created_at
    }
    client.collection("users").document(user_uuid).set(user_data)

#Sending Workout Session & Rep Records To Firebase
def save_workout_session_to_firebase(
    session_uuid:str,
    user_uuid:str,
    exercise: str,
    status:str,
    started_at:str,
    finished_at:str,
    total_reps:int,
    avg_score: float|None,
    reps_list:list[dict]
):
    client = _get_client()
    if client is None:
        return
    session_data = {
        "user_id": user_uuid,
        "exercise": exercise,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_reps": total_reps,
        "avg_score": avg_score
    }
    session_ref = client.collection("workout_sessions").document(session_uuid)
    session_ref.set(session_data)
    
    for rep in reps_list:
        rep_data= {
            "index":rep["index"],
            "score": rep["score"],
            "min_angle": rep["min_angle"],
            "feedback": rep["feedback"],
            "completed_at": rep["completed_at"]
        }
        session_ref.collection("reps").document(str(rep["index"])).set(rep_data)
        