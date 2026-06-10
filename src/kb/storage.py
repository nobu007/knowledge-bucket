"""S3-compatible raw data storage for Knowledge Bucket.

Supports uploading raw content (HTML, PDF bytes, etc.) to S3/R2 or a local
directory under .kb/raw/. The ``raw_ref`` URI is recorded in document front
matter so ``kb raw <doc_id>`` can retrieve it later.
"""

import os


class S3Storage:
    """S3 / Cloudflare R2 storage backend using boto3."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        bucket: str = "",
        region: str = "auto",
        prefix: str = "raw/",
    ):
        self.endpoint_url = endpoint_url
        self.bucket = bucket
        self.region = region
        self.prefix = prefix

    def _client(self):
        import boto3

        kwargs: dict = {"region_name": self.region}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        return boto3.client("s3", **kwargs)

    def upload(self, key: str, data: bytes) -> str:
        s3_key = f"{self.prefix}{key}"
        self._client().put_object(Bucket=self.bucket, Key=s3_key, Body=data)
        return f"s3://{self.bucket}/{s3_key}"

    def download(self, ref: str) -> bytes:
        if not ref.startswith("s3://"):
            raise ValueError(f"Invalid S3 reference: {ref}")
        _, _, path = ref.partition("//")
        bucket, _, key = path.partition("/")
        resp = self._client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()


class LocalStorage:
    """Local filesystem storage backend (.kb/raw/)."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def upload(self, key: str, data: bytes) -> str:
        path = os.path.join(self.base_dir, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return f"local://{key}"

    def download(self, ref: str) -> bytes:
        if not ref.startswith("local://"):
            raise ValueError(f"Invalid local reference: {ref}")
        key = ref[len("local://"):]
        path = os.path.join(self.base_dir, key)
        with open(path, "rb") as f:
            return f.read()


def _load_storage_config(root: str) -> dict:
    import yaml

    config_path = os.path.join(root, "config", "kb.yml")
    if not os.path.exists(config_path):
        return {}
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    return config.get("storage") or {}


def get_storage_backend(root: str):
    """Return configured storage backend, or None if unconfigured."""
    cfg = _load_storage_config(root)
    if not cfg:
        return None

    backend = cfg.get("backend", "local")
    if backend == "s3":
        return S3Storage(
            endpoint_url=cfg.get("endpoint_url"),
            bucket=cfg.get("bucket", ""),
            region=cfg.get("region", "auto"),
            prefix=cfg.get("prefix", "raw/"),
        )
    return LocalStorage(os.path.join(root, ".kb", "raw"))


def save_raw(root: str, doc_id: str, data: bytes) -> str:
    """Upload raw data and return the reference URI.

    Falls back to local storage if no backend is configured.
    """
    backend = get_storage_backend(root)
    if backend is None:
        backend = LocalStorage(os.path.join(root, ".kb", "raw"))
    return backend.upload(doc_id, data)


def get_raw(root: str, raw_ref: str) -> bytes:
    """Download raw data by reference URI."""
    if raw_ref.startswith("local://"):
        backend = LocalStorage(os.path.join(root, ".kb", "raw"))
        return backend.download(raw_ref)
    if raw_ref.startswith("s3://"):
        backend = get_storage_backend(root)
        if backend is None:
            raise RuntimeError("S3 reference but no storage backend configured")
        return backend.download(raw_ref)
    raise ValueError(f"Unknown raw reference scheme: {raw_ref}")
