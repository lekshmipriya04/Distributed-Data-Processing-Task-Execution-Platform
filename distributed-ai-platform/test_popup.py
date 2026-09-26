import asyncio
from worker.approval_ui.popup import ResourceApprovalUI

async def test_popup():
    ui = ResourceApprovalUI()
    print("Opening Tkinter popup...")
    result = await ui.show_approval_dialog(
        request_id="req-12345",
        job_id="job-abcde-12345",
        requestor="TestUser",
        cpu_requested=4,
        memory_requested_gb=8.0,
        gpu_requested=True,
        cpu_available=8,
        memory_available_gb=16.0,
        gpu_available=True
    )
    print("Popup closed. Result:")
    print(result)

if __name__ == "__main__":
    asyncio.run(test_popup())
