"""集中配置：端口、依赖地址、数据目录。全部可用环境变量覆盖，便于测试与部署。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))

WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))
TRANSFORMER_PORT = int(os.environ.get("TRANSFORMER_PORT", "8001"))
STORAGE_PORT = int(os.environ.get("STORAGE_PORT", "8002"))
COMPAT_PORT = int(os.environ.get("COMPAT_PORT", "8003"))
LEGACY_PORT = int(os.environ.get("LEGACY_PORT", "8004"))

TRANSFORMER_URL = os.environ.get("TRANSFORMER_URL", f"http://127.0.0.1:{TRANSFORMER_PORT}")
STORAGE_URL = os.environ.get("STORAGE_URL", f"http://127.0.0.1:{STORAGE_PORT}")
COMPAT_URL = os.environ.get("COMPAT_URL", f"http://127.0.0.1:{COMPAT_PORT}")
LEGACY_URL = os.environ.get("LEGACY_URL", f"http://127.0.0.1:{LEGACY_PORT}")

NEW_DB = os.path.join(DATA_DIR, "new_store.sqlite3")
LEGACY_DB = os.path.join(DATA_DIR, "legacy_store.sqlite3")
INGEST_LOG = os.path.join(DATA_DIR, "ingest_log.jsonl")
