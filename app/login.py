"""咪咕网页版扫码登录：生成二维码、轮询状态、跟随跳转取 cookie、解析 userId/token。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from urllib.parse import unquote

import httpx

from .migu_client import UA

log = logging.getLogger("migu-m3u")

SOURCE_ID = "203009"
CREATE_URL = "https://passport.migu.cn/api/qrcWeb/qrcLogin"
QUERY_URL = "https://passport.migu.cn/api/qrcWeb/qrcquery"

# 扫码轮询状态码（来自 passport 前端 JS）
STATUS_EXPIRED = 4072  # 二维码已失效
STATUS_POLLING = 4073  # 已扫码，等待手机确认
STATUS_NO_SCAN = 4074  # 未扫码
STATUS_SUCCESS = 2000


class LoginManager:
    def __init__(self, file: str) -> None:
        self.file = file
        self.user_id = ""
        self.user_token = ""
        self.pass_id = ""
        self.login_at = 0.0
        self.rate_type = 7  # 7=原画 4=蓝光 3=高清 9=4K
        self.h265 = True
        self.pending_session = ""
        self.last_poll_raw: dict | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(25.0),
            follow_redirects=False,
            headers={"User-Agent": UA, "Referer": "https://passport.migu.cn/login?sourceid=203009"},
        )
        self._load()

    async def close(self) -> None:
        await self._client.aclose()

    def is_logged_in(self) -> bool:
        return bool(self.user_id and self.user_token)

    def info(self) -> dict:
        return {
            "logged_in": self.is_logged_in(),
            "user_id": self.user_id if self.is_logged_in() else "",
            "login_at": self.login_at if self.is_logged_in() else None,
            "rate_type": self.rate_type,
            "h265": self.h265,
        }

    # ---- 持久化 ----
    def _load(self) -> None:
        try:
            p = Path(self.file)
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                self.user_id = str(d.get("user_id") or "")
                self.user_token = str(d.get("user_token") or "")
                self.pass_id = str(d.get("pass_id") or "")
                self.login_at = float(d.get("login_at") or 0)
                self.rate_type = int(d.get("rate_type") or 7)
                self.h265 = bool(d.get("h265", True))
                if self.is_logged_in():
                    log.info("已从本地文件恢复登录信息: userId=%s", self.user_id)
        except Exception as e:
            log.warning("读取登录文件失败: %s", e)

    def save(self) -> None:
        try:
            Path(self.file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.file).write_text(
                json.dumps(
                    {
                        "user_id": self.user_id,
                        "user_token": self.user_token,
                        "pass_id": self.pass_id,
                        "login_at": self.login_at,
                        "rate_type": self.rate_type,
                        "h265": self.h265,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("保存登录文件失败: %s", e)

    def logout(self) -> None:
        self.user_id = ""
        self.user_token = ""
        self.pass_id = ""
        self.login_at = 0.0
        self.save()
        log.info("已退出登录")

    def set_manual(self, user_id: str, user_token: str) -> None:
        self.user_id = user_id.strip()
        self.user_token = user_token.strip()
        self.pass_id = ""
        self.login_at = time.time()
        self.save()
        log.info("手动填写登录信息: userId=%s", self.user_id)

    # ---- 扫码登录 ----
    async def create_qrcode(self) -> dict:
        r = await self._client.post(CREATE_URL, data={"isAsync": "true", "sourceid": SOURCE_ID})
        r.raise_for_status()
        result = r.json()["result"]
        self.pending_session = str(result["qrc_sessionid"])
        return {
            "qrcUrl": result["qrcUrl"],
            "session_id": self.pending_session,
            "expires_in": 120,
        }

    @staticmethod
    def _cookie_value(raw_cookie: str, name: str) -> str:
        """从原始 Set-Cookie 字符串中取出指定 cookie 的值。"""
        for part in raw_cookie.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k.strip().lower() == name.lower():
                    return unquote(v.strip())
        return ""

    def _parse_userinfo_cookie(self, set_cookie_headers: list[str]) -> tuple[str, str]:
        """UserInfo cookie 格式: userId|nlps...token"""
        for raw in set_cookie_headers:
            val = self._cookie_value(raw, "UserInfo")
            if val and "|" in val:
                uid, tok = val.split("|", 1)
                if uid.strip() and tok.strip():
                    return uid.strip(), tok.strip()
        return "", ""

    async def check_status(self, session_id: str) -> dict:
        try:
            r = await self._client.post(
                QUERY_URL,
                data={"isAsync": "true", "sourceid": SOURCE_ID, "qrc_sessionid": session_id},
            )
            obj = r.json()
        except Exception as e:
            return {"state": "unknown", "message": f"轮询请求失败: {e}", "raw": None}

        status = obj.get("status")
        result = obj.get("result") or {}
        self.last_poll_raw = obj
        log.info("qrcquery status=%s result_keys=%s", status, list(result.keys())[:10])

        if status == STATUS_NO_SCAN:
            return {"state": "waiting", "message": "等待扫码"}
        if status == STATUS_POLLING:
            return {"state": "confirmed", "message": "已扫码，请在手机上点击确认登录", "raw": obj}
        if status == STATUS_EXPIRED:
            return {"state": "expired", "message": "二维码已失效，请刷新重试", "raw": obj}

        # 成功：优先从 result 直接取，其次跟随 redirectURL 拿 cookie
        if status == STATUS_SUCCESS or result.get("token") or result.get("userToken"):
            uid = str(result.get("userId") or "")
            tok = str(result.get("userToken") or result.get("token") or "")
            chain_info: dict = {}
            if not (uid and tok.startswith("nlps")):
                uid, tok, chain_info = await self._complete_with_redirect(r, result)
            if uid and tok:
                self.user_id = uid
                self.user_token = tok
                self.pass_id = str(result.get("passId") or "")
                self.login_at = time.time()
                self.save()
                log.info("扫码登录成功: userId=%s", self.user_id)
                return {"state": "success", "message": "登录成功", "raw": result}
            return {
                "state": "need_parse",
                "message": "扫码成功但未解析出完整登录信息，请把下方原始返回复制发给我",
                "raw": {
                    "result": result,
                    "cookies": list(r.headers.get_list("set-cookie")),
                    "chain": chain_info,
                },
            }
        return {"state": "unknown", "message": f"未知状态码 {status}", "raw": obj}

    async def _complete_with_redirect(
        self, qr_resp: httpx.Response, result: dict
    ) -> tuple[str, str, dict]:
        """跟随 passport 返回的 redirectURL?token=... 取登录 cookie，解析 UserInfo。"""
        redirect_url = str(result.get("redirectURL") or "")
        token = str(result.get("token") or "")
        if not redirect_url or not token:
            return "", "", {"error": "缺少 redirectURL 或 token"}
        sep = "&" if "?" in redirect_url else "?"
        try:
            cookies = {k: v for k, v in qr_resp.cookies.items()}
            r2 = await self._client.get(
                redirect_url + sep + "token=" + token,
                headers={"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())},
                follow_redirects=True,
            )
        except Exception as e:
            log.warning("跟随登录跳转失败: %s", e)
            return "", "", {"error": str(e)}

        hops = r2.history + [r2]
        set_cookie_headers = qr_resp.headers.get_list("set-cookie")
        for resp in hops:
            set_cookie_headers += resp.headers.get_list("set-cookie")
        chain = [
            {
                "status": resp.status_code,
                "url": str(resp.url)[:160],
                "set_cookie": [c.split(";")[0] for c in resp.headers.get_list("set-cookie")],
            }
            for resp in hops
        ]
        log.info("登录回调链: %s", chain)

        uid, tok = self._parse_userinfo_cookie(set_cookie_headers)
        if uid and tok:
            return uid, tok, {"chain": chain, "cookies": set_cookie_headers}
        # 兜底：从其他 cookie 字段里找
        for name in ("userId", "userToken", "accessToken", "MIGU_UID", "MIGU_TOKEN"):
            for raw in set_cookie_headers:
                v = self._cookie_value(raw, name)
                if not v:
                    continue
                if name.lower() in ("userid", "migu_uid") and not uid:
                    uid = v
                elif name.lower() in ("usertoken", "accesstoken", "migu_token") and not tok:
                    tok = v
        return uid, tok, {"chain": chain, "cookies": set_cookie_headers}
