# Helldivers Tactical Command — 交接文档

> 交接范围：项目定位、架构、关键实现决策、已知陷阱、构建发布流程、后续可做项。
> 文档基于 v1.0.0（含“窗口层级 / 背景透明度”设置功能）的最终交付状态编写，并叠加后续的置顶修复、拖动修复、挂件 v2、品牌资源与 M0 地基记录。

---

## 1. 项目概览

| 项 | 内容 |
|---|---|
| 名称 | Helldivers Tactical Command（绝地潜兵战术指挥终端） |
| 类型 | Windows 桌面常驻小挂件 + 可展开的战术指挥大窗 |
| 数据 | 《绝地潜兵 2》银河战争实时数据（MO、行星事件、新闻、DSS、统计） |
| 技术 | Python 3.11 + pywebview（EdgeChromium/WebView2）+ 原生 HTML/CSS/JS；PyInstaller onedir 打包 |
| 安装位置 | `C:\Users\Administrator\AppData\Local\Programs\HelldiversTactical` |
| 发布物 | `release\HelldiversTactical-v1.0.0-win64.zip`（约 24.5 MB，247 个条目） |

核心体验：默认 400×260 挂件贴在屏幕右下角 → 点击展开 1440×900 指挥台；不出现在任务栏，可缩进托盘；重大事件只做视觉告警不发声；开机自启。

---

## 2. 目录结构

```
HelldiversTactical/
├─ app.py                 程序入口：窗口、AppController、TrayController、Bridge、CLI
├─ hd2/
│  ├─ api.py              数据抓取 / 归一化 / 双源合并 / 缓存回退（905 行）
│  ├─ alerts.py           静默告警引擎（237 行）
│  ├─ config.py           路径解析、默认设置、窗口层级归一化、读写（137 行）
│  ├─ autostart.py        启动项 / 桌面快捷方式（109 行）
│  ├─ winflash.py         Win32：窗口几何、层级三态（含置底 keeper）、透明度、任务栏、托盘闪烁（624 行）
│  ├─ store.py            SQLite 地基：连接、PRAGMA、schema v1 迁移（M0）
│  ├─ scheduler.py        单线程任务调度：间隔可变、失败指数退避（M0）
│  ├─ netcache.py         缓存 + 限速 HTTP：ETag、429 退避、离线回退（M0）
│  ├─ modules/            游戏模块：base.py 契约 / registry.py 注册表 / hd2_mod.py 适配器（M0）
│  └─ util.py             阵营表、时间/时长格式化、原子写 JSON（159 行）
├─ web/
│  ├─ index.html          结构：挂件 #widget、大窗 #app、设置弹窗 #settings-modal
│  ├─ css/style.css       HD2 风格（切角、危险条纹、扫描线），822 行
│  └─ js/
│     ├─ app.js           状态机、渲染、bridge 调用、设置（956 行）
│     ├─ map.js           Canvas 银河地图（571 行）
│     └─ clock.js         像素辉光管时钟（82 行）
├─ data/                  planets.json（281 星）、tasks.json、fallback_snapshot.json、games.json（游戏注册表，M0）
├─ tests/                 unittest：M0 地基（store / scheduler / netcache / registry）
├─ assets/                icon.ico（多尺寸）/ icon.png（1024，托盘与窗口图标）
│                         web/favicon.png（256）——图标与内联 SVG 同源，见 6.2 品牌记录
├─ docs/                  screenshot-{command,map,clock,alert,widget,settings}.png
├─ packaging/HelldiversTactical.exe.config
├─ pyi_rth_hd2_pythonnet.py   PyInstaller 运行时 hook（pythonnet 修复）
├─ build.ps1 / install.ps1 / uninstall.ps1 / 安装.bat / 卸载.bat
├─ dist/HelldiversTactical/   构建产物
└─ release/                   发布 zip 与 staging 目录
```

---

## 3. 运行时架构

### 3.1 线程模型

| 线程 | 职责 |
|---|---|
| WebView2 UI 线程 | pywebview 主线程，渲染页面 |
| `hd2-refresh` | `AppController._loop()`：每 `poll_seconds`（默认 60，clamp 30–300）调 `refresh_once()` |
| Bridge 线程 | pywebview 处理 JS `api.*` 调用（注意：**不要在此线程调用 pywebview 的 resize**，会卡死） |
| Tray 线程 | pystray 图标与菜单 |

JS 侧另有 15 秒一次的 `pollState()`（`get_state`）用于补偿推送丢失。

### 3.2 进程内单实例

`MUTEX_NAME = "Local\\HD2TacticalCommand-v1-8D4B21"`。第二个实例会弹窗提示并退出（日志 `Single-instance mutex acquired=False`）。
**开发时务必先停掉安装版进程，否则新启动的源码版/新构建版会静默退出。**

### 3.3 窗口三态

| 模式 | 尺寸/位置 | 说明 |
|---|---|---|
| widget | 380×160，工作区右下角 −16px | 默认；`#widget` 可见、`#app` 隐藏 |
| expanded | ≤1440×900、居中（不足时 工作区−80） | `#app` 可见；底部最小 1000×680 |
| 托盘隐藏 | — | `hide_window`，进程继续刷新与告警 |

窗口参数：`frameless=True, shadow=True, on_top=True, easy_drag=False, min_size=(340,140)`，背景色 `#080906`。
小窗尺寸常量在 `app.py` 顶部：`WIDGET_W, WIDGET_H = 380, 160`、`WIDGET_MIN_W, WIDGET_MIN_H = 340, 140`（必须与 `min_size` 同步）。
`hide_from_taskbar()` 设置 `WS_EX_TOOLWINDOW`，因此**不进任务栏也不进 Alt+Tab**；拖拽靠 `pywebview-drag-region` + `Bridge.start_drag()`（`SendMessage WM_NCLBUTTONDOWN/HTCAPTION`）。

### 3.4 前后端通信

