import paramiko
import time
import io
import structlog
from typing import Optional, Dict, Any

logger = structlog.get_logger(__name__)

class SSHExecutor:
    def __init__(self, host: str, port: int, username: str, password: Optional[str] = None, private_key: Optional[str] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        
    def _get_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 10
        }
        
        if self.private_key:
            key_file = io.StringIO(self.private_key)
            pkey = paramiko.RSAKey.from_private_key(key_file)
            connect_kwargs["pkey"] = pkey
        elif self.password:
            connect_kwargs["password"] = self.password
        else:
            raise ValueError("Either password or private_key must be provided")
            
        client.connect(**connect_kwargs)
        return client

    def test_connection(self) -> Dict[str, Any]:
        client = None
        try:
            client = self._get_client()
            stdin, stdout, stderr = client.exec_command("echo 'connection_successful'")
            exit_status = stdout.channel.recv_exit_status()
            return {"status": "success", "message": "Successfully connected via SSH"} if exit_status == 0 else {"status": "error", "message": "Command execution failed"}
        except Exception as e:
            logger.error("ssh_test_failed", error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            if client:
                client.close()
                
    def execute_task(self, task_code: str) -> Dict[str, Any]:
        client = None
        try:
            client = self._get_client()
            sftp = client.open_sftp()
            
            # Check CPU and RAM basics
            _, stdout, _ = client.exec_command("grep -c ^processor /proc/cpuinfo")
            cpu_cores = stdout.read().decode().strip()
            _, stdout, _ = client.exec_command("free -m | awk '/^Mem:/{print $2}'")
            total_ram = stdout.read().decode().strip()
            
            remote_script_path = f"/tmp/task_{int(time.time())}.py"
            file = sftp.file(remote_script_path, "w")
            file.write(task_code)
            file.close()
            
            # Execute
            stdin, stdout, stderr = client.exec_command(f"python3 {remote_script_path}")
            out = stdout.read().decode()
            err = stderr.read().decode()
            exit_status = stdout.channel.recv_exit_status()
            
            # Cleanup
            sftp.remove(remote_script_path)
            sftp.close()
            
            return {
                "status": "success" if exit_status == 0 else "error",
                "exit_code": exit_status,
                "stdout": out,
                "stderr": err,
                "system_info": {"cpu_cores": cpu_cores, "total_ram_mb": total_ram}
            }
        except Exception as e:
            logger.error("ssh_execution_failed", error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            if client:
                client.close()
