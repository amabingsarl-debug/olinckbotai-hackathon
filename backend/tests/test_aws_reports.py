from __future__ import annotations

import json
from pathlib import Path

from app.core.config import get_settings
from app.services.aws_reports import AWSReportStore


def test_aws_report_store_writes_local_demo_artifact(monkeypatch, tmp_path: Path) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AWS_S3_REPORTS_BUCKET", "")
    store = AWSReportStore(storage_path=tmp_path)

    result = store.save_report({"memory_id": "memory-demo", "decision": "wait"})

    assert result["saved"] is True
    assert result["mode"] == "local_demo"
    target = Path(result["path"])
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["decision"] == "wait"


def test_aws_report_store_uses_s3_when_bucket_configured(monkeypatch) -> None:
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    class FakeBoto3:
        @staticmethod
        def client(service: str, region_name: str):
            captured["service"] = service
            captured["region_name"] = region_name
            return FakeS3()

    monkeypatch.setenv("AWS_S3_REPORTS_BUCKET", "olinckbotai-agent-reports-demo")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setitem(__import__("sys").modules, "boto3", FakeBoto3)

    result = AWSReportStore().save_report({"memory_id": "abc", "decision": "wait"})

    assert result["saved"] is True
    assert result["mode"] == "s3"
    assert captured["service"] == "s3"
    assert captured["Bucket"] == "olinckbotai-agent-reports-demo"
    assert captured["ServerSideEncryption"] == "AES256"
