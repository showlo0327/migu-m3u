"""咪咕视频接口客户端：频道列表获取 + 免登录播放地址解析 + ddCalcu 加密。"""

from __future__ import annotations

import datetime
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("migu-m3u")

# 安卓 720p 免登录签名参数（来自公开逆向资料，实测有效）
APP_VERSION = "2600034600"
APP_VERSION_SHORT = APP_VERSION[:8]
CLIENT_CHANNEL_ID = "2600034600-99000-201600010010028"
SIGN_SECRET = "2cac4f2c6c3346a5b34e085725ef7e33migu"
PLAY_URL_BASE = "https://play.miguvideo.com/playurl/v1/play/playurl"
CHANNEL_LIST_BASE = "https://program-sc.miguvideo.com/live/v2/tv-data/"
TOP_CATEGORY_ID = "1ff892f2b5ab4a79be6e25b69d2f5d05"

# 已登录（网页端扫码登录）使用的安卓签名参数
LOGIN_APP_VERSION = "26000370"
LOGIN_CLIENT_CHANNEL_ID = "2600037000-99000-200300220100002"
LOGIN_SIGN_SECRET = "3ce941cc3cbc40528bfd1c64f9fdf6c0migu0123"
LOGIN_SALT = "1230024"

UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-G9910) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/89.0.4343.0 Mobile Safari/537.36"
)


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def build_playurl_request(pid: str, rate_type: int = 3, h265: bool = False) -> tuple[str, dict[str, str]]:
    """构造 playurl v1 请求 URL 与请求头（免登录 720p 方案）。"""
    timestamp = str(int(time.time() * 1000))
    salt = f"{random.randint(0, 999999):06d}25"
    md5 = md5_hex(timestamp + pid + APP_VERSION_SHORT)
    sign = md5_hex(md5 + SIGN_SECRET + salt[:4])

    params = (
        f"?sign={sign}&rateType={rate_type}&contId={pid}&timestamp={timestamp}&salt={salt}"
        "&flvEnable=true&super4k=true"
    )
    if h265:
        params += "&h265N=true"
    params += "&4kvivid=true&2Kvivid=true&vivid=2"

    headers = {
        "User-Agent": UA,
        "AppVersion": APP_VERSION,
        "TerminalId": "android",
        "X-UP-CLIENT-CHANNEL-ID": CLIENT_CHANNEL_ID,
        "ClientId": md5_hex(str(int(time.time() * 1000))),
        "appCode": "miguvideo_default_android",
    }
    return PLAY_URL_BASE + params, headers


