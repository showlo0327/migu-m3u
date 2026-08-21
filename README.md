# migu-m3u：咪咕视频动态 M3U 电视台列表服务

> ⚠️ **重要警告：登录功能未经作者完整验证，且存在封号风险**
>
> 1. 作者本人**尚未完整跑通扫码登录流程**，登录功能仅供研究参考；
> 2. 使用咪咕账号登录后，由服务器长期批量拉流，**存在封号风险**（公开同类项目已有账号被封的案例）；
> 3. 建议默认使用**免登录模式**（720P 高清）。如需体验蓝光 / 原画 / 4K，请自行评估风险，由此造成的账号损失与作者无关。

基于咪咕视频公开接口开发的免登录动态 M3U 服务。你只需要在 NAS 上跑一个容器，把生成的 M3U 地址填进 APTV、VLC 等支持 M3U 列表的播放器，就能看央视、各大卫视和地方台的直播。

## 特点

- **完全免登录**：不填任何咪咕账号，没有封号风险
- **频道齐全**：央视 28 个、卫视 22 个、地方 37 个，另有新闻 / 影视 / 教育 / 少儿 / 纪实等，共 150+ 个频道（以咪咕官方频道接口为准，自动更新）
- **播放地址自动刷新 + 缓存**：每 60 分钟批量刷新，单频道地址缓存 3 小时，失效自动重取
- **节目单（EPG）+ 回看**：使用咪咕官方节目单（加载快、中文节目名），M3U 已带回看声明，支持回看的播放器（APTV、TiviMate 等）可直接回看近 3 天内容
- **两个订阅地址**：
  - `/migu.m3u`：跳转版，播放时由本服务实时跳转，地址永不过期（推荐）
  - `/migu_direct.m3u`：直链版，包含真实流地址，适合把列表下载下来离线使用
- **可视化面板 + 扫码登录**：浏览器打开面板即可复制订阅地址、用咪咕 App 扫码登录解锁蓝光 / 原画 / 4K、管理画质并查看频道状态
- **国内友好**：Dockerfile 使用清华 PyPI 镜像，README 附国内镜像加速说明

## 在 NAS 上部署（Docker Compose 一键启动）

把整个 `migu-m3u` 文件夹上传到 NAS（群晖 / 飞牛 / 威联通等），然后：

1. 群晖：**Container Manager → 项目 → 新建**，选择 `migu-m3u` 文件夹，自动识别 `docker-compose.yml`，直接启动。
2. 飞牛：**Docker → Compose → 新建**，把 `docker-compose.yml` 内容粘贴进去，启动。
3. 命令行：进入 `migu-m3u` 目录执行：

```bash
docker compose up -d --build
```

基础镜像默认走 **DaoCloud 加速源**（`docker.m.daocloud.io/library/python:3.13-slim`），国内可直接拉取，不需要额外配置。如果该源在你的网络下不可用，二选一：

- 把 [Dockerfile](D:/guanertongxuezzz/Desktop/IPTV-NAS/migu-m3u/Dockerfile) 第一行换成其他源，例如 `docker.1ms.run/library/python:3.13-slim`；
- 或者在 NAS 的 Docker 设置里配置镜像加速（群晖 Container Manager → 注册表设置；飞牛 → Docker 设置 → 镜像加速），填入 `https://docker.m.daocloud.io` 等地址。

> 小提示：如果日志里出现过 `pull access denied for migu-m3u`，那是旧版配置里多余的一条镜像名导致的，新版本已移除，不影响构建，可忽略。

## 常见问题

- **构建卡在 `load metadata for docker.io/library/python`**：Docker Hub 在国内拉不动，本项目基础镜像已默认走 DaoCloud 加速源，重新上传最新版 [Dockerfile](D:/guanertongxuezzz/Desktop/IPTV-NAS/migu-m3u/Dockerfile) 后重建即可。
- **pip 报 `Could not find a version ... (from versions: none)`**：某个 PyPI 镜像源在你网络下暂时不可用。新版 Dockerfile 已内置“清华 → 阿里 → 腾讯 → 官方”自动切换，任一成功即继续；若三个镜像都失败，多半是 NAS 本身网络/DNS 问题，检查 NAS 是否能正常访问外网。
- **日志出现 `pull access denied for migu-m3u`**：旧版 compose 多写了镜像名，已移除，可忽略或直接删掉旧项目重建。

启动后浏览器打开 `http://<NAS-IP>:8090/` 进入管理面板，可以复制订阅地址、扫码登录、查看频道状态。

