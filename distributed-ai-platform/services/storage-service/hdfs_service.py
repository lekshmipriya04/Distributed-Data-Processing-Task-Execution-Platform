import httpx
import structlog
from typing import BinaryIO
from shared.common.exceptions import StorageError

logger = structlog.get_logger(__name__)

class HDFSService:
    def __init__(self, webhdfs_url: str, hdfs_user: str = "hadoop"):
        self.webhdfs_url = webhdfs_url
        self.user = hdfs_user

    async def upload_file(self, file_obj: BinaryIO, hdfs_path: str) -> None:
        """
        Uploads a file to HDFS using WebHDFS REST API.
        This is a two-step process:
        1. Submit PUT request and get redirection URL.
        2. Submit PUT request with data to the redirection URL.
        """
        url = f"{self.webhdfs_url}/webhdfs/v1{hdfs_path}?op=CREATE&user.name={self.user}&overwrite=true"
        
        async with httpx.AsyncClient() as client:
            # Step 1: Get redirect URL
            try:
                response = await client.put(url, follow_redirects=False)
                if response.status_code != 307:
                    raise StorageError(f"Expected 307 redirect from WebHDFS, got {response.status_code}")
                
                redirect_url = response.headers.get("Location")
                if not redirect_url:
                    raise StorageError("Missing Location header in WebHDFS response")
                
                # Step 2: Upload data
                # Using chunked reading to avoid loading large files fully into memory
                async def file_iterator():
                    while chunk := file_obj.read(8192):
                        yield chunk
                        
                upload_response = await client.put(redirect_url, content=file_iterator())
                upload_response.raise_for_status()
                logger.info("file_uploaded_to_hdfs", path=hdfs_path)
            except Exception as e:
                logger.error("hdfs_upload_error", error=str(e), path=hdfs_path)
                raise StorageError(f"Failed to upload file to HDFS: {e}")

    async def download_file(self, hdfs_path: str) -> bytes:
        """
        Reads a file back from HDFS using the WebHDFS OPEN operation.

        WebHDFS answers the namenode request with a 307 redirect to the
        datanode that actually holds the block; httpx follows that redirect
        automatically, so the final response body is the file content.
        """
        url = f"{self.webhdfs_url}/webhdfs/v1{hdfs_path}?op=OPEN&user.name={self.user}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                logger.info("file_downloaded_from_hdfs", path=hdfs_path)
                return response.content
            except Exception as e:
                logger.error("hdfs_download_error", error=str(e), path=hdfs_path)
                raise StorageError(f"Failed to download file from HDFS: {e}")