def ddcalcu_720p(video_url: str, pid: str) -> str:
    """旧版 720p ddCalcu 加密（纯字符串算法，实测有效）。"""
    pu_data = video_url.split("&puData=")[1]
    keys = "cdabyzwxkl"
    day_key = keys[int(str(datetime.date.today().day)[0])]
    pid_key = keys[int(pid[6])]
    out: list[str] = []
    for i in range(len(pu_data) // 2):
        out.append(pu_data[len(pu_data) - i - 1])
        out.append(pu_data[i])
        if i == 1:
            out.append("v")
        elif i == 2:
            out.append(day_key)
        elif i == 3:
            out.append(pid_key)
        elif i == 4:
            out.append("a")
    return f"{video_url}&ddCalcu={''.join(out)}&sv=10004&ct=android"


def ddcalcu_login(video_url: str, pid: str, user_id: str, rate_type: int) -> str:
    """已登录模式 ddCalcu（安卓，字符随 userId 变化）。"""
    pu_data = video_url.split("&puData=")[1]
    keys = "cdabyzwxkl"
    words = ["v", "a", "0", "a"]
    if user_id:
        if len(user_id) > 7:
            words[0] = keys[int(user_id[7])]
        if rate_type == 2:
            words[0] = "v"
        if 3 < len(user_id) <= 8:
            words[0] = "e"
    day_key = keys[int(str(datetime.date.today().day)[0])]
    pid_key = keys[int(pid[6])]
    out: list[str] = []
    for i in range(len(pu_data) // 2):
        out.append(pu_data[len(pu_data) - i - 1])
        out.append(pu_data[i])
        if i == 1:
            out.append(words[0])
        elif i == 2:
            out.append(day_key)
        elif i == 3:
            out.append(pid_key)
        elif i == 4:
            out.append(words[3])
    return f"{video_url}&ddCalcu={''.join(out)}&sv=10004&ct=android"


class MiguClient:
    def __init__(self, timeout: float = 25.0, max_connections: int = 10) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
            follow_redirects=True,
            headers={"User-Agent": UA},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_categories(self) -> list[dict]:
        """获取频道分类（央视 / 卫视 / 地方 / ...）。"""
        data = (await self._client.get(CHANNEL_LIST_BASE + TOP_CATEGORY_ID)).json()
        return [c for c in (data.get("body") or {}).get("liveList", []) if c.get("vomsID")]

    async def fetch_channels(self, voms_id: str) -> list[dict]:
        """获取某个分类下的频道列表。"""
        data = (await self._client.get(CHANNEL_LIST_BASE + voms_id)).json()
        result = []
        for ch in (data.get("body") or {}).get("dataList", []):
            pics = ch.get("pics") or {}
            result.append(
                {
                    "name": ch.get("name"),
                    "pID": str(ch.get("pID") or ""),
                    "logo": pics.get("highResolutionH") or "",
                }
            )
        return [r for r in result if r["name"] and r["pID"]]

    async def fetch_programs(self, pID: str, date: str) -> list[dict]:
        """获取某频道某天的节目单（EPG），返回节目起止时间与回看 contId。"""
        url = f"https://program-sc.miguvideo.com/live/v2/tv-programs-data/{pID}/{date}"
        data = (await self._client.get(url)).json()
        progs: list[dict] = []
        for day in (data.get("body") or {}).get("program", []):
            for p in day.get("content", []):
                progs.append(
                    {
                        "name": p.get("contName") or "",
                        "start": p.get("startTime") or 0,
                        "end": p.get("endTime") or 0,
                        "contId": p.get("contId") or "",
                        "lookback": str(p.get("isLookBack") or "0") == "1",
                    }
                )
        return [p for p in progs if p["name"] and p["start"] and p["end"]]

    async def resolve_playurl(self, pid: str, rate_type: int = 3, h265: bool = False) -> str:
        """解析频道 pID 的免登录播放地址（已带 ddCalcu 加密参数）。"""
        url, headers = build_playurl_request(pid, rate_type=rate_type, h265=h265)
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        obj = resp.json()
        body = obj.get("body") or {}
        if obj.get("rid") != "SUCCESS":
            raise RuntimeError(f"playurl 返回异常: rid={obj.get('rid')} msg={obj.get('msg') or obj.get('message')}")
        video_url = (body.get("urlInfo") or {}).get("url")
        if not video_url:
            raise RuntimeError("playurl 未返回播放地址")
        real_pid = str((body.get("content") or {}).get("contId") or pid)
        return ddcalcu_720p(video_url, real_pid)

    async def resolve_playurl_login(
        self,
        pid: str,
        rate_type: int = 7,
        h265: bool = True,
        user_id: str = "",
        user_token: str = "",
    ) -> str:
        """已登录模式解析播放地址；会员等级不足时自动降级（7->4->3）。"""
        attempts = [rate_type, 4, 3] if rate_type > 3 else [rate_type]
        last_err = ""
        for rt in attempts:
            timestamp = str(int(time.time() * 1000))
            md5 = md5_hex(timestamp + pid + LOGIN_APP_VERSION)
            sign = md5_hex(md5 + LOGIN_SIGN_SECRET)
            params = (
                f"?sign={sign}&rateType={rt}&contId={pid}&timestamp={timestamp}&salt={LOGIN_SALT}"
                "&flvEnable=true&super4k=true"
            )
            if h265:
                params += "&h265N=true"
            params += "&4kvivid=true&2Kvivid=true&vivid=2"
            headers = {
                "User-Agent": UA,
                "AppVersion": "2600037000",
                "TerminalId": "android",
                "X-UP-CLIENT-CHANNEL-ID": LOGIN_CLIENT_CHANNEL_ID,
                "ClientId": md5_hex(str(int(time.time() * 1000))),
                "appCode": "miguvideo_default_android",
                "UserId": user_id,
                "UserToken": user_token,
            }
            resp = await self._client.get(PLAY_URL_BASE + params, headers=headers)
            resp.raise_for_status()
            obj = resp.json()
            rid = obj.get("rid")
            if rid == "TIPS_NEED_MEMBER":
                last_err = f"会员等级不足（{rt}），尝试降级"
                continue
            body = obj.get("body") or {}
            if rid != "SUCCESS":
                raise RuntimeError(f"playurl 返回异常: rid={rid} msg={obj.get('msg') or obj.get('message')}")
            video_url = (body.get("urlInfo") or {}).get("url")
            if not video_url:
                raise RuntimeError("playurl 未返回播放地址")
            real_pid = str((body.get("content") or {}).get("contId") or pid)
            return ddcalcu_login(video_url, real_pid, user_id, rt)
        raise RuntimeError(last_err or "播放地址解析失败")


@dataclass
class ChannelState:
    name: str
    pID: str
    group: str
    logo: str = ""
    url: str | None = None
    updated_at: float = 0.0
    error: str = ""
    url_expires: float = 0.0