- Python → JS：`window.evaluate_js("window.HD2 && window.HD2.receive({...})")`，payload 类型 `state` / `alerts`。
- JS → Python：`window.pywebview.api.<method>()`。
- Bridge 方法清单：`bootstrap, get_state, list_modules, scheduler_status, expand, collapse, hide_to_tray, start_drag, quit, refresh, set_setting, set_zorder, set_topmost, set_autostart, autostart_status, create_desktop_shortcut, acknowledge, open_url, minimize`。
- 启动时 JS 用 12 次 × 250ms 重试 `bootstrap`，失败提示“数据链桥接失败”。

---

## 4. 数据层（hd2/api.py）

### 4.1 三路数据源

1. **社区 API**（主）：`https://api.helldivers2.dev/api/v1` — `/assignments`、`/planets`、`/war`、`/dispatches`
2. **ArrowHead 官方**：`https://api.live.prod.thehelldiversgame.com/api` — `WarSeason/current/WarID`、`WarSeason/{id}/Status|WarInfo`、`Stats/war/{id}/summary`、`NewsFeed/{id}`、`v2/Assignment/War/{id}`
3. **Companion 中继**：`https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live`

### 4.2 必读：请求头

社区 API 缺少以下两个头会返回 **HTTP 400 `The X-Super-Client and X-Super-Contact headers are required`**：

```python
"X-Super-Client": "HelldiversTactical",
"X-Super-Contact": "local-desktop-app",
```

`http_json()` 已统一注入，另带 `Accept-Language: zh-Hans,...` 以获得中文叙事。

### 4.3 抓取与合并流程（`LiveDataService.fetch()`）

1. 社区与官方在两线程中并行；社区失败后进入**指数退避** `min(900, 60 * 2**min(4, failures))` 秒。
2. 两者都成功 → `_merge_dual()`：
   - 行星 / 事件 / 在线人数取社区（`planet.statistics.playerCount`）
   - 重要指令标题与简报、`global_events`、`DSS`、新闻取官方（本地化中文）
   - `source = {name: "COMMUNITY API", status: "LIVE", secondary: "ARROWHEAD DIRECT"}`
3. 仅有官方 → 直接用官方快照（UI 显示 `ARROWHEAD DIRECT`）。
4. 两者都失败 → Companion 中继。
5. 仍失败 → `load_cached()`：先读 `cache/snapshot.json`（显示 `... // CACHE`），再读内置 `data/fallback_snapshot.json`（`ARCHIVE SNAPSHOT`），并标记 `status = "OFFLINE"`。

> 官方 `NewsFeed` 常滞后，`_fetch_official()` 会额外拉 Companion 的 `news` 做增量合并；Companion 不可用只记 info 日志，不影响主流程。

### 4.4 数据契约（归一化快照）

```
{ ok, fetched_at, cached, source{name,status,endpoint,secondary,latency_ms,last_success,error,war_id},
  war{id, players, ...}, major_order{id,title,briefing,objectives[],reward{type,amount},expires_at,remaining_seconds,remaining,progress},
  events[{id,planet_index,planet_name,planet_name_zh,faction{},is_defense,progress,players,remaining,severity}],
  planets[{index,name,name_zh,sector,owner{key,zh,en},biome,environmentals[],players,control,health,max_health,
           regen,has_dss,supply_lines[],mo_target}],
  news[{id,title,message,published_at}], global_events[], dss{planet_index,...}, stats{...} }
```

### 4.5 时间口径（踩过的坑）

- **官方 `WarInfo.startDate` 会整体偏移约 22 天**，不能直接当 epoch 用。事件起始时间须按
  `start = now - (war_clock_seconds - event.startTime)` 反推（`_parse_event_dt` / `_news_time`）。
- 官方事件用 `expireTime`（war-time 秒）；社区事件用 `endTime`。

---

## 5. 告警层（hd2/alerts.py）

`AlertEngine.evaluate(snapshot)`，状态持久化在 `state/alerts.json`。

| 规则 | 严重度 | 触发条件 |
|---|---|---|
| `MAJOR_ORDER` | critical | 重要指令 id 变化 |
| `MO_EXPIRING` | high | 剩余 ≤ 6 小时（每次 MO 只报一次） |
| `PLANETARY_EVENT` | critical/high | 出现新事件（按 event id 去重） |
| `GALACTIC_EVENT` | critical | 官方 `globalEvents` 中新 `flag == 1` 的条目 |
| `PLANET_CHANGE` | critical/high | 归属变化；忽略在线 <200 且非 MO 目标、非事件星的行星；合并成一条“失守/解放” |
| `DSS` | medium | 民主空间站换轨道 |

关键行为：
- **首次运行只建立基线，不弹任何告警**（日志 `Alert baseline established`）。
- 新闻不逐条弹窗（早期版本会一次弹 5 条 DISPATCH 噪音，已移除），只记录 `news_ids`。
- 历史最多 `MAX_HISTORY = 80` 条，支持 `acknowledge(id)`。
- 有新告警时：任务栏 `flash_window(WINDOW_TITLE, 6)` + 推送 `alerts`；若 `alert_focus` 为真还会 `window.restore()`。**全程不调用任何声音 API。**

---

## 6. 设置与窗口行为

### 6.1 设置项（`%LOCALAPPDATA%\HelldiversTactical\settings.json`）

```json
{ "alerts_enabled": true, "alert_focus": false, "poll_seconds": 60,
  "reduced_motion": false, "show_source_detail": true, "autostart": true,
  "last_autostart_check": 0, "window_zorder": "top", "always_on_top": true,
  "window_opacity": 100, "features": { "notes": false, "events": false } }
```

- `window_zorder`：`"bottom"` / `"normal"` / `"top"`，默认 `"top"`，widget 与 expanded 都遵循；`hd2/config.py::normalize_zorder(value, legacy)` 负责校验与迁移，非法值按旧字段回落再退回 `"top"`。
- `always_on_top`：`v1.1` 之前的旧字段，现在只作镜像保留（`window_zorder == "top"` 时为 `true`），旧 JSON 只有它时按 `true → top`、`false → normal` 迁移。
- `features`：功能开关（`notes` / `events`），M0 地基预留，业务未开工时恒为 `false`。
- `window_opacity`：35–100（clamp，非法值回退 100）。
- 旧版本 JSON 由 `load_settings()` 自动补默认值，无需迁移脚本。
- 写入用 `atomic_write_json`（临时文件 + `os.replace`）。

