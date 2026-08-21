"""分享令牌管理：生成带有效期的专属 M3U 链接，支持吊销。"""

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path

log = logging.getLogger("migu-m3u")


class ShareManager:
    def __init__(self, file: str) -> None:
        self.file = file
        self.tokens: dict[str, dict] = {}
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        try:
            p = Path(self.file)
            if p.exists():
                self.tokens = json.loads(p.read_text(encoding="utf-8"))
                log.info("已加载分享令牌 %d 个", len(self.tokens))
        except Exception as e:
            log.warning("读取分享令牌文件失败: %s", e)

    def _save(self) -> None:
        try:
            Path(self.file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.file).write_text(
                json.dumps(self.tokens, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:
            log.warning("保存分享令牌文件失败: %s", e)

    # ---- 管理 ----
    def create(self, note: str, days: int) -> dict:
        token = secrets.token_urlsafe(24)
        now = time.time()
        expires_at = None if days <= 0 else now + days * 86400
        self.tokens[token] = {
            "note": (note or "").strip(),
            "created_at": now,
            "expires_at": expires_at,
            "revoked": False,
        }
        self._save()
        return self._view(token)

    def revoke(self, token: str) -> bool:
        if token not in self.tokens:
            return False
        self.tokens[token]["revoked"] = True
        self._save()
        return True

    def delete(self, token: str) -> bool:
        if token not in self.tokens:
            return False
        del self.tokens[token]
        self._save()
        return True

    def check(self, token: str) -> bool:
        """令牌是否有效（存在、未吊销、未过期）。"""
        if token not in self.tokens:
            return False
        t = self.tokens[token]
        if t.get("revoked"):
            return False
        exp = t.get("expires_at")
        if exp is not None and time.time() > exp:
            return False
        return True

    def _view(self, token: str) -> dict:
        t = self.tokens[token]
        exp = t.get("expires_at")
        now = time.time()
        return {
            "token": token,
            "note": t.get("note", ""),
            "created_at": t.get("created_at"),
            "expires_at": exp,
            "days_left": None if exp is None else max(0, int((exp - now) / 86400) + (1 if exp - now > 0 else 0)),
            "revoked": bool(t.get("revoked")),
            "permanent": exp is None,
        }

    def list(self) -> list[dict]:
        return sorted(
            [self._view(t) for t in self.tokens],
            key=lambda v: (v["revoked"], v.get("created_at") or 0),
            reverse=True,
        )
