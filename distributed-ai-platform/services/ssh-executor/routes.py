from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from ssh_client import SSHExecutor

router = APIRouter()

class SSHConnectionRequest(BaseModel):
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None

class SSHTaskRequest(SSHConnectionRequest):
    task_code: str

@router.post("/workers/test")
async def test_worker(req: SSHConnectionRequest):
    executor = SSHExecutor(req.host, req.port, req.username, req.password, req.private_key)
    res = executor.test_connection()
    if res["status"] == "error":
        raise HTTPException(status_code=400, detail=res["message"])
    return res

@router.post("/tasks/execute")
async def execute_task(req: SSHTaskRequest):
    executor = SSHExecutor(req.host, req.port, req.username, req.password, req.private_key)
    res = executor.execute_task(req.task_code)
    return res
