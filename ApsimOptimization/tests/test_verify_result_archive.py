import hashlib
import io
import json
import tarfile

from scripts.verify_result_archive import verify


def test_verifies_manifest_hash_and_size(tmp_path):
    payload = b"a,b\n1,2\n"
    manifest = json.dumps({"files": [{
        "path": "result.csv", "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }]}).encode()
    path = tmp_path / "result.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, value in (("manifest.json", manifest), ("result.csv", payload)):
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    result = verify(path)
    assert result["verified"] is True
    assert result["declared_files"] == 1


def test_reports_changed_payload(tmp_path):
    payload = b"changed"
    manifest = json.dumps({"files": [{
        "path": "result.csv", "size": len(payload), "sha256": "0" * 64,
    }]}).encode()
    path = tmp_path / "result.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, value in (("manifest.json", manifest), ("result.csv", payload)):
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    result = verify(path)
    assert result["verified"] is False
    assert result["errors"] == ["sha256:result.csv"]