## 在播放器中添加订阅

打开 APTV、VLC 等支持 M3U 列表的播放器，添加订阅，填入：

```
http://192.168.1.100:8090/migu.m3u
```

（把 IP 换成你 NAS 的实际 IP）

## 节目单与回看

- **EPG 节目单地址**：`http://<NAS-IP>:8090/playback.xml`（XMLTV 格式，也可用 `/epg.xml`）。M3U 头部已自动带上 `x-tvg-url` 指向它，播放器导入 M3U 后无需再手动添加节目单。
- 节目单数据来自**咪咕官方节目单接口**（`tv-programs-data`），覆盖近 3 天至未来 1 天，节目名与咪咕 App 一致。
- **回看**：M3U 已声明 `catchup="append"`，APTV、TiviMate 等支持回看的播放器会自动显示回看入口（节目单上长按/右键选择时间段即可回看）。
- 免费回看范围为**近 3 天**，更早的节目需要咪咕会员；个别频道没有回看权限属正常现象。
- 手动测试回看：`http://<NAS-IP>:8090/play/608807420?playbackbegin=20260821090000&playbackend=20260821100000`（时间格式 `YYYYMMDDHHmmss`）。

## 可视化面板与扫码登录

浏览器打开 `http://<NAS-IP>:8090/` 就是管理面板，包含：

- **订阅地址**：跳转版 / 直链版两个 M3U 地址，一键复制；
- **咪咕账号扫码登录**：点击“获取二维码”，用手机上的**咪咕视频 App 扫一扫**并确认，面板自动填入 userId / token，服务自动切换到登录模式并重新解析全部频道；
- **画质选择**：登录后可选 高清 720P / 蓝光 1080P / 原画 / 4K，并开关 H265 编码，保存后立即生效；
- **服务状态与频道列表**：实时查看每个频道的解析状态，支持搜索和单频道试播。

> ⚠️ 注意事项
>
> 1. 蓝光、原画、4K 需要咪咕账号有对应**会员**，否则会自动降级到高清。
> 2. 登录信息保存在 NAS 的 `data/login.json` 中，重启容器不会掉线；token 过期后需重新扫码。
> 3. 服务器长期使用登录态批量拉流有一定**封号风险**（公开逆向项目反复提醒），请自行权衡；不登录时免登录 720P 高清不受影响。
>
> 手机扫码时 App 会提示“正在TV上登录咪咕家庭版”，这是咪咕网页登录的正常提示，**点击确认即可**。咪咕手机端会员与电视端会员不互通，扫码登录使用的是同一个咪咕账号，账号有手机端会员才能解锁高清及以上画质。

如果扫码登录遇到问题，面板里还提供了**手动填写 userId / token** 的备用方式（App 抓包获取的 nlps 开头 token 最稳定）。

## 分享链接（给朋友）

管理面板的“分享链接管理”可以生成带**有效期**的专属 M3U 地址，适合分享给朋友：

1. 打开面板 → 分享链接管理，输入管理密码（部署时通过环境变量 `MIGU_ADMIN_PASSWORD` 设置，**默认 `admin`，请务必修改**）；
2. 填备注（如朋友名字），选有效期：**7 天 / 30 天 / 一年 / 永久**，点“生成分享链接”；
3. 把生成的地址复制给朋友，形如 `http://<NAS-IP>:8090/s/xxxxxxxx/migu.m3u`，朋友在任意支持 M3U 的播放器中订阅即可。

特性说明：

- 到期后链接自动失效（返回 403），也可随时在面板**吊销**，立即作废；
- 分享链接里的每个频道地址都带 token，**播放时也校验**，所以已导入的列表到期后同样无法播放；
- 节目单、回看同样走分享链接（`/s/{token}/playback.xml`）；
- 分享链接**不支持直链版**（直链是真实 CDN 地址，无法控制有效期）；
- token 就是钥匙，朋友转发给他人也能用，请选择合适有效期并保管好管理密码。

