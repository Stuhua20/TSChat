from fastapi import Cookie, HTTPException

from . import storage


def get_current_user(
    session: str | None = Cookie(default=None, alias="textsql_session"),
):
    user = storage.session_user(session)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
