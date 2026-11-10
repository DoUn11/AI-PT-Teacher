import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate("app/core/serviceAccountkey.json")
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

#Creating Firebase database client
db = firestore.client()

#Sending the user's info to firebase
def save_user_to_firestore(user_id:int, email:str,nickname:str,created_at:str):
    user_data = {
        "email": email,
        "nickname": nickname,
        "created_at": created_at
    }
    db.collection("users").document(str(user_id)).set(user_data)

#Sending Workout Session & Rep Records To Firebase
def save_workout_session_to_firebase(
    session_id:int,
    user_id:int,
    exercise: str,
    status:str,
    started_at:str,
    finished_at:str,
    total_reps:int,
    avg_score: float|None,
    reps_list:list[dict]
):
    session_data = {
        "user_id": user_id,
        "exercise": exercise,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_reps": total_reps,
        "avg_score": avg_score
    }
    session_ref = db.collection("workout_sessions").document(str(session_id))
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
        