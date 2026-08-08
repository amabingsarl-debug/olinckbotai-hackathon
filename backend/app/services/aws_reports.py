from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class AWSReportStore:
    """Stores agent-context reports in S3 when configured, local JSON otherwise."""

    def __init__(self, storage_path: Path | None = None) -> None:
        settings = get_settings()
        self.bucket = settings.aws_s3_reports_bucket
        self.region = settings.aws_region
        self.storage_path = storage_path or Path(__file__).resolve().parents[1] / "data" / "agent_context_reports"

    def save_report(self, report: dict[str, Any]) -> dict[str, Any]:
        key = f"agent-context/{datetime.now(UTC).strftime('%Y/%m/%d')}/{report.get('memory_id', 'demo')}.json"
        if not self.bucket:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            target = self.storage_path / key.replace("/", "_")
            target.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            return {"saved": True, "mode": "local_demo", "path": str(target)}
        try:
            import boto3

            client = boto3.client("s3", region_name=self.region)
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=json.dumps(report, indent=2, default=str).encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )
            return {"saved": True, "mode": "s3", "bucket": self.bucket, "key": key}
        except Exception as exc:  # pragma: no cover - depends on AWS runtime credentials
            return {"saved": False, "mode": "s3", "error": str(exc), "bucket": self.bucket, "key": key}