### 6.2 透明度实现

`hd2/winflash.py::set_window_opacity(title, percent)`：

```python
WS_EX_LAYERED = 0x00080000
SetLayeredWindowAttributes(hwnd, 0, round(percent / 100 * 255), LWA_ALPHA)
```

即**整窗 alpha**：窗口半透明时文字与背景一起变淡（桌面挂件常见观感）。
若要“只让背景透明、文字保持不透明”，需改造为 `DwmEnableBlurBehindWindow` 或让 CSS 背景带 alpha，这属于后续可做项。
窗口层级统一走 `winflash.set_window_zorder(title, zorder)`（内部 `SetWindowPos` + `SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE`，见下方“窗口层级三态”记录）；`set_window_topmost()` 退化成 `zorder in ("top",)` 的布尔包装。

> **修复记录（“取消置顶无效”根因）**：`winflash.py` 早先直接调 `ctypes.windll.user32.SetWindowPos(...)` 但**没有声明 `argtypes`/`restype`**，ctypes 按默认 `c_int` 传参，`SetWindowPos` 返回 0、窗口扩展样式一字不变；旧代码只捕获 Python 异常（`LOG.debug`），因此失败是**静默**的。实测（对运行中的窗口，未声明 argtypes）：`SetWindowPos(hwnd, HWND_TOPMOST)` 与 `(hwnd, HWND_NOTOPMOST)` 都返回 0 且 `exstyle` 保持 `0x90080`；补上 argtypes 后同样两次调用都返回 1，`exstyle` 在 `0x90080` / `0x90088`（含 `WS_EX_TOPMOST=0x8`）之间正确切换。窗口由 pywebview 以 `on_top=True` 创建（`Form.TopMost=True`），原生调用失效后没有任何手段能关掉置顶，所以开关形同虚设。
>
> 现在的实现：
> - 所有原生调用走**模块私有实例** `_USER32/_SHELL32/_DWMAPI/_KERNEL32 = ctypes.WinDLL(..., use_last_error=True)`；`_declare_types()` 只在这些私有实例上声明 argtypes/restype，进程共享的 `ctypes.windll.user32` 保持未声明（原因见下方“拖动失效”记录）。`set_window_pos(hwnd, insert_after, flags)` 是所有 z 序操作的唯一入口，失败会 `LOG.warning`。
> - `set_window_geometry()` 只做几何（`SWP_NOZORDER`），不再顺带改置顶；置顶状态只由 `apply_visual_settings()` 里的 `set_window_topmost()` 负责，改尺寸/改透明度都不会把窗口重新钉到最上层。
> - `set_window_opacity()` 的 `SetWindowPos` 刷新也带 `SWP_NOZORDER`，拖动透明度滑块不会改变 z 序。
> - `find_window_handle()`：`FindWindowW` 失败时用 `EnumWindows` 精确匹配标题位，避免标题尚未设置时拿不到 hwnd。
> - `show_window()` 用 `SW_SHOWNOACTIVATE` 显示托盘里的窗口，不会抢焦点、也不会把窗口重新插到最上层；`hide_window()`/`toggle_window_visibility()` 同理走统一入口。

> **修复记录（“拖动失效”回归）**：第一次修置顶时把 `SetWindowPos.argtypes` 声明在了进程共享的 `ctypes.windll.user32` 上。pywebview 的 winforms 后端移动窗口用的是 `windll.user32.SetWindowPos(self.Handle.ToInt32(), None, x_phys, y_phys, None, None, SWP_NOSIZE|SWP_NOZORDER|SWP_SHOWWINDOW)`——`cx/cy` 传 `None`（靠 `SWP_NOSIZE` 忽略）。argtypes 生效后 ctypes 校验参数直接抛 `ArgumentError: argument 5: TypeError: wrong type`，异常被 pywebview 的 JS 桥吞掉，于是每次 `mousemove` 都变成静默 no-op：grip 能按下、回调也照发，但窗口一动不动。
>
> 修法：`hd2/winflash.py` 不再碰共享 `windll`，改用私有 `WinDLL` 实例声明原型（`_declare_types()`）。pywebview 那条带 `None` 的调用恢复成未声明状态，可以正常通过；`SingleInstance`（命名互斥量 `Local\HD2TacticalCommand-v1-8D4B21`）在重写时被误删，已按原实现补回，并改用私有 `_KERNEL32.CreateMutexW`（`use_last_error=True` + `ctypes.get_last_error()`，仍按 `ERROR_ALREADY_EXISTS=183` 判断）。
>
> 实测（安装版）：`_jsApiCallback('pywebviewMoveWindow',[+37,+23])` 让窗口 `(1512,768) → (1549,791)`；真实鼠标按住 grip 拖动 `(600,400) → (666,433)`；同期置顶开关来回切换 `exstyle 0x90080 ↔ 0x90088` 正常。
>
> 排查陷阱：验证拖动**必须**先确认命中点属于本窗口（`WindowFromPoint` 的顶层祖先），因为小窗被别的全屏窗口（本例是 Steam 全屏窗口）盖住时，真实鼠标事件根本到不了 WebView2，看起来就像“拖动坏了”。合成事件（CDP `Input.dispatchMouseEvent`）不受遮挡影响，适合单独验证 JS→Python 移动链路；grip 的命中框是 `#widget-grip` 的 16×26 区域（左上约 +10,+12），点偏了会落到 `#settings-scrim` 上。

