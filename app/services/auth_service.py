from sqlalchemy.orm import Session
from app.models.user import User
from app.utils.security import verify_password, get_password_hash, create_access_token
from app.utils.response import success, error


class AuthService:
    @staticmethod
    def login(db: Session, username: str, password: str) -> dict:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return error(1001, "用户名或密码错误")
        if user.is_locked:
            return error(1002, "账号已被锁定")
        if not verify_password(password, user.password_hash):
            return error(1001, "用户名或密码错误")
        if not user.is_active:
            return error(1002, "账号已被禁用")

        token = create_access_token({"sub": str(user.id), "username": user.username})
        return success({
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "avatar": user.avatar
            }
        }, "登录成功")

    @staticmethod
    def register(db: Session, username: str, password: str, email: str) -> dict:
        if db.query(User).filter(User.username == username).first():
            return error(1003, "用户名已存在")
        if db.query(User).filter(User.email == email).first():
            return error(1004, "邮箱已被注册")

        user = User(
            username=username,
            password_hash=get_password_hash(password),
            email=email
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return success({"id": user.id, "username": user.username}, "注册成功")
