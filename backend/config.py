import os
import json
from pathlib import Path


def as_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self):
        self.host = os.getenv("APP_HOST", "127.0.0.1")
        self.port = int(os.getenv("APP_PORT", "8080"))
        self.demo_mode = as_bool("DEMO_MODE", False)
        self.controller_url = os.getenv("MIHOMO_CONTROLLER_URL", "http://127.0.0.1:9090").rstrip("/")
        self.mihomo_secret = os.getenv("MIHOMO_SECRET", "")
        self.delay_url = os.getenv("MIHOMO_DELAY_URL", "http://www.gstatic.com/generate_204")
        self.delay_timeout_ms = int(os.getenv("MIHOMO_DELAY_TIMEOUT_MS", "3000"))
        self.delay_cache_ttl_ms = int(os.getenv("MIHOMO_DELAY_CACHE_TTL_MS", "5000"))
        self.delay_workers = min(32, max(1, int(os.getenv("MIHOMO_DELAY_WORKERS", "8"))))
        self.allow_config_write = as_bool("ALLOW_CONFIG_WRITE", False)
        self.allow_profile_activate = as_bool("ALLOW_PROFILE_ACTIVATE", False)
        self.allow_source_ip_routes = as_bool("ALLOW_SOURCE_IP_ROUTES", False)
        self.config_dir = Path(os.getenv("CONFIG_DIR", "data/config")).resolve()
        self.profiles_dir = Path(os.getenv("PROFILES_DIR", str(self.config_dir / "profiles"))).resolve()
        self.mapping_file = Path(os.getenv("IP_MAPPING_FILE", str(self.config_dir / "ip-mappings.json"))).resolve()
        configured_mihomo_path = os.getenv("MIHOMO_CONFIG_PATH", "").strip()
        self.mihomo_config_path = Path(configured_mihomo_path).resolve() if configured_mihomo_path else None
        config_root = self.config_dir
        for candidate in (self.profiles_dir, self.mapping_file):
            if candidate != config_root and config_root not in candidate.parents:
                raise ValueError("PROFILES_DIR and IP_MAPPING_FILE must stay inside CONFIG_DIR")
        try:
            self.region_map = json.loads(os.getenv("REGION_MAP_JSON", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("REGION_MAP_JSON must be valid JSON") from exc
        if not isinstance(self.region_map, dict):
            raise ValueError("REGION_MAP_JSON must be an object")
        self.max_body_bytes = int(os.getenv("MAX_BODY_BYTES", "65536"))
