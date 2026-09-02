"""
模型广场（Model Hub）反向代理

将 /api/v1/hub/* 请求代理到远程量化模型社区广场（quantdb.quantmind.cloud）。
写入类操作（上传/发布/点赞/下架）需要 X-API-Key，这里统一注入服务端已配置的
QUANTDB_API_KEY（见 shared/runtime_secrets.py），前端无需再持有明文 Key。
"""

import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from backend.shared.runtime_secrets import get_secret

router = APIRouter(tags=["HubProxy"])

HUB_BASE_URL = os.getenv("QUANTDB_HUB_URL", "https://quantdb.quantmind.cloud").rstrip("/")

_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _forward_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}


@router.api_route("/api/v1/hub/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_model_hub(path: str, request: Request):
    api_key = get_secret("QUANTDB_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="QUANTDB_API_KEY 未配置，无法访问模型广场。请在「个人中心 → 数据平台 QuantDB」中配置 API Key。",
        )

    upstream_url = f"{HUB_BASE_URL}/api/v1/hub/{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    method = request.method.upper()
    headers = _forward_headers(request)
    headers["X-API-Key"] = api_key
    body = await request.body()

    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, upstream_url,
                content=body if body else None,
                headers=headers,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers={"content-type": resp.headers.get("content-type", "application/json")},
            )
    except httpx.HTTPError:
        return PlainTextResponse("模型广场服务不可达", status_code=502)