> **修复记录（大窗按 MINI 收回跑到右下角）**：`app.py::set_mode()` 的 `widget` 分支原先硬编码 `x = right - WIDGET_W - 16; y = bottom - WIDGET_H - 16`，只记得“默认右下角”，不记得用户把小窗拖到过哪里，所以“拖走 → 放大 → MINI”必然跳回右下角。
>
> 现在：
> - `AppController.__init__` 新增 `self._widget_geometry: tuple[int, int, int, int] | None`，记录小窗最后一次的 `(width, height, left, top)`。
> - `_remember_widget_geometry()` 只在 `widget → expanded` 边沿调用（`set_mode()` 里先取 `previous = self.mode` 再赋值），用新增的 `winflash.get_window_rect(title)`（`GetWindowRect`，argtypes 走私有 `_USER32`）读当前矩形；宽高小于 `WIDGET_MIN_W/WIDGET_MIN_H`（现为 340×140，与 `min_size` 一致）时丢弃，避免把异常尺寸记下来。
> - `_widget_target(...)` 在有存档时把宽高 clamp 到 `[380, work_w]` / `[240, work_h]`、坐标 clamp 进工作区（防止改过分辨率/拔掉副屏后窗口跑到屏幕外），没有存档才回退到右下角 16px 内边距。
> - 首次启动、托盘隐藏/显示都不经过这条路径，行为不变。
>
> 实测（安装版）：小窗拖到 `(700,350,420,270)` → `⛶` 展开 `(240,66,1440,900)` → `MINI` 回到 `(700,350,420,270)` 完全一致；另测放置 `(1700,900)`（超出 1920×1032 工作区）→ 展开/收回后 clamp 到 `(1520,772)`。

> **改造记录（桌面挂件 v2：大时钟 + 左侧标签栏）**：需求是“小窗偏大、留白多、要像桌面挂件”，于是把 400×260 收到 **380×160**（最小 340×140），并把小窗内容重构为“左侧竖排标签栏 + 右侧整块内容面板”：
>
> - 结构（`web/index.html`）：`.widget-main` > `.widget-rail`（两个 `.widget-tab`：`data-wtab="clock"` 时钟、`data-wtab="mo"` MO）+ 两个 `.widget-pane`（`#wpane-clock` 默认 `.is-active`、`#wpane-mo`）。原 `#w-events` 已从 DOM 移除（`renderWidget()` 里对 `$('#w-events')` 做了存在性判断）。
> - 时钟面板：`#widget-nixie` 撑满面板，底部一行 `.widget-clock-foot`（左 `#w-date`+`#w-week`，右 `#w-players`）。辉光管像素尺寸用容器查询单位自适应：`.widget-main { container-type: inline-size }` + `.widget-nixie .tube { --px: clamp(5.2px, 1.9cqw, 11px) }`（不支持 cqw 时回落到固定 6.5px，不会溢出）。
> - MO 面板：`.widget-mo-head`（MAJOR ORDER // 重要指令 + `#w-mo-progress`）→ `#w-mo-title`（15px、两行截断）→ 进度条 → `.widget-mo-sub`（`#w-mo-objective` + 橙色 `#w-mo-time` 倒计时）。
> - 切页由 `app.js::setWidgetTab(name, persist)` 负责（只认 `clock`/`mo`）；`bindEvents()` 给每个 `.widget-tab` 绑定 click 并 `stopPropagation()`，所以点标签**不会**触发“点小窗任意处展开”。用户选择写 `localStorage['hd2.widgetTab']`，`finishBoot()` 启动时恢复。
> - 实测（源码版 CDP）：380×160 下六个数字占满 311/342px；点 `#wtab-mo` 窗口矩形不变（`(1524,856,1904,1016)`）；`Page.reload` 后仍停在 MO（localStorage 生效）；340×140 与 520×320 下 `scrollWidth/scrollHeight` 均等于 `clientWidth/clientHeight`，无滚动溢出。
> - 坑：`getComputedStyle(el).getPropertyValue('--px')` 返回的是 `clamp(...)` 字面量而不是解析后的像素值，判断缩放要看截图或量 `.tube` 的 rect。

