"""
Approval popup that shows the resource request to the worker owner.
Uses tkinter for a simple cross-platform GUI.
Falls back to CLI if tkinter is not available.
"""
import asyncio
import structlog
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
            return await self._show_tkinter_dialog(
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
        except Exception:
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

    async def _show_tkinter_dialog(self, **kwargs) -> Optional[dict]:
        """Show tkinter-based GUI dialog."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._create_tkinter_dialog, kwargs)

    def _create_tkinter_dialog(self, params: dict) -> Optional[dict]:
        import tkinter as tk
        from tkinter import ttk
        
        result = {"approved": False, "cpu": 0, "memory": 0.0, "gpu": False}
        
        root = tk.Tk()
        root.title("Resource Request - Distributed AI Platform")
        root.geometry("500x450")
        root.configure(bg="#f0f0f0")
        
        # Header
        header = tk.Label(
            root,
            text="⚡ Distributed Compute Request",
            font=("Arial", 14, "bold"),
            bg="#2563EB",
            fg="white",
            pady=10,
        )
        header.pack(fill=tk.X)
        
        # Info frame
        info_frame = tk.Frame(root, bg="#f0f0f0", padx=20, pady=10)
        info_frame.pack(fill=tk.X)
        
        tk.Label(info_frame, text=f"Job ID: {params['job_id'][:8]}...", bg="#f0f0f0").pack(anchor=tk.W)
        tk.Label(info_frame, text=f"Requestor: {params['requestor']}", bg="#f0f0f0").pack(anchor=tk.W)
        
        # Requested resources
        req_frame = tk.LabelFrame(root, text="Requested Resources", padx=10, pady=10, bg="#f0f0f0")
        req_frame.pack(fill=tk.X, padx=20, pady=5)
        
        tk.Label(req_frame, text=f"CPU: {params['cpu_requested']} cores", bg="#f0f0f0").pack(anchor=tk.W)
        tk.Label(req_frame, text=f"Memory: {params['memory_requested_gb']:.1f} GB", bg="#f0f0f0").pack(anchor=tk.W)
        tk.Label(
            req_frame,
            text=f"GPU: {'Yes' if params['gpu_requested'] else 'No'}",
            bg="#f0f0f0",
        ).pack(anchor=tk.W)
        
        # Approval frame with sliders
        approve_frame = tk.LabelFrame(root, text="Approve Resources", padx=10, pady=10, bg="#f0f0f0")
        approve_frame.pack(fill=tk.X, padx=20, pady=5)
        
        # CPU slider
        tk.Label(approve_frame, text="CPU cores to grant:", bg="#f0f0f0").pack(anchor=tk.W)
        cpu_var = tk.IntVar(value=min(params['cpu_requested'], params['cpu_available']))
        cpu_slider = ttk.Scale(
            approve_frame, from_=1, to=params['cpu_available'],
            variable=cpu_var, orient=tk.HORIZONTAL, length=300
        )
        cpu_slider.pack()
        cpu_label = tk.Label(approve_frame, textvariable=cpu_var, bg="#f0f0f0")
        cpu_label.pack()
        
        # Memory slider
        tk.Label(approve_frame, text="Memory (GB) to grant:", bg="#f0f0f0").pack(anchor=tk.W)
        mem_var = tk.DoubleVar(
            value=min(params['memory_requested_gb'], params['memory_available_gb'])
        )
        mem_slider = ttk.Scale(
            approve_frame,
            from_=0.5,
            to=params['memory_available_gb'],
            variable=mem_var,
            orient=tk.HORIZONTAL,
            length=300,
        )
        mem_slider.pack()
        mem_label = tk.Label(approve_frame, textvariable=mem_var, bg="#f0f0f0")
        mem_label.pack()
        
        # GPU checkbox
        gpu_var = tk.BooleanVar(
            value=params['gpu_requested'] and params['gpu_available']
        )
        if params['gpu_available']:
            gpu_check = tk.Checkbutton(
                approve_frame, text="Grant GPU access", variable=gpu_var, bg="#f0f0f0"
            )
            gpu_check.pack(anchor=tk.W)
        
        # Buttons
        btn_frame = tk.Frame(root, bg="#f0f0f0")
        btn_frame.pack(pady=10)
        
        def on_approve():
            result["approved"] = True
            result["cpu"] = cpu_var.get()
            result["memory"] = round(mem_var.get(), 1)
            result["gpu"] = gpu_var.get()
            root.destroy()
        
        def on_reject():
            result["approved"] = False
            root.destroy()
        
        approve_btn = tk.Button(
            btn_frame,
            text="✓ Approve",
            command=on_approve,
            bg="#16a34a",
            fg="white",
            width=15,
            font=("Arial", 10, "bold"),
        )
        approve_btn.pack(side=tk.LEFT, padx=10)
        
        reject_btn = tk.Button(
            btn_frame,
            text="✗ Reject",
            command=on_reject,
            bg="#dc2626",
            fg="white",
            width=15,
            font=("Arial", 10, "bold"),
        )
        reject_btn.pack(side=tk.LEFT, padx=10)
        
        root.mainloop()
        
        if result["approved"]:
            return {
                "cpu_approved": result["cpu"],
                "memory_approved_gb": result["memory"],
                "gpu_approved": result["gpu"],
            }
        return None

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
