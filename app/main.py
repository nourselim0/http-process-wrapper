from typing import Annotated as Ann

import jwt
from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .service import LogLine, ProcessWrapper, processes_registry

app = FastAPI()
bearer_auth = HTTPBearer(auto_error=False)
api_key_auth = APIKeyHeader(name="X-API-Key", auto_error=False)


def enforce_http_auth(
    auth_header: Ann[HTTPAuthorizationCredentials | None, Depends(bearer_auth)],
    api_key: Ann[str | None, Depends(api_key_auth)],
) -> None:
    enforce_ws_auth(auth_header.credentials if auth_header else None, api_key)


def enforce_ws_auth(
    jwt_token: Ann[str | None, Query()] = None,
    api_key: Ann[str | None, Query()] = None,
) -> None:
    if settings.jwt_algo:
        if jwt_token is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="JWT required")
        try:
            jwt.decode(
                jwt_token,
                settings.jwt_verif_key,
                algorithms=[settings.jwt_algo],
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid JWT"
            ) from exc

    if settings.api_key:
        if not api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")
        if api_key != settings.api_key:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


async def resolve_process(name: str) -> ProcessWrapper:
    """Dependency: resolve a process by name or raise 404."""
    proc = processes_registry.get(name)
    if proc is None:
        raise HTTPException(status_code=404, detail="Process not found")
    return proc


@app.get("/procs", dependencies=[Depends(enforce_http_auth)])
async def list_processes() -> list[ProcessWrapper]:
    return list(processes_registry.values())


@app.post("/procs", dependencies=[Depends(enforce_http_auth)])
async def create_process(proc: ProcessWrapper, start: bool = True) -> ProcessWrapper:
    if proc.name in processes_registry:
        raise HTTPException(status_code=400, detail="Process with this name already exists")
    processes_registry[proc.name] = proc
    if start:
        await proc.start()
    return proc


@app.get("/procs/{name}", dependencies=[Depends(enforce_http_auth)])
async def get_process(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
) -> ProcessWrapper:
    return proc


@app.post("/procs/{name}/start", dependencies=[Depends(enforce_http_auth)])
async def start_process(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
) -> ProcessWrapper:
    if proc.pid is not None:
        raise HTTPException(status_code=400, detail="Process has already started")
    await proc.start()
    return proc


@app.post(
    "/procs/{name}/write",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_http_auth)],
)
async def write_process_input(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
    line: Ann[str, Body()],
) -> Response:
    await proc.write_stdin(line)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@app.get("/procs/{name}/tail", dependencies=[Depends(enforce_http_auth)])
async def tail_process_output(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
    n: int,
    include_stderr: bool = True,
) -> list[LogLine]:
    return await proc.tail(n, include_stderr)


@app.get("/procs/{name}/tail-text", dependencies=[Depends(enforce_http_auth)])
async def tail_process_output_text(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
    n: int,
    include_stderr: bool = True,
    prefix_timestamp: bool = True,
) -> list[str]:
    lines = await proc.tail(n, include_stderr)
    if prefix_timestamp:
        return [f"{line.timestamp.isoformat()} | {line.text}" for line in lines]
    return [line.text for line in lines]


@app.websocket("/procs/{name}/tail-stream", dependencies=[Depends(enforce_ws_auth)])
async def tail_process_output_stream(
    websocket: WebSocket,
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
    n: int,
):
    await websocket.accept()
    tail_stream = await proc.tail_stream(n)
    try:
        async for log_line in tail_stream:
            await websocket.send_text(log_line.model_dump_json())
    except WebSocketDisconnect:
        await proc.unsubscribe_tail_stream(tail_stream)


@app.post("/procs/{name}/stop", dependencies=[Depends(enforce_http_auth)])
async def stop_process(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
    kill: bool = False,
) -> ProcessWrapper:
    await proc.stop(kill)
    return proc


@app.post("/procs/{name}/restart", dependencies=[Depends(enforce_http_auth)])
async def restart_process(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
    kill_existing: bool = False,
    clear_logs: bool = False,
) -> ProcessWrapper:
    await proc.restart(kill_existing, clear_logs)
    return proc


@app.delete(
    "/procs/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_http_auth)],
)
async def delete_process(
    proc: Ann[ProcessWrapper, Depends(resolve_process)],
) -> Response:
    if proc.returncode is None:
        raise HTTPException(status_code=400, detail="Process is still running")
    del processes_registry[proc.name]
    return Response(status_code=status.HTTP_204_NO_CONTENT)