> **改造记录（阵营配色 + 品牌 logo）**：
>
> - 配色：人类阵营由品牌黄 `#ffe81a` 改为超级地球蓝 **`#4cc2ff`**，原来和终结族的暗黄 `#d7a52e` 在深色银河图上分不清。改动点只有三处色值定义：`web/js/map.js:4`（`PALETTE.humans`）、`web/js/app.js:9`（`COLORS.humans`）、`web/css/style.css:22`（`--humans`，图例 `.lg.humans` 用）。MO 高亮、网格经纬线、进度百分比仍是品牌黄——那是强调色不是阵营色，故意不动。
> - logo：官方素材取自 Steam CDN `steam/apps/553850/logo.png`（640×360 RGBA），裁出中间翅膀骷髅徽记后按 alpha/亮度二值化 → 最大连通域 → 沿对称轴镜像 → 形态学开闭 → Moore 边界追踪 → Chaikin×2 → RDP（eps=2.2），得到 29 点轮廓，写成 `viewBox="0 0 100 100"`、`fill="currentColor"`、`fill-rule="evenodd"` 的单条 path（两只眼睛是 arc 子路径，鼻子是三角子路径）。同一段 path 内联进 `web/index.html` 三处：`.widget-mark`（小窗 16–19px）、`.brand-mark`（大窗 35px）、`.boot-emblem`（启动页 76px / 小窗 30px），颜色由 `color: var(--yellow)` 控制。
> - 图标：`assets/icon.png`（1024×1024 深色圆角徽章 + 黄边 + 居中骷髅）供托盘（`app.py` 的 `TrayController`）与窗口图标；`assets/icon.ico`（16/20/24/32/40/48/64/96/128/256）供 exe（`.spec`）与快捷方式（`install.ps1`）；`web/favicon.png`（256）供页面。三者与内联 SVG 同源同几何。
> - 坑：大窗截图里 logo 看起来偏暗偏橄榄（取色 `#ffe81a` 处渲染成约 `(176,162,30)`），一度怀疑透明度/滤镜。实际是 `style.css:55` 的 `.scanlines` 与 `:60` 的 `.vignette` 覆盖层压在内容之上（`body.mode-widget` 时 `display:none`，所以小窗里是满色）。验证方法：用 CDP 在页面里临时插一个 `background:#ffe81a` 的方块再截图取色，得到 `(255,232,26)`，说明渲染与截图都没问题。
> - 前端版本号升到 `?v=6`。
> **改造记录（窗口层级三态：置底 / 通常 / 置顶）**：需求是“光有置顶开关不够，要能把挂件钉在桌面底层”。
>
> - 数据层：`settings.json` 新增 `window_zorder`（`bottom`/`normal`/`top`），`always_on_top` 降级为镜像；`hd2/config.py` 新增 `ZORDER_VALUES` 与 `normalize_zorder(value, legacy)`，`load_settings()` 用旧字段迁移并回写镜像。`Bridge.set_setting` 的 `always_on_top` 键仍接受（映射成 `top`/`normal`），并新增 `Bridge.set_zorder(value)`；旧前端只发 `always_on_top` 也不会坏。
> - 原生层（`hd2/winflash.py`）：新增 `HWND_TOP=0`/`HWND_BOTTOM=1`/`HWND_TOPMOST=-1`/`HWND_NOTOPMOST=-2`、`set_window_zorder(title, zorder)`、`get_window_zorder(title)`、`_window_class()`/`_is_pinned_to_desktop()`（沿 `GW_HWNDNEXT` 找第一个可见窗口，判断是不是 `Progman`/`WorkerW` 桌面），以及 `ZOrderKeeper(title, interval=0.5)`（后台线程，只在 `bottom` + 可见 + 非前台 + 不贴桌面时重发 `HWND_BOTTOM`）。`apply_visual_settings()` 调 `set_window_zorder()` 后把 keeper 的 `set_position(zorder)` 同步过去，`stop()` 里先停 keeper。
> - **坑 1**：`SetWindowPos(hwnd, HWND_BOTTOM, ...)` 会把 `WS_EX_TOPMOST` 一并清掉，所以“置底 → 置顶”必须先置底再回 `HWND_TOPMOST`，`set_window_zorder()` 里对两种方向都先 `HWND_NOTOPMOST` 再落位。
> - **坑 2**：“置底 → 通常”不能只发 `HWND_TOP`：实测该窗口 `SetWindowPos(hwnd, HWND_TOP, ...) = 1` 但 `EnumWindows` 下标纹丝不动（`BringWindowToTop` 也一样），因为窗口正贴着桌面层。改成经典写法——先 `HWND_TOPMOST` 再 `HWND_NOTOPMOST`（仅当 `_is_pinned_to_desktop()` 为真时）——“通常”才真正脱离桌面层：下标 280 → 37。
> - **坑 3**：置底状态下外部（含用户点别的窗口）任何抬升都会被 keeper 在 0.5s 内拉回底层，这是设计行为；调试时若手动 `SetWindowPos` 看到“返回 1 却没效果”，先确认当前是不是 `bottom`。
> - **坑 4（排查陷阱）**：调试期端口 9223 曾被**上一轮实例遗留的 `msedgewebview2.exe`** 占着，CDP 连到的是已失去 Python 桥的旧页面，点按钮毫无反应，被误判成“三态不生效”。重启前必须 `Stop-Process python3.11,msedgewebview2,HelldiversTactical`，并用 `netstat -ano | findstr :9223` 核对监听 pid 是不是当前 python 进程。
> - 前端：`web/index.html` 里原“窗口置顶”开关行换成 `#setting-zorder`（`role="radiogroup"`，三个 `button.seg`，`data-zorder` 分别是 `bottom`/`normal`/`top`，文案 `置底`/`通常`/`置顶`）；`app.js` 新增 `ZORDER_LABELS`、`applyZorder()`、`setZorder()`（乐观更新 + 失败回滚 toast）；`style.css` 新增 `.segmented/.seg` 样式（小窗字号 7px）。
> - 实测（源码版 CDP + `EnumWindows` 下标 + exstyle）：启动 `bottom` 下标 262；点“通常” 37，+2s 稳定在 40；点“置底” 280；点“置顶” 9 且 `exstyle=0x90088`；回“通常” 31。keeper：置底时外部强设 `HWND_TOPMOST`，0.15s 后升到 9，1.65s 被拉回 280；通常/置顶时同样抬升不再被压。安装版（md5 `8bdaee6a6b9e2692786cfe0435dd1076`）复测三态与重启恢复（`bottom` 落盘 → 重启后 `aria=bottom`、原生仍在底层）均通过。
> - 前端版本号升到 `?v=7`。
> **修复记录（置底模式下点击挂件会浮到其他窗口上方）**：用户反馈“点击置底后确实沉底了，但点一下它、或者拖动别的窗口再点它，它就跑到所有窗口上面”。
>
> - 根因：点击会**激活**窗口，shell 随即把激活窗口抬到普通层顶部；而 `ZOrderKeeper.tick()` 里有一行 `if GetForegroundWindow() == hwnd: return`（“别和用户交互抢”），于是置底模式在窗口成为前台后就再也不压回，挂件停在最上层。
> - 试错（已否决）：先试了给置底窗口加 `WS_EX_NOACTIVATE`（0x08000000），想让它点了也不激活。用两个 tkinter 窗口做对照实验（一个当“别的窗口”、一个当“挂件”，都 `-topmost True` 才能盖过本机的 Excel，用 `SetCursorPos`+`mouse_event` 真点击，`EnumWindows` 下标判前后）：**运行期用 `SetWindowLongW(-20)` 加的 NOACTIVATE 拦不住激活**——MSDN 的语义是 “created with this style”。该标志连点击可交互性都正常（clicks 计数照增），但没有阻止激活与抬升，所以整段 `_apply_noactivate()` 已删除，不留在代码里误导后来人。
> - 实际修法：`ZOrderKeeper.tick()` **去掉前台豁免**，只要 `position == "bottom"`、窗口可见、且下面还有应用窗口，就重发 `HWND_BOTTOM`；`AppController` 里把 keeper 间隔从 0.5s 收紧到 **0.2s**（`ZOrderKeeper(WINDOW_TITLE, interval=0.2)`）。实测前台窗口照样能被压回底层（`SetWindowPos(HWND_BOTTOM)` 对激活窗口有效，激活状态不变、只是 z 序下沉），所以点击后最多 0.2s 就回到最底层。
> - 配套：托盘菜单新增「窗口层级」子菜单（`pystray` 三项 `radio=True`，`checked` 读 `controller.settings["window_zorder"]`，动作为 `_dispatch("zorder:bottom|normal|top")`；`TrayController._zone()` 做取值兜底），`window.HD2.trayAction` 增加 `zorder:` 分支转发给 `setZorder()`。这样置底被别的窗口全遮住时，仍能从托盘切换层级。前端版本号 `?v=8`。
> - 实测（安装版 md5 `160c280519d4d3937f33e86793f7a39d`）：置底状态下先强制 `HWND_TOPMOST` 让它浮到 idx=9（exstyle `0x90088`），真实鼠标点击挂件标题区 → `GetForegroundWindow()==hwnd`（确实被激活），**+0.1s 时已经回到 idx=257、exstyle `0x90080`**，之后 1.2s 内保持在前台但始终在底层；三态往返（26 / 9 / 261）与 2s 后稳定性均复测通过。

