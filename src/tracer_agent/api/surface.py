"""이 프로세스가 실제로 여는 경로를 계약과 대조할 수 있는 어휘로 낸다."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# 배포 단위 사이에서만 오가는 창구라 게이트웨이가 바깥에 열지 않는다.
SURFACE_PATH = "/internal/surface"


def served_routes(application: FastAPI) -> list[dict[str, str]]:
    """손으로 적은 목록이 라우팅과 갈라지지 않도록 앱이 조립한 표를 그대로 읽는다."""
    declared = {
        (method.upper(), path)
        for path, operations in application.openapi()["paths"].items()
        for method in operations
    }
    return [{"method": method, "path": path} for method, path in sorted(declared)]


async def get_served_surface(request: Request) -> JSONResponse:
    """이 프로세스에 등록된 창구를 메서드와 경로 템플릿의 쌍으로 낸다."""
    return JSONResponse(status_code=200, content={"ok": True, "data": {"routes": served_routes(request.app)}})
