"""
Approval popup that shows the resource request to the worker owner.
Uses tkinter for a simple cross-platform GUI.
Falls back to CLI if tkinter is not available.
"""
import asyncio
import structlog
import json
import webbrowser
import threading
import os
import socketserver
from http.server import SimpleHTTPRequestHandler
from string import Template
from typing import Optional

logger = structlog.get_logger(__name__)


class ResourceApprovalUI:
    """Shows approval dialog to worker owner and collects their decision."""

    async def show_approval_dialog(
        self,
        request_id: str,
        job_id: str,
        requestor: str,
        cpu_requested: int,
        memory_requested_gb: float,
        gpu_requested: bool,
        cpu_available: int,
        memory_available_gb: float,
        gpu_available: bool,
    ) -> Optional[dict]:
        """
        Show dialog and return approved resources or None if rejected.
        
        Returns:
            dict with cpu_approved, memory_approved_gb, gpu_approved
            None if rejected
        """
        try:
            return await self._show_web_dialog(
                request_id=request_id,
                job_id=job_id,
                requestor=requestor,
                cpu_requested=cpu_requested,
                memory_requested_gb=memory_requested_gb,
                gpu_requested=gpu_requested,
                cpu_available=cpu_available,
                memory_available_gb=memory_available_gb,
                gpu_available=gpu_available,
            )
        except Exception as e:
            logger.error(f"Web UI failed: {e}. Falling back to CLI.")
            # Fall back to CLI
            return await self._show_cli_dialog(
                request_id=request_id,
                job_id=job_id,
                requestor=requestor,
                cpu_requested=cpu_requested,
                memory_requested_gb=memory_requested_gb,
                gpu_requested=gpu_requested,
                cpu_available=cpu_available,
                memory_available_gb=memory_available_gb,
                gpu_available=gpu_available,
            )

    async def _show_web_dialog(self, **kwargs) -> Optional[dict]:
        """Show web-based modern GUI dialog."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        
        # Prepare parameters for the template
        params = kwargs.copy()
        params["cpu_default"] = min(params["cpu_requested"], params["cpu_available"])
        params["memory_default"] = min(params["memory_requested_gb"], params["memory_available_gb"])
        params["gpu_display"] = "flex" if params["gpu_available"] else "none"
        params["gpu_checked"] = "checked" if params["gpu_requested"] and params["gpu_available"] else ""
        
        class ApprovalHandler(SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
                
            def do_GET(self):
                if self.path == "/":
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    
                    static_dir = os.path.join(os.path.dirname(__file__), "static")
                    template_path = os.path.join(static_dir, "index.html")
                    
                    with open(template_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    html = Template(content).safe_substitute(**params)
                    self.wfile.write(html.encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()
                    
            def do_POST(self):
                if self.path == "/api/approve":
                    content_len = int(self.headers.get("Content-Length", 0))
                    post_data = self.rfile.read(content_len)
                    data = json.loads(post_data.decode("utf-8"))
                    
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')
                    
                    loop.call_soon_threadsafe(future.set_result, data)
                
                elif self.path == "/api/reject":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')
                    
                    loop.call_soon_threadsafe(future.set_result, None)
        
        # Start local server on a random free port
        httpd = socketserver.TCPServer(("127.0.0.1", 0), ApprovalHandler)
        port = httpd.server_address[1]
        
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        
        # Open default browser
        webbrowser.open(f"http://127.0.0.1:{port}/")
        
        try:
            result = await future
            if result and result.get("approved"):
                return {
                    "cpu_approved": result.get("cpu_approved"),
                    "memory_approved_gb": result.get("memory_approved_gb"),
                    "gpu_approved": result.get("gpu_approved"),
                }
            return None
        finally:
            httpd.shutdown()
            httpd.server_close()

    async def _show_cli_dialog(self, **kwargs) -> Optional[dict]:
        """CLI fallback approval dialog."""
        print("\n" + "="*50)
        print("DISTRIBUTED COMPUTE REQUEST")
        print("="*50)
        print(f"Job ID: {kwargs['job_id']}")
        print(f"Requestor: {kwargs['requestor']}")
        print(f"\nRequested:")
        print(f"  CPU: {kwargs['cpu_requested']} cores")
        print(f"  Memory: {kwargs['memory_requested_gb']:.1f} GB")
        print(f"  GPU: {'Yes' if kwargs['gpu_requested'] else 'No'}")
        print(f"\nYour available:")
        print(f"  CPU: {kwargs['cpu_available']} cores")
        print(f"  Memory: {kwargs['memory_available_gb']:.1f} GB")
        print(f"  GPU: {'Yes' if kwargs['gpu_available'] else 'No'}")
        print("="*50)
        
        loop = asyncio.get_event_loop()
        
        decision = await loop.run_in_executor(
            None, input, "\nApprove? [y/n]: "
        )
        
        if decision.lower() != 'y':
            return None
        
        cpu_input = await loop.run_in_executor(
            None, input, f"CPU cores to grant [1-{kwargs['cpu_available']}]: "
        )
        cpu_approved = min(int(cpu_input or kwargs['cpu_requested']), kwargs['cpu_available'])
        
        mem_input = await loop.run_in_executor(
            None, input, f"Memory GB to grant [0.5-{kwargs['memory_available_gb']:.1f}]: "
        )
        mem_approved = min(
            float(mem_input or kwargs['memory_requested_gb']),
            kwargs['memory_available_gb']
        )
        
        gpu_approved = False
        if kwargs['gpu_available'] and kwargs['gpu_requested']:
            gpu_input = await loop.run_in_executor(None, input, "Grant GPU? [y/n]: ")
            gpu_approved = gpu_input.lower() == 'y'
        
        return {
            "cpu_approved": cpu_approved,
            "memory_approved_gb": round(mem_approved, 1),
            "gpu_approved": gpu_approved,
        }