### 6.3 UI 控件

| 位置 | 控件 |
|---|---|
| 小窗 header | `↻` 刷新 · `⚙` 设置 · `⛶` 放大 · `×` 隐藏到托盘 |
| 小窗左栏 | `时间`（辉光管时钟，默认）· `MO`（重要指令简报）两个标签，点击切面板 |
| 大窗顶栏 | `MINI` 收回小窗 · `⚙` 设置 · `×` 隐藏 |
| 设置弹窗 | 窗口层级三段按钮（`置底` / `通常` / `置顶`，`role=radiogroup`）、背景透明度滑块（35%–100%，`input` 60ms 防抖） |

- 设置弹窗挂在 `<body>` 下（不在 `#app` 内），所以 widget 模式也能显示；`z-index` 980。
- 小窗内容区约 342px 宽（380 − 36px 标签栏），时钟/MO 面板各自内部滚动均已消除（`scrollHeight == clientHeight`）。
- 点击小窗其他区域仍为“展开”，设置/放大/隐藏按钮都调用了 `event.stopPropagation()`。
- 快捷键：`1/2/3/4` 切换 COMMAND / GALACTIC MAP / INTEL FEED / CHRONO，`Esc` 先关设置再关告警。

### 6.4 资源缓存

`index.html` 以 `?mode=widget&v=<unix时间戳>` 加载；CSS/JS 用 `?v=7` 版本号（挂件改版 4→5、阵营配色/logo 资产 5→6、窗口层级三态 6→7；`clock.js` 未改仍为 `?v=3`）。
**改了前端务必同时升 `?v=` 版本号**，否则 WebView2 会命中旧缓存（本项目已踩过：页面结构不变但 JS 未更新）。

---

## 7. 打包与发布

### 7.1 构建

```powershell
cd "D:\工具\AI\DSH work\workspace\HelldiversTactical"
python -m py_compile app.py hd2/config.py hd2/winflash.py hd2/api.py hd2/alerts.py
powershell -ExecutionPolicy Bypass -File build.ps1        # 加 -Console 出调试版
```

`build.ps1` 实际执行：

```
--onedir --noconsole --name HelldiversTactical --icon assets\icon.ico
--runtime-hook pyi_rth_hd2_pythonnet.py
--add-data "web;web" --add-data "data;data" --add-data "assets;assets"
--collect-all webview --collect-all clr_loader --collect-all pythonnet --collect-all pystray
```

随后把 `packaging/HelldiversTactical.exe.config` 复制成 `<name>.exe.config`，并复制 README 和 docs。

### 7.2 三个必须知道的打包坑

1. **onefile 不可靠** → 一直用 `--onedir`；onefile 曾报 `Could not create temporary directory`。
2. **pythonnet 在 D: 盘报错** `Failed to resolve Python.Runtime.Loader.Initialize from ...Python.Runtime.dll`
   → 根因是 .NET 把 D: 判为 remote location 而拒绝 `Assembly.LoadFile`；
   解决：`HelldiversTactical.exe.config` 内 `<loadFromRemoteSources enabled="true"/>`，
   再配合 `pyi_rth_hd2_pythonnet.py`（设置 `PYTHONNET_RUNTIME`、解除 MOTW、补 DLL 目录）。
   **exe 与 .exe.config 必须同目录同名前缀。**
3. **数据目录不可写** → `config.app_data_dir()` 依次尝试 `HD2_DATA_DIR` → `%LOCALAPPDATA%\HelldiversTactical`
   → `~/.helldiverstactical` → `<资源目录>/userdata` → `%TEMP%`。

### 7.3 安装/卸载

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1              # 需先关闭已运行实例
powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
```

- 安装目录默认 `%LOCALAPPDATA%\Programs\HelldiversTactical`（不可写时退到脚本同级 `App`）。
- 创建桌面 `Helldivers Tactical Command.lnk` 与启动目录同名 `.lnk`（开机自启）。
- **脚本必须是 UTF-8 with BOM**，否则 Windows PowerShell 5.1 读中文会乱码。

### 7.4 打发布包

```powershell
$stage = 'release\HelldiversTactical-v1.0.0'
# 先建父目录再复制，否则 dist\HelldiversTactical 会被平铺、zip 结构错乱
New-Item -ItemType Directory -Path "$stage\dist" -Force | Out-Null
Copy-Item 'dist\HelldiversTactical' "$stage\dist" -Recurse -Force
Copy-Item 'docs','packaging' $stage -Recurse -Force
Copy-Item 'README.md','install.ps1','uninstall.ps1','requirements.txt' $stage -Force
Get-ChildItem -Filter '*.bat' -File | Copy-Item -Destination $stage -Force
Compress-Archive -Path "$stage\*" -DestinationPath 'release\HelldiversTactical-v1.0.0-win64.zip' -Force
```

自检要点：zip 内应有 `dist/HelldiversTactical/HelldiversTactical.exe`、`.exe.config`、
`_internal/web/index.html`（含 `style.css?v=4`、`app.js?v=4`、`settings-modal`）、`docs/screenshot-settings.png`。
验证：

```powershell
Expand-Archive .\release\HelldiversTactical-v1.0.0-win64.zip C:\Temp\HD2ReleaseTest -Force
C:\Temp\HD2ReleaseTest\dist\HelldiversTactical\HelldiversTactical.exe --version
# → Helldivers Tactical Command 1.0.0
```

---

## 8. CLI 与调试

| 命令 | 用途 |
|---|---|
| `--version` | 打印版本 |
| `--dump-state <path>` | 抓取快照写 JSON，便于离线排查 |
| `--install-autostart` / `--remove-autostart` | 手动增删启动项 |
| `--desktop-shortcut` | 重建桌面快捷方式 |

- 日志：`%LOCALAPPDATA%\HelldiversTactical\logs\app.log`（轮转）。
- 调试：`set HD2_DEVTOOLS=1` 后启动 → WebView2 调试端口 `9223`，可用 CDP 做
  `HttpClient` + `/json` 列表 + `Runtime.evaluate` / `Page.captureScreenshot` 验证 UI。
- 本地数据：`cache/snapshot.json`、`state/alerts.json`、`settings.json`、`webview/`（WebView2 profile）。

---

## 9. 已知问题与陷阱清单

1. **Store 版 Python 会重定向 `%LOCALAPPDATA%`**：用 Python 读到的路径可能是
   `...\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\Local\...`。
   验证安装版设置请用 PowerShell 读真实路径 `C:\Users\Administrator\AppData\Local\HelldiversTactical\settings.json`。
2. **不要用 pywebview 的 resize/几何 API 在 Bridge 线程改窗口**：会卡死；统一走
   `set_window_geometry()`（`MoveWindow`）。
3. **Bridge 里不要暴露 `controller` 字段**：pywebview 会递归 introspection 产生大量
   `CoreWebView2 ... can only be accessed from the UI thread` COM 错误并拖慢 UI。已改名 `_controller`。
4. **pywebview 关闭事件**被改写为“隐藏到托盘”，真正退出只走托盘菜单“退出”或 `Bridge.quit()`。
5. **CDP 截图可能拿到 resize 前的旧帧**：先等 `set_mode` 完成（约 1–2 秒）再截图。
6. **社区 API 时好时坏**：超时会自动退避并回退官方源，UI 的 `source` 会在
   `COMMUNITY API` / `ARROWHEAD DIRECT` / `COMPANION RELAY` / `... // CACHE` / `OFFLINE` 之间变化，属预期行为。
