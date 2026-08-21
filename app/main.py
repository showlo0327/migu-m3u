"""migu-m3u 服务：动态 M3U 列表 + 可视化管理面板 + 咪咕 App 扫码登录。"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from .login import LoginManager
from .migu_client import ChannelState, MiguClient
from .share import ShareManager

log = logging.getLogger("migu-m3u")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [migu-m3u] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str) -> list[str]:
    v = os.getenv(name, default)
    return [s.strip() for s in v.split(",") if s.strip()]


SETTINGS = {
    "host": os.getenv("MIGU_HOST", "0.0.0.0"),
    "port": int(os.getenv("MIGU_PORT", "8090")),
    "base_url": os.getenv("MIGU_BASE_URL", "").rstrip("/"),
    "rate_type": int(os.getenv("MIGU_RATE_TYPE", "3")),
    "h265": env_bool("MIGU_H265", False),
    "refresh_minutes": int(os.getenv("MIGU_REFRESH_MINUTES", "60")),
    "url_cache_minutes": int(os.getenv("MIGU_URL_CACHE_MINUTES", "180")),
    "channel_refresh_hours": int(os.getenv("MIGU_CHANNEL_REFRESH_HOURS", "6")),
    "categories": env_list(
        "MIGU_CATEGORIES",
        "央视,卫视,地方,新闻,影视,教育,综艺,少儿,纪实",
    ),
    "max_workers": int(os.getenv("MIGU_MAX_WORKERS", "6")),
    "channels_file": os.getenv("MIGU_CHANNELS_FILE", "/data/channels.json"),
    "login_file": os.getenv("MIGU_LOGIN_FILE", "/data/login.json"),
    "epg_back_days": int(os.getenv("MIGU_EPG_BACK_DAYS", "2")),
    "epg_forward_days": int(os.getenv("MIGU_EPG_FORWARD_DAYS", "1")),
    "epg_refresh_hours": int(os.getenv("MIGU_EPG_REFRESH_HOURS", "6")),
    "epg_file": os.getenv("MIGU_EPG_FILE", "/data/epg.json"),
    "tokens_file": os.getenv("MIGU_TOKENS_FILE", "/data/tokens.json"),
    "admin_password": os.getenv("MIGU_ADMIN_PASSWORD", "admin"),
}


class ServiceState:
    def __init__(self) -> None:
        self.migu = MiguClient()
        self.login = LoginManager(SETTINGS["login_file"])
        self.share = ShareManager(SETTINGS["tokens_file"])
        self.channels: list[ChannelState] = []
        self.lock = asyncio.Lock()
        self.last_refresh: float = 0.0
        self.last_channel_refresh: float = 0.0
        self.last_error: str = ""
        self.refreshing = False
        self.epg: dict[str, list[dict]] = {}
        self.epg_last_refresh: float = 0.0
        self.epg_refreshing = False
        self._xml_cache = ""
        self._xml_cache_ts = 0.0
        self._load_epg()

    async def close(self) -> None:
        await self.migu.close()
        await self.login.close()

    # ---- 频道列表 ----
    async def load_channels(self, force: bool = False) -> None:
        now = time.time()
        if not force and self.channels and now - self.last_channel_refresh < SETTINGS["channel_refresh_hours"] * 3600:
            return
        try:
            cats = await self.migu.fetch_categories()
            want = SETTINGS["categories"]
            picked = [c for c in cats if c.get("name") in want]
            all_ch = []
            for cat in picked:
                try:
                    chs = await self.migu.fetch_channels(cat["vomsID"])
                except Exception as e:
                    log.warning("分类 %s 获取失败: %s", cat.get("name"), e)
                    continue
                for ch in chs:
                    all_ch.append(
                        ChannelState(
                            name=ch["name"],
                            pID=ch["pID"],
                            group=cat.get("name") or "其他",
                            logo=ch.get("logo") or "",
                        )
                    )
            if all_ch:
                seen: set[str] = set()
                unique: list[ChannelState] = []
                for ch in all_ch:
                    if ch.name in seen:
                        continue
                    seen.add(ch.name)
                    unique.append(ch)
                async with self.lock:
                    self.channels = unique
                    self.last_channel_refresh = now
                self._save_channels()
                log.info("频道列表更新完成: %d 个频道（去重后 %d）", len(all_ch), len(unique))
        except Exception as e:
            self.last_error = f"频道列表获取失败: {e}"
            log.error(self.last_error)
            if not self.channels:
                self._load_channels_file()

    def _save_channels(self) -> None:
        try:
            Path(SETTINGS["channels_file"]).parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"name": c.name, "pID": c.pID, "group": c.group, "logo": c.logo}
                for c in self.channels
            ]
            Path(SETTINGS["channels_file"]).write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:
            log.warning("保存频道文件失败: %s", e)

    def _load_channels_file(self) -> None:
        p = Path(SETTINGS["channels_file"])
        if not p.exists():
            p = Path(__file__).resolve().parent.parent / "channels.json"
        if not p.exists():
            log.error("没有可用频道数据")
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and data and "vomsID" in data[0]:
                self.channels = [
                    ChannelState(name=ch["name"], pID=ch["pID"], group=cat["name"], logo=ch.get("logo", ""))
                    for cat in data
                    for ch in cat.get("channels", [])
                ]
            else:
                self.channels = [
                    ChannelState(name=c["name"], pID=c["pID"], group=c.get("group", "其他"), logo=c.get("logo", ""))
                    for c in data
                ]
            log.info("从本地文件加载 %d 个频道", len(self.channels))
        except Exception as e:
            log.error("读取频道文件失败: %s", e)

    # ---- 播放地址 ----
    async def resolve_one(self, pid: str) -> str:
        if self.login.is_logged_in():
            return await self.migu.resolve_playurl_login(
                pid, self.login.rate_type, self.login.h265, self.login.user_id, self.login.user_token
            )
        return await self.migu.resolve_playurl(pid, SETTINGS["rate_type"], SETTINGS["h265"])

    async def get_playurl(self, pid: str) -> str:
        ch = next((c for c in self.channels if c.pID == pid), None)
        now = time.time()
        if ch and ch.url and ch.url_expires > now:
            return ch.url
        url = await self.resolve_one(pid)
        if ch:
            ch.url = url
            ch.updated_at = now
            ch.error = ""
            ch.url_expires = now + SETTINGS["url_cache_minutes"] * 60
        return url

    async def refresh_urls(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        try:
            sem = asyncio.Semaphore(SETTINGS["max_workers"])

            async def resolve(ch: ChannelState) -> None:
                async with sem:
                    try:
                        ch.url = await self.resolve_one(ch.pID)
                        ch.updated_at = time.time()
                        ch.error = ""
                        ch.url_expires = time.time() + SETTINGS["url_cache_minutes"] * 60
                    except Exception as e:
                        ch.error = str(e)[:200]
                        log.warning("频道 %s(%s) 解析失败: %s", ch.name, ch.pID, e)

            tasks = [asyncio.create_task(resolve(c)) for c in self.channels]
            await asyncio.gather(*tasks)
            ok = sum(1 for c in self.channels if c.url)
            self.last_refresh = time.time()
            mode = "登录模式" if self.login.is_logged_in() else "免登录模式"
            log.info("播放地址刷新完成[%s]: 成功 %d/%d", mode, ok, len(self.channels))
        finally:
            self.refreshing = False

    # ---- 节目单（EPG）与回看 ----
    async def refresh_epg(self, force: bool = False) -> None:
        now = time.time()
        if self.epg_refreshing:
            return
        if (
            not force
            and self.epg
            and now - self.epg_last_refresh < SETTINGS["epg_refresh_hours"] * 3600
        ):
            return
        self.epg_refreshing = True
        try:
            today = datetime.date.today()
            dates = [
                (today - datetime.timedelta(days=i)).strftime("%Y%m%d")
                for i in range(SETTINGS["epg_back_days"], -1, -1)
            ] + [
                (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
                for i in range(1, SETTINGS["epg_forward_days"] + 1)
            ]
            sem = asyncio.Semaphore(SETTINGS["max_workers"])

            async def fetch_one(ch: ChannelState) -> list[dict]:
                async with sem:
                    items: list[dict] = []
                    for d in dates:
                        try:
                            items += await self.migu.fetch_programs(ch.pID, d)
                        except Exception:
                            pass
                    return items

            results = await asyncio.gather(*[fetch_one(c) for c in self.channels])
            epg = {}
            for ch, items in zip(self.channels, results):
                if items:
                    epg[ch.name] = sorted(items, key=lambda p: p["start"])
            async with self.lock:
                self.epg = epg
                self.epg_last_refresh = time.time()
            self._save_epg()
            total = sum(len(v) for v in epg.values())
            log.info("节目单更新完成: %d 个频道 %d 条节目", len(epg), total)
        except Exception as e:
            log.error("节目单更新失败: %s", e)
            if not self.epg:
                self._load_epg()
        finally:
            self.epg_refreshing = False

    def _save_epg(self) -> None:
        try:
            Path(SETTINGS["epg_file"]).parent.mkdir(parents=True, exist_ok=True)
            Path(SETTINGS["epg_file"]).write_text(
                json.dumps(self.epg, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            log.warning("保存节目单文件失败: %s", e)

    def _load_epg(self) -> None:
        try:
            p = Path(SETTINGS["epg_file"])
            if p.exists():
                self.epg = json.loads(p.read_text(encoding="utf-8"))
                log.info("从本地文件加载节目单: %d 个频道", len(self.epg))
        except Exception as e:
            log.warning("读取节目单文件失败: %s", e)

    def build_xmltv(self) -> str:
        if self._xml_cache and time.time() - self._xml_cache_ts < 300:
            return self._xml_cache
        tz = datetime.timezone(datetime.timedelta(hours=8))

        def esc(s: str) -> str:
            return (
                str(s)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        def fmt(ms: int) -> str:
            return datetime.datetime.fromtimestamp(ms / 1000, tz=tz).strftime("%Y%m%d%H%M%S") + " +0800"

        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="migu-m3u">']
        for name in self.epg:
            lines.append(f'  <channel id="{esc(name)}"><display-name>{esc(name)}</display-name></channel>')
        for name, progs in self.epg.items():
            for p in progs:
                lines.append(
                    f'  <programme start="{fmt(p["start"])}" stop="{fmt(p["end"])}" channel="{esc(name)}">'
                    f'<title>{esc(p["name"])}</title></programme>'
                )
        lines.append("</tv>")
        self._xml_cache = "\n".join(lines)
        self._xml_cache_ts = time.time()
        return self._xml_cache

    async def background_loop(self) -> None:
        while True:
            try:
                await self.load_channels()
                await self.refresh_urls()
                await self.refresh_epg()
            except Exception as e:
                log.error("刷新任务异常: %s", e)
            await asyncio.sleep(SETTINGS["refresh_minutes"] * 60)

    # ---- M3U ----
    def build_m3u(self, base_url: str, direct: bool, token: str | None = None) -> str:
        epg_url = f"{base_url}/playback.xml" if token is None else f"{base_url}/s/{token}/playback.xml"
        play_prefix = f"{base_url}/play" if token is None else f"{base_url}/s/{token}/play"
        lines = [
            f'#EXTM3U x-tvg-url="{epg_url}" '
            'catchup="append" '
            'catchup-source="?playbackbegin=${(b)yyyyMMddHHmmss}&playbackend=${(e)yyyyMMddHHmmss}" '
            'catchup-days="3"',
        ]
        for ch in self.channels:
            if not ch.url:
                continue
            attrs = (
                f'tvg-id="{ch.name}" tvg-name="{ch.name}" '
                f'tvg-logo="{ch.logo}" group-title="{ch.group}"'
            )
            lines.append(f'#EXTINF:-1 {attrs},{ch.name}')
            if direct:
                lines.append(ch.url)
            else:
                lines.append(f"{play_prefix}/{ch.pID}")
        return "\n".join(lines) + "\n"


state = ServiceState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(state.background_loop())
    yield
    task.cancel()
    await state.close()


app = FastAPI(title="migu-m3u", version="2.0.0", lifespan=lifespan)


def base_url_of(request: Request) -> str:
    if SETTINGS["base_url"]:
        return SETTINGS["base_url"]
    return str(request.base_url).rstrip("/")


def require_share(token: str) -> None:
    if not state.share.check(token):
        raise HTTPException(403, "分享链接无效或已过期")


def require_admin(request: Request) -> None:
    pwd = request.headers.get("X-Admin-Password", "")
    if not pwd or pwd != SETTINGS["admin_password"]:
        raise HTTPException(401, "管理密码错误")


@app.get("/", include_in_schema=False)
async def index():
    page = Path(__file__).resolve().parent / "static" / "index.html"
    return FileResponse(page, media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "migu-m3u"}


@app.get("/status")
async def status():
    now = time.time()
    resolved = [c for c in state.channels if c.url]
    login = state.login.info()
    return {
        "service": "migu-m3u",
        "channels": len(state.channels),
        "resolved": len(resolved),
        "groups": sorted({c.group for c in state.channels}),
        "mode": "login" if login["logged_in"] else "anonymous",
        "login": login,
        "url_cache_minutes": SETTINGS["url_cache_minutes"],
        "refresh_minutes": SETTINGS["refresh_minutes"],
        "last_refresh": state.last_refresh or None,
        "last_channel_refresh": state.last_channel_refresh or None,
        "cache_age": int(now - state.last_refresh) if state.last_refresh else None,
        "last_error": state.last_error or None,
        "epg_channels": len(state.epg),
        "epg_last_refresh": state.epg_last_refresh or None,
        "streams": [
            {"name": c.name, "pID": c.pID, "group": c.group, "ok": bool(c.url), "error": c.error or None}
            for c in state.channels
        ],
    }


@app.get("/migu.m3u", response_class=PlainTextResponse)
async def migu_m3u(request: Request):
    if not state.channels:
        raise HTTPException(503, "频道列表尚未加载")
    if not any(c.url for c in state.channels) and not state.refreshing:
        await state.refresh_urls()
    if not any(c.url for c in state.channels):
        raise HTTPException(503, "播放地址尚未解析完成，请稍后重试")
    return state.build_m3u(base_url_of(request), direct=False)


@app.get("/migu_direct.m3u", response_class=PlainTextResponse)
async def migu_direct_m3u(request: Request):
    if not state.channels:
        raise HTTPException(503, "频道列表尚未加载")
    if not any(c.url for c in state.channels) and not state.refreshing:
        await state.refresh_urls()
    if not any(c.url for c in state.channels):
        raise HTTPException(503, "播放地址尚未解析完成，请稍后重试")
    return state.build_m3u(base_url_of(request), direct=True)


@app.get("/play/{pid}")
async def play(pid: str, playbackbegin: str | None = None, playbackend: str | None = None):
    if not any(c.pID == pid for c in state.channels):
        raise HTTPException(404, f"未知频道 ID: {pid}")
    try:
        url = await state.get_playurl(pid)
    except Exception as e:
        log.error("频道 %s 解析失败: %s", pid, e)
        raise HTTPException(502, f"播放地址解析失败: {e}")
    if playbackbegin or playbackend:
        extra = []
        if playbackbegin:
            extra.append("playbackbegin=" + playbackbegin)
        if playbackend:
            extra.append("playbackend=" + playbackend)
        url += "&" + "&".join(extra)
    return RedirectResponse(url, status_code=302)


@app.get("/playback.xml")
@app.get("/epg.xml")
async def playback_xml():
    if not state.epg and not state.epg_refreshing:
        await state.refresh_epg(force=True)
    if not state.epg:
        raise HTTPException(503, "节目单尚未生成，请稍后重试")
    return Response(content=state.build_xmltv(), media_type="application/xml; charset=utf-8")


async def ensure_ready() -> None:
    if not state.channels:
        await state.load_channels(force=True)
    if not any(c.url for c in state.channels) and not state.refreshing:
        await state.refresh_urls()
    if not any(c.url for c in state.channels):
        raise HTTPException(503, "播放地址尚未解析完成，请稍后重试")


@app.get("/refresh")
async def refresh():
    asyncio.create_task(state.refresh_urls())
    asyncio.create_task(state.refresh_epg())
    return {"status": "refreshing"}


# ---------- 分享链接（带 token） ----------
@app.get("/s/{token}/migu.m3u", response_class=PlainTextResponse)
async def share_m3u(token: str, request: Request):
    require_share(token)
    await ensure_ready()
    return state.build_m3u(base_url_of(request), direct=False, token=token)


@app.get("/s/{token}/play/{pid}")
async def share_play(token: str, pid: str, playbackbegin: str | None = None, playbackend: str | None = None):
    require_share(token)
    if not any(c.pID == pid for c in state.channels):
        raise HTTPException(404, f"未知频道 ID: {pid}")
    try:
        url = await state.get_playurl(pid)
    except Exception as e:
        raise HTTPException(502, f"播放地址解析失败: {e}")
    if playbackbegin or playbackend:
        extra = []
        if playbackbegin:
            extra.append("playbackbegin=" + playbackbegin)
        if playbackend:
            extra.append("playbackend=" + playbackend)
        url += "&" + "&".join(extra)
    return RedirectResponse(url, status_code=302)


@app.get("/s/{token}/playback.xml")
async def share_playback_xml(token: str):
    require_share(token)
    if not state.epg and not state.epg_refreshing:
        await state.refresh_epg(force=True)
    if not state.epg:
        raise HTTPException(503, "节目单尚未生成，请稍后重试")
    return Response(content=state.build_xmltv(), media_type="application/xml; charset=utf-8")


@app.get("/s/{token}/migu_direct.m3u")
async def share_direct(token: str):
    require_share(token)
    raise HTTPException(403, "分享链接不支持直链版（直链无法控制有效期），请使用 /migu.m3u")


# ---------- 管理接口（需管理密码） ----------
@app.get("/api/admin/share/list")
async def admin_share_list(request: Request):
    require_admin(request)
    return {"tokens": state.share.list()}


class ShareCreatePayload(BaseModel):
    note: str = ""
    days: int = 7


@app.post("/api/admin/share")
async def admin_share_create(payload: ShareCreatePayload, request: Request):
    require_admin(request)
    if payload.days not in (7, 30, 365, 0):
        raise HTTPException(400, "有效期仅支持 7 / 30 / 365 / 0（永久）")
    return {"ok": True, "share": state.share.create(payload.note, payload.days)}


class ShareRevokePayload(BaseModel):
    token: str


@app.post("/api/admin/share/revoke")
async def admin_share_revoke(payload: ShareRevokePayload, request: Request):
    require_admin(request)
    if not state.share.revoke(payload.token):
        raise HTTPException(404, "令牌不存在")
    return {"ok": True}


@app.post("/api/admin/share/delete")
async def admin_share_delete(payload: ShareRevokePayload, request: Request):
    require_admin(request)
    if not state.share.delete(payload.token):
        raise HTTPException(404, "令牌不存在")
    return {"ok": True}


# ---------- 扫码登录 ----------
@app.post("/api/login/qrcode")
async def login_qrcode():
    try:
        info = await state.login.create_qrcode()
        return {"ok": True, **info}
    except Exception as e:
        raise HTTPException(502, f"二维码获取失败: {e}")


@app.get("/api/login/qrcode/status")
async def login_qrcode_status(session_id: str):
    return await state.login.check_status(session_id)


@app.get("/api/login/info")
async def login_info():
    return state.login.info()


@app.post("/api/login/logout")
async def login_logout():
    state.login.logout()
    asyncio.create_task(state.refresh_urls())
    return {"ok": True}


class SettingsPayload(BaseModel):
    rate_type: int | None = None
    h265: bool | None = None


class ManualLoginPayload(BaseModel):
    user_id: str
    user_token: str


@app.post("/api/login/manual")
async def login_manual(payload: ManualLoginPayload):
    if not payload.user_id.strip() or not payload.user_token.strip():
        raise HTTPException(400, "userId 与 userToken 不能为空")
    state.login.set_manual(payload.user_id, payload.user_token)
    asyncio.create_task(state.refresh_urls())
    return {"ok": True, **state.login.info()}


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload):
    if not state.login.is_logged_in():
        raise HTTPException(400, "请先扫码登录")
    if payload.rate_type in (3, 4, 7, 9):
        state.login.rate_type = payload.rate_type
    if isinstance(payload.h265, bool):
        state.login.h265 = payload.h265
    state.login.save()
    asyncio.create_task(state.refresh_urls())
    return {"ok": True, **state.login.info()}
