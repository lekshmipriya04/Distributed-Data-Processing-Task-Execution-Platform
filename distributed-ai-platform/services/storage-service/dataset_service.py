"""
High-level service for handling dataset lifecycle:
1. Upload to HDFS
2. Extract metadata
3. Validate
4. Persist to PostgreSQL
"""
import csv
import io
import json
import uuid
import zipfile
import structlog
from typing import Optional
from fastapi import UploadFile

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import Dataset
from hdfs_service import HDFSService
from shared.common.exceptions import ValidationError

logger = structlog.get_logger(__name__)


class DatasetService:
    def __init__(self, db_session: AsyncSession, hdfs_service: HDFSService):
        self._db = db_session
        self._hdfs = hdfs_service

    async def create_dataset(
        self,
        file: UploadFile,
        name: str,
        description: Optional[str],
        created_by: Optional[str] = None,
    ) -> Dataset:
        """Upload file to HDFS and create a database record."""
        # 1. Validate extension
        filename = file.filename or ""
        if not any(filename.endswith(ext) for ext in [".csv", ".parquet"]):
            raise ValidationError("Only .csv or .parquet files are supported")
        
        file_format = "csv" if filename.endswith(".csv") else "parquet"
        dataset_id = uuid.uuid4()
        
        # 2. Upload to HDFS
        hdfs_path = f"/platform/raw/{dataset_id}.{file_format}"
        logger.info("uploading_dataset_to_hdfs", dataset_id=str(dataset_id), path=hdfs_path)
        
        try:
            # Read into memory in chunks (or use file.file directly if HDFSService supports it)
            # For simplicity, assuming HDFSService handles it
            await self._hdfs.upload_file(file.file, hdfs_path)
            
            # 3. Save metadata
            dataset = Dataset(
                id=dataset_id,
                name=name,
                description=description,
                hdfs_path=hdfs_path,
                file_format=file_format,
                created_by=created_by,
                validation_status="pending"
            )
            
            self._db.add(dataset)
            await self._db.commit()
            await self._db.refresh(dataset)
            
            logger.info("dataset_created", dataset_id=str(dataset_id))
            return dataset
            
        except Exception as e:
            logger.error("dataset_upload_failed", error=str(e))
            raise

    async def create_dataset_from_images(
        self,
        zip_bytes: bytes,
        name: str,
        description: Optional[str],
        created_by: Optional[str] = None,
        image_size: int = 8,
        color: str = "grayscale",
    ) -> Dataset:
        """Convert a zip of label-foldered images into a flattened pixel CSV.

        Expected zip layout: ``<label>/<image files>`` (folder name = class).
        Each image is decoded, grayscaled, resized to a square ``image_size``,
        and flattened to columns ``p0 … p{S*S-1}`` plus a numeric ``label``.
        The result is stored as an ordinary CSV via the same HDFS path the CSV
        upload uses, so the whole training pipeline downstream is unchanged;
        ``schema_json`` records the image shape + columns so the UI can auto-fill.
        """
        from PIL import Image  # imported lazily so the module loads without Pillow

        s = max(4, int(image_size))
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
        samples: list[tuple[str, list[int]]] = []
        labels_seen: set[str] = set()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
                if len(parts) < 2 or not parts[-1].lower().endswith(exts):
                    continue  # need a label folder; skip top-level / non-images
                label_name = parts[-2]
                try:
                    with z.open(info) as fh:
                        im = Image.open(io.BytesIO(fh.read())).convert("L").resize((s, s))
                except Exception:
                    continue  # skip unreadable files rather than failing the batch
                samples.append((label_name, list(im.getdata())))
                labels_seen.add(label_name)
        if not samples:
            raise ValidationError(
                "No images found. Expected a .zip laid out as <label>/<image files>."
            )
        # Map label folders -> numeric indices; integer-named folders keep order.
        def _sort_key(v: str):
            try:
                return (0, int(v))
            except ValueError:
                return (1, v)

        class_names = sorted(labels_seen, key=_sort_key)
        label_index = {ln: i for i, ln in enumerate(class_names)}
        px = s * s
        headers = [f"p{i}" for i in range(px)] + ["label"]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        for label_name, pixels in samples:
            writer.writerow(list(pixels) + [label_index[label_name]])
        csv_bytes = buf.getvalue().encode("utf-8")

        dataset_id = uuid.uuid4()
        hdfs_path = f"/platform/raw/{dataset_id}.csv"
        logger.info("uploading_image_dataset_to_hdfs", dataset_id=str(dataset_id),
                    path=hdfs_path, images=len(samples), side=s, classes=len(class_names))
        await self._hdfs.upload_file(io.BytesIO(csv_bytes), hdfs_path)

        schema = {
            "columns": headers,
            "feature_columns": headers[:-1],
            "target_column": "label",
            "num_classes": len(class_names),
            "class_names": class_names,
            "image": {"side": s, "channels": 1},
        }
        dataset = Dataset(
            id=dataset_id,
            name=name,
            description=description,
            hdfs_path=hdfs_path,
            file_format="csv",
            created_by=created_by,
            row_count=len(samples),
            column_count=px + 1,
            size_bytes=len(csv_bytes),
            schema_json=json.dumps(schema),
            validation_status="pending",
        )
        self._db.add(dataset)
        await self._db.commit()
        await self._db.refresh(dataset)
        logger.info("image_dataset_created", dataset_id=str(dataset_id))
        return dataset

    async def get_dataset(self, dataset_id: uuid.UUID, owner_id: str) -> Optional[Dataset]:
        """Return the dataset if it exists **and** belongs to ``owner_id``.

        A dataset owned by someone else is indistinguishable from a missing one
        (both return None), so callers cannot probe for other users' IDs.
        """
        result = await self._db.execute(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.created_by == owner_id)
        )
        return result.scalar_one_or_none()

    async def download_dataset(self, dataset_id: uuid.UUID, owner_id: str) -> tuple[bytes, str]:
        """Fetch the raw file bytes for one of ``owner_id``'s datasets from HDFS.

        Returns (content, file_format). Raises ValidationError if the dataset
        does not exist or is not owned by ``owner_id`` so the route can
        translate it into a 404.
        """
        dataset = await self.get_dataset(dataset_id, owner_id)
        if dataset is None:
            raise ValidationError("Dataset not found")
        content = await self._hdfs.download_file(dataset.hdfs_path)
        return content, dataset.file_format

    async def delete_dataset(self, dataset_id: uuid.UUID, owner_id: str) -> bool:
        """Remove one of ``owner_id``'s datasets: HDFS file (best-effort) and DB row.

        Returns False if the dataset does not exist or belongs to another
        owner. HDFS removal failures are logged but do not block deleting the
        record, so a dataset whose bytes are already gone can still be cleared
        from the list.
        """
        dataset = await self.get_dataset(dataset_id, owner_id)
        if dataset is None:
            return False
        try:
            await self._hdfs.delete_file(dataset.hdfs_path)
        except Exception as e:  # best-effort; the DB row is the source of truth for the UI
            logger.warning("dataset_hdfs_delete_failed", dataset_id=str(dataset_id), error=str(e))
        await self._db.delete(dataset)
        await self._db.commit()
        logger.info("dataset_deleted", dataset_id=str(dataset_id))
        return True

    async def list_datasets(
        self, owner_id: str, skip: int = 0, limit: int = 100
    ) -> tuple[list[Dataset], int]:
        """List ``owner_id``'s datasets, newest first, with their total count."""
        owned = Dataset.created_by == owner_id
        total = await self._db.scalar(
            select(func.count()).select_from(Dataset).where(owned)
        )
        if total is None:
            total = 0

        result = await self._db.execute(
            select(Dataset).where(owned).order_by(Dataset.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all()), total