7. **单实例 mutex**：调试新构建前先 `Stop-Process -Name HelldiversTactical`。
8. **前端缓存**：改 CSS/JS 记得升 `?v=`。

---

## 10. 后续可做项（建议）

| 优先级 | 事项 | 说明 |
|---|---|---|
| 高 | 背景透明度改为“仅背景” | 现为整窗 alpha；可用 CSS `rgba` 背景 + `DwmEnableBlurBehindWindow` 或分层绘制，让文字保持清晰 |
| 中 | 多显示器支持 | 现在用 `work_area()` 主显示器右下角；可记忆上次位置并按光标所在屏贴边 |
| 中 | 挂件位置/尺寸记忆 | 目前每次启动固定回到右下角 |
| 中 | 设置项补充 | 自动刷新频率、是否显示新闻、告警阈值（如事件剩余 <N 小时） |
| 低 | 告警声音（可选开关） | 当前刻意静音；若加需明确默认关闭 |
| 低 | 更新检查 | 版本号已集中定义在 `hd2/config.py:VERSION` |
| 低 | i18n | 目前中文为主 + 英文军事播报，未做语言切换 |

---

## 11. 交付状态（交接时）

- 安装版正在运行，widget 模式 **380×160**（挂件 v2 改版后），无任务栏图标。
- 设置基线：`window_zorder: "normal"`、`window_opacity: 100`、`poll_seconds: 60`、`autostart: true`。
- 开机启动项：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Helldivers Tactical Command.lnk`
  → `C:\Users\Administrator\AppData\Local\Programs\HelldiversTactical\HelldiversTactical.exe`
- 桌面快捷方式：同名 `.lnk`，指向同一 exe。
- 发布包：`release\HelldiversTactical-v1.0.0-win64.zip`（24,497,280 字节，247 entries），已解压验证 `--version` 正常。
- 托盘菜单：打开指挥台 / 收起到桌面挂件 / 立即刷新 / 退出。

---

## 12. v2 功能扩展（M0 已落地，业务未开工）

三个新功能（游戏备忘录 / 赛事卡片 / 赛季倒计时）的架构、数据模型、难点与里程碑，见 `docs/ROADMAP-v2.md`。

- 定位已定：**HD2 降级为“战况模块”之一**，程序升级为多游戏战术终端；1.x 只改界面标题，不改 `APP_ID`/安装目录（数据迁移风险见文档 D9）。
- 范围已定：赛事 = 彩虹六号（`rainbowsix`）+ 英雄联盟（`leagueoflegends`）；赛季并入备忘录（截止日期 + 倒计时 + 到期提醒）。
- **M0 地基与模块入口已按用户指令（2026-11-27）落地，细节见第 13 节。**
- 业务功能（笔记 / 赛事）仍未开工；UI、前端拆分、大窗导航都保持 v1 原样。

---

## 13. v2 M0 地基与模块入口（已落地）

用户指令是"先把地基和模块入口搭好，其他的先不动"，所以本轮只加基础设施与模块边界，**不改 UI、不拆前端、不做笔记/赛事业务**。安装版已验证，窗口行为与 v1 完全一致。

| 文件 | 职责 | 关键入口 |
|---|---|---|
| `hd2/store.py` | SQLite 连接、PRAGMA、schema v1 迁移、查询助手 | `connect()` / `close()` / `transaction()` / `execute/query/query_one` / `table_names()` |
| `hd2/scheduler.py` | 单线程任务调度，失败指数退避 | `Scheduler.add(name, interval, fn, run_now=…, backoff=…, first_delay=…)` / `tick_once()` / `start()` / `stop()` / `snapshot()` |
| `hd2/netcache.py` | 缓存 + 限速 HTTP，离线可回退 | `http_cached(url, ttl=…, limiter=…, fetcher=…, force=…)` → `(payload, "fresh"｜"cache"｜"stale"｜"backoff"｜"error")`；`RateLimiter` |
| `hd2/modules/base.py` | 模块契约 | `GameInfo` / `WidgetCard` / `GameModule`(Protocol) / `BaseModule` |
| `hd2/modules/registry.py` | 读 `data/games.json`，绑定实现 | `load_registry(services, path, factories)` / `parse_games()` / `ModuleRegistry.describe()/cards()/enabled()/by_kind()` |
| `hd2/modules/hd2_mod.py` | HD2 战况适配器（不改 `api.py`） | `Hd2Module.observe(snapshot)` / `widget_card()` |
| `data/games.json` | 游戏注册表 | `hd2`(module=hd2, enabled) + `r6`/`lol`/`ow2` 占位条目 |
| `tests/test_m0.py` | 地基单测（14 例） | `python -m unittest discover -s tests -t . -v` |

### 13.1 存储

- 库文件 `%LOCALAPPDATA%\HelldiversTactical\hd2.db`，与既有 JSON 状态同目录（不可写时沿用 `config` 的 TEMP 回退）。
- 单连接 + `check_same_thread=False` + 模块级 `RLock`：调度线程与 webview 线程共用，不会踩 threading 检查。
- `PRAGMA journal_mode=WAL`、`synchronous=NORMAL`、`foreign_keys=ON`；schema 版本写在 `PRAGMA user_version`。
- 表：`meta` / `notes` / `matches` / `follows` / `http_cache`。`notes` 已带 `due_utc`、`reminded_at`、`deleted`，就是"赛季并入备忘录"要用的列，后续不用再迁移。
- 迁移是加法式的：`_migrate()` 只在 `user_version < SCHEMA_VERSION` 时执行，实测首次启动日志 `SQLite migrated from v0 to v1`。

### 13.2 调度

- 原来 `AppController._loop` 的 `while True: fetch; sleep` 已删除，改为 `Scheduler` 的一个 `war` 作业：

```python
self.scheduler.add(
    "war",
    interval=lambda: max(30, min(300, int(self.settings.get("poll_seconds") or 60))),
    fn=self.refresh_once,
    first_delay=1.5,  # 等 webview 注册好 JS bridge
)
self.registry.bootstrap()
self.scheduler.start()
```

- 间隔传的是 callable，**改 `poll_seconds` 不需要重注册作业**，下一次 tick 自动按新间隔排期。
- 作业抛异常只影响自己：`failures += 1`、`next_at = now + min(backoff_max, backoff_min * 2**(failures-1))`，默认 30s→300s 封顶；成功一次即清零。循环本身不会死。
- `snapshot()` 暴露 `name/interval/runs/failures/last_duration_ms/next_at/last_error`，用于 `bootstrap()` 与 `Bridge.scheduler_status()`。

### 13.3 网络缓存

- 只有赛事/赛季这类可选功能会联网，全部走 `http_cached`；v1 的战况抓取仍走 `hd2/api.py`，未改动。
- 优先级：未过期缓存 → `"cache"`；带 `If-None-Match` 请求，`304` 刷新时间戳 → `"fresh"`；`429/503` 读 `Retry-After` 写 `next_try`，期间返回旧数据 → `"stale"`；异常同理，绝不让调用方处理异常。
- `RateLimiter` 按 host 记录下一个可用时刻，默认最小间隔 2s（Liquipedia ToS：全站 ≤1 请求/2 秒）；lolesports 可单独传更小的 limiter。
- `fetcher` 参数可注入，测试用假 fetcher，不落真实网络。

### 13.4 模块注册表

- `load_registry()` **永不抛异常**：文件缺失/JSON 坏/条目重复/未知模块名/构造异常，都只记日志并降级，问题放在 `ModuleRegistry.error`。
- `r6`/`lol`/`ow2` 现在没有实现，注册表里照样列出（`describe()` 里 `available: false`），以后加 `providers/` 时只需要在 `default_factories()` 里登记。
- `Hd2Module` 不发网络请求：`refresh()` 是空实现，`observe()` 只接收 `refresh_once()` 已经拿到的快照，`widget_card()` 把它翻译成 `{title, subtitle, status, metrics:[{label,value}], updated_utc}`。这样 `api.py` 一行没改，壳层却已经能"泛化地"渲染任意游戏。
- `refresh_once()` 里对每个 enabled 模块调用 `observe()`，模块异常只 `LOG.debug`，不影响战况推送。

### 13.5 设置与 Bridge

- `DEFAULT_SETTINGS` 新增 `"features": {"notes": False, "events": False}`；`load_settings()` 做嵌套合并，只接受已知 flag，脏数据不会传染。
- `bootstrap()` 新增字段：`features`、`modules`（`describe()`）、`scheduler`（作业快照）、`paths.db`。前端目前不读这些键，属于纯增量。
- `Bridge` 新增两个只读方法：`list_modules()` → `{modules, cards, error}`；`scheduler_status()` → `{jobs, db, storage_error}`。
- 存储不可用时只设 `store_error` 并继续启动（`bootstrap().paths.db` 仍给出路径，`scheduler_status().storage_error` 给出原因）。

### 13.6 验证记录（2026-11-27 14:26，安装版）

- `python -m unittest discover -s tests -t . -v` → 14 passed，0.35s。
- 无 GUI 冒烟（`AppController` + 假 window）：features 正确、4 个游戏 1 个可用、`probe` 作业 1.2s 内跑 1 次、`refresh_once()` 得到 `LIVE` 快照、`cards()` 输出 `ONLINE 26.5K / MO 战斗集结 / PROGRESS 33%`。
- 安装版启动日志：`Single-instance mutex acquired=True` → `SQLite migrated from v0 to v1` → `SQLite ready … (schema v1)` → `Game registry: 4 game(s), 1 module(s) loaded` → `Scheduler started with 1 job(s)` → 约 8s 后首次战况抓取。
- 实测 `hd2.db`：`journal_mode=wal`、`user_version=1`、5 张表齐全；窗口仍为 380×160 @ (1524,856,1904,1016)。
- 构建产物与安装目录 exe md5 一致：`5f00825ba2b6373b933d56912d034ff0`（第二次构建，含 factory 契约收敛）。
- 调度复跑验证：首次抓取 14:27:46（警告超时），`cache/snapshot.json` 于 14:28:51 再次写入，间隔 ≈65s，说明 `war` 作业按 `poll_seconds` 自动重排。

### 13.7 明确未做

- 笔记 / 赛事 / 赛季业务逻辑与对应 Bridge 方法。
- 前端拆分（`core.js/notes.js/events.js`）、左栏第 3/4 个 tab、大窗导航项。
- 标题改中性名（`TACTICAL COMMAND // 战术终端`）——等 UI 改动一起做，避免只改一半。

