"""Đọc/ghi Firebase Realtime Database qua REST API."""
import logging

import requests

import config

log = logging.getLogger("firebase")


class Firebase:
    def __init__(self, base_url=config.FIREBASE_DB_URL, auth=config.FIREBASE_AUTH):
        self.base_url = base_url
        self.auth = auth
        self.session = requests.Session()

    def _url(self, path):
        path = path.strip("/")
        return f"{self.base_url}/{path}.json" if path else f"{self.base_url}/.json"

    def _params(self):
        return {"auth": self.auth} if self.auth else {}

    def get(self, path="/"):
        """Trả dict dữ liệu, hoặc None nếu lỗi mạng."""
        try:
            res = self.session.get(self._url(path), params=self._params(), timeout=10)
            res.raise_for_status()
            return res.json() or {}
        except (requests.RequestException, ValueError) as exc:
            log.warning("Đọc Firebase lỗi: %s", exc)
            return None

    def update(self, values, path="/"):
        """PATCH — chỉ ghi các key được truyền vào."""
        if config.DRY_RUN:
            log.info("[DRY_RUN] Bỏ qua ghi Firebase %s: %s", path, values)
            return True
        try:
            res = self.session.patch(
                self._url(path), params=self._params(), json=values, timeout=10
            )
            res.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.error("Ghi Firebase lỗi: %s", exc)
            return False