> **访问限制（默认开启）**：为了让分享链接真正生效，服务默认开启 `MIGU_REQUIRE_TOKEN=true`——此时 `/migu.m3u`、`/playback.xml`、`/play/{频道ID}` 均返回 403，只能通过 `/s/{token}/...` 访问，去掉 token 也看不到任何内容。自己用可以生成一个“永久”分享链接，或把 `MIGU_REQUIRE_TOKEN` 改为 `false` 恢复开放访问。直链版 `/migu_direct.m3u` 默认保持开放（自用），如需彻底关闭可设 `MIGU_ALLOW_DIRECT=false`。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MIGU_BASE_URL` | 自动识别 | 播放器访问本服务的地址，如 `http://192.168.1.100:8090`；局域网内可不填 |
| `MIGU_CATEGORIES` | `央视,卫视,地方,新闻,影视,教育,综艺,少儿,纪实` | 要包含的频道分组，按逗号分隔 |
| `MIGU_RATE_TYPE` | `3` | 画质：`3`=高清720P（免登录最高）；`4` 蓝光需要登录 |
| `MIGU_H265` | `false` | 是否请求 H265 原画。`true` 画质更好，但部分播放器/浏览器可能无法解码，默认关闭 |
| `MIGU_REFRESH_MINUTES` | `60` | 批量刷新播放地址的间隔（分钟） |
| `MIGU_URL_CACHE_MINUTES` | `180` | 单频道播放地址缓存时长（分钟） |
| `MIGU_MAX_WORKERS` | `6` | 批量解析时的并发数 |
| `MIGU_CHANNELS_FILE` | `/data/channels.json` | 频道列表缓存文件（容器内路径） |
| `MIGU_LOGIN_FILE` | `/data/login.json` | 扫码登录信息保存位置（容器内路径） |
| `MIGU_ADMIN_PASSWORD` | `admin` | 面板管理密码（分享链接管理用，请修改） |
| `MIGU_TOKENS_FILE` | `/data/tokens.json` | 分享令牌保存位置（容器内路径） |
| `MIGU_REQUIRE_TOKEN` | `true` | 访问限制：开启后 /migu.m3u、/playback.xml、/play 必须用分享链接 |
| `MIGU_ALLOW_DIRECT` | `true` | 直链版是否开放（自用）；false 则彻底关闭 |

## 接口一览

| 地址 | 说明 |
|---|---|
| `GET /` | 可视化管理面板 |
| `GET /migu.m3u` | 推荐订阅地址（本服务跳转版） |
| `GET /migu_direct.m3u` | 直链版订阅地址 |
| `GET /playback.xml` | EPG 节目单（XMLTV），同 `/epg.xml` |
| `GET /play/{频道ID}` | 单频道 302 跳转到真实播放地址 |
| `GET /status` | 频道与解析状态 |
| `GET /refresh` | 手动触发一次刷新 |
| `GET /health` | 健康检查 |
| `POST /api/login/qrcode` | 获取扫码登录二维码 |
| `GET /api/login/qrcode/status` | 轮询扫码状态（等待/已扫码/成功） |
| `GET /api/login/info` | 当前登录状态 |
| `POST /api/login/logout` | 退出登录 |
| `POST /api/settings` | 保存画质与 H265 设置 |
| `GET /s/{token}/migu.m3u` | 分享订阅地址（带有效期） |
| `GET /s/{token}/play/{频道ID}` | 分享频道跳转 |
| `GET /s/{token}/playback.xml` | 分享节目单 |
| `GET/POST /api/admin/share*` | 分享令牌管理（需管理密码） |

## 实现原理（给爱折腾的朋友）

1. **频道列表**：`program-sc.miguvideo.com/live/v2/tv-data/{分类ID}`，免登录返回央视 / 卫视 / 地方等全部分类与频道 ID（本项目的 `channels.json` 就是它的快照，服务运行时会自动更新）。
2. **播放地址**：`play.miguvideo.com/playurl/v1/play/playurl`，用安卓 720P 免登录签名（`sign` 为固定密钥 + 时间戳 + 频道 ID 的 MD5），无需任何账号。
3. **ddCalcu 加密**：咪咕的 m3u8 地址必须带 `ddCalcu` 参数才能拉流，否则返回 661。本服务实现了完整的纯字符串算法（无需 wasm 环境），实测加密后 m3u8 / TS 分片均可正常访问。

> 说明：本项目为个人学习与研究用途，频道与流地址均来自咪咕官方接口，请勿用于商业用途。

## 已知限制

- 极少数频道（如 CHC 动作电影、CHC 家庭影院）版权要求登录，免登录模式下无法播放；**扫码登录后即可播放**。
- 个别频道偶尔返回“节目播出调整”，过段时间会自动恢复。
- 咪咕接口与加密参数可能随版本更新而变化，届时需同步更新本服务。

## 开发计划

- ~~回看功能~~：已支持近 3 天回看（结合咪咕官方 EPG）。
- 后续：会员专属回看、更多播放器兼容性测试。
