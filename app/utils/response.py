from typing import Optional, Any
from pydantic import BaseModel


class ResponseModel(BaseModel):
    code: int = 200
    message: str = "success"
    data: Optional[Any] = None


def success(data: Any = None, message: str = "success") -> dict:
    return {"code": 200, "message": message, "data": data}


def error(code: int = 500, message: str = "error", data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}


class PaginatedData(BaseModel):
    list: list
    total: int
    page: int
    pageSize: int


def paginated(items: list, total: int, page: int, page_size: int) -> dict:
    return {
        "list": items,
        "total": total,
        "page": page,
        "pageSize": page_size
    }
