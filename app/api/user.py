from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.user import LoginRequest, RegisterRequest
from app.services.auth_service import AuthService
from app.utils.response import success, error
from app.utils.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/user", tags=["用户"])
security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    return int(payload.get("sub"))


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    return AuthService.login(db, data.username, data.password)


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    return AuthService.register(db, data.username, data.password, data.email)


@router.get("/info")
def get_user_info(credentials: HTTPAuthorizationCredentials = Depends(security),
                  db: Session = Depends(get_db)):
    user_id = get_current_user(credentials)
    if not user_id:
        return error(1005, "Token 已过期")
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return error(1005, "用户不存在")
    return success({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "avatar": user.avatar,
        "nickname": user.nickname,
        "location": user.location,
        "preferences": user.preferences or {},
        "visaInfo": user.visa_info or {},
        "createdAt": str(user.created_at) if user.created_at else None
    })


@router.put("/info")
def update_user_info(data: dict, credentials: HTTPAuthorizationCredentials = Depends(security),
                     db: Session = Depends(get_db)):
    user_id = get_current_user(credentials)
    if not user_id:
        return error(1005, "Token 已过期")
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return error(1005, "用户不存在")
    if data.get("avatar"):
        user.avatar = data["avatar"]
    if data.get("nickname"):
        user.nickname = data["nickname"]
    db.commit()
    db.refresh(user)
    return success({
        "id": user.id,
        "avatar": user.avatar,
        "nickname": user.nickname
    }, "更新成功")


@router.put("/preferences")
def update_preferences(data: dict, credentials: HTTPAuthorizationCredentials = Depends(security),
                       db: Session = Depends(get_db)):
    user_id = get_current_user(credentials)
    if not user_id:
        return error(1005, "Token 已过期")
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return error(1005, "用户不存在")

    prefs = user.preferences or {}
    for key in ["budget", "travelStyle", "seasonPreference", "interests"]:
        if key in data:
            prefs[key] = data[key]
    user.preferences = prefs
    db.commit()
    return success({"preferences": user.preferences}, "偏好更新成功")


@router.put("/visa")
def update_visa(data: dict, credentials: HTTPAuthorizationCredentials = Depends(security),
                db: Session = Depends(get_db)):
    user_id = get_current_user(credentials)
    if not user_id:
        return error(1005, "Token 已过期")
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return error(1005, "用户不存在")

    visa_info = user.visa_info or {}
    if "hasVisa" in data:
        visa_info["hasVisa"] = data["hasVisa"]
    if "passportType" in data:
        visa_info["passportType"] = data["passportType"]
    if "visas" in data:
        visa_info["visas"] = data["visas"]
    user.visa_info = visa_info
    db.commit()
    return success({"visaInfo": user.visa_info}, "签证信息更新成功")


@router.put("/location")
def update_location(data: dict, credentials: HTTPAuthorizationCredentials = Depends(security),
                    db: Session = Depends(get_db)):
    user_id = get_current_user(credentials)
    if not user_id:
        return error(1005, "Token 已过期")
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return error(1005, "用户不存在")

    user.location = data.get("location", user.location)
    db.commit()
    return success({"location": user.location}, "所在地更新成功")
