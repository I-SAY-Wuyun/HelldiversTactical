# Helldivers Tactical Command — v2 功能扩展规划（备忘录 / 赛事 / 赛季倒计时）

> 状态：**设计稿，未写任何代码**。本文只做架构与可行性规划，供后续开发逐条落地。
> 2025-11-27 更新：按用户决策收敛范围（R6 + LoL 赛事 / OW2 + R6 赛季 / HD2 降为模块），见 0.1 与 2.0。
> 用户明确要求：**未获允许不得开工**——本文件是当前唯一交付物。
> 读者：后续接手的人（或 AI agent）。看完本文应能直接开工，不需要再翻聊天记录。

---

## 0. 目标 / 非目标

**目标**：把一个"《绝地潜兵 2》桌面战况挂件"扩展成"**多游戏桌面战术终端**"——**《绝地潜兵 2》从此只是程序里的一个战况模块，不再是产品本体**：

1. **游戏备忘录**：玩家自己记录"想玩 / 在玩 / 吃灰"的清单，可带提醒。
2. **赛事卡片**：接液体百科（Liquipedia），查询职业赛事与赛程，生成"下一场比赛"倒计时卡片。
3. **通行证 / 赛季倒计时**：多游戏赛季、通行证、活动倒计时，用于规划时间。

### 0.1 范围（2025-11-27 已确认）

| 用途 | 游戏 | 备注 |
|---|---|---|
| 赛事卡片 | **彩虹六号：围攻**（Liquipedia wiki `rainbowsix`）、**英雄联盟**（lolesports 赛程接口） | 均无需用户 key（实测见 4.2） |
| 赛季 / 通行证倒计时 | **守望先锋 2**、**彩虹六号：围攻** | **并入备忘录**（截止日期 + 倒计时 + 到期提醒），见 4.1 / 4.3 |
| 实时战况（现有能力） | 绝地潜兵 2 | 降级为「模块」，可在设置里关闭 |

暂不纳入：Apex / VALORANT / CS2 等其他游戏（预留扩展点，不做硬编码）。

**非目标（明确不做）**：
- 不做账号体系、不做云同步（数据全部本地）。
- 不做游戏内数据抓取/注入（不碰反作弊）。
- 不做赛事直播播放器（只存 URL，用系统浏览器打开）。
- 不引入需要付费的服务端，也不要求用户申请任何 API key。

---

## 1. 现状评估（这些事实决定了改造方式）

| 事实 | 位置 | 对规划的影响 |
|---|---|---|
| Python 3.11 + pywebview(WebView2)，PyInstaller `--onedir` | `build.ps1` / `HelldiversTactical.spec` | 新增代码尽量只用标准库，避免再打一轮依赖 |
| 后端 579 行 `app.py`：`AppController` + `TrayController` + `Bridge` 三个类，Bridge 上 18 个方法 | `app.py:77 / :300 / :353` | 新功能通过"加 Bridge 方法 + 加 job"接入，不动现有方法签名 |
| 前端 `web/js/app.js` 926 行，另有 `map.js` 571、`clock.js` 82、`style.css` 812 | `web/` | **单文件已到上限**，再加 3 个模块前必须先做模块拆分（M0） |
| 大量 `innerHTML += 模板字符串` 渲染（20+ 处） | `web/js/app.js` | 用户输入（备忘录内容）会被拼进 HTML → **必须改用 `textContent`/转义**，否则是 XSS/排版事故 |
| 存储只有 JSON：`atomic_write_json` + `read_json`（`hd2/util.py:140`） | `hd2/util.py` | 结构化数据（笔记、赛事缓存）需要 SQLite；settings.json 继续沿用 |
| 网络只有 `http_json(url, timeout, attempts)`，带固定 UA、无 ETag/无退避 | `hd2/api.py:65` | 需要新增"带缓存 + 限流 + 退避"的抓取层，不能直接复用 |
| 轮询线程 `AppController._loop` 固定 `poll_seconds`（默认 60s），一次抓全量战况 | `app.py:228` | 需要改成多任务调度器（备忘录提醒、赛事刷新、赛季刷新频率完全不同） |
| 时间口径：内部全 UTC，`util.iso/parse_dt`；无 `tzdata` 依赖 | `hd2/util.py:27-58` | 见 D3：展示一律换算北京时间，不被系统时区影响 |
| 单实例互斥量 `Local\\HD2TacticalCommand-v1-8D4B21`；托盘 pystray 0.19.5 已可用 | `hd2/winflash.py` / `app.py:300` | 提醒可用托盘气泡，不必引入 winrt |
| 小窗 380×160（最小 340×140），内容区 342×126，左栏 36px | `app.py:58` / `web/css/style.css` | **最大的硬约束**：导轨最多再挤 2 个标签（见 4.4） |

---

## 2. 总体架构

```
┌──────────────────────────── WebView2 (前端) ────────────────────────────┐
│  core.js (state / api / toast)  │  widget.js  │  notes.js  events.js     │
│  ───────────────────────────────┼─────────────┼──────────────────────    │
│  大窗 dashboard: COMMAND | MAP | INTEL | CHRONO | NOTES | EVENTS          │
└───────────────▲──────────────────────────────────────────┬──────────────┘
                │ window.HD2.receive({type, data})          │ pywebview.api.<method>()
┌───────────────┴──────────────────────────────────────────▼──────────────┐
│ app.py  Bridge（薄层：参数校验 → 调 service → 返回 dict）                 │
├─────────────────────────────────────────────────────────────────────────┤
│ services/ (新增)                                                        │
│  notes.py      CRUD + 倒计时 / 提醒（赛季 = 笔记）                      │
│  events.py     关注列表 + 赛程聚合 + 倒计时                             │
│  scheduler.py  单线程 tick(1s) + 任务注册 + 指数退避                    │
│  store.py      SQLite 连接、schema 迁移、事务                           │
│  providers/    lolesports.py / liquipedia.py                           │
├─────────────────────────────────────────────────────────────────────────┤
│ 既有：api.py（HD2 战况）、alerts.py、winflash.py、config.py、util.py      │
└─────────────────────────────────────────────────────────────────────────┘
```

**数据流（以赛事倒计时为例）**：`scheduler` 到点 → `providers.liquipedia.fetch(`关注列表`)`（限流：全局 1 req/2s）→ 归一化成 `Match` → 写 `matches` 表 + `cache` 表 → `window.evaluate_js("window.HD2.receive({type:'events'})")` → 前端只渲染，不请求网络。

**为什么后端代理**：Liquipedia 没有 CORS，前端 `fetch` 必然失败；API key 也不能放前端；限流必须在唯一出口处集中执行。

### 2.0 平台化：HD2 从"核心"降为"模块"（本轮最重要的结构调整）

现状是"一个游戏 + 一堆功能"，代码里到处是 HD2 的字段（`war`/`planets`/`MO`/`faction`）。要变成"多游戏终端"，**不做大爆炸重写**，而是加一层"模块注册表 + 适配器"：

```
data/games.json                       # 游戏注册表（数据驱动，不写死）
[{"id":"hd2","name":"Helldivers 2","cn":"绝地潜兵 2","kind":["war"],
  "module":"hd2","enabled":true,"accent":"#ffe81a"},
 {"id":"r6","name":"Rainbow Six Siege","cn":"彩虹六号：围攻","kind":["esports","season"],
  "liquipedia":"rainbowsix","enabled":true},
 {"id":"lol","name":"League of Legends","cn":"英雄联盟","kind":["esports"],
  "liquipedia":"leagueoflegends"},
 {"id":"ow2","name":"Overwatch 2","cn":"守望先锋 2","kind":["season"],"enabled":true}]

hd2/modules/registry.py               # 读 games.json → 可用模块列表
hd2/modules/base.py                   # Protocol: id / enabled / widgets() / refresh(ctx) / bootstrap()
hd2/modules/hd2_mod.py                # 适配器：包装现有 api.LiveDataService，字段不改
```

要点：

- **HD2 模块 = 适配器**，`hd2/api.py` 这轮**不动**（905 行，动它风险最大）；适配器只负责"把 `LiveDataService.fetch()` 的结果转成统一的 `widget_card()`"。
- **关闭模块即停止抓取**：`enabled=false` 时 `scheduler` 不注册对应 job，省流量也省 token。
- **小窗 rail 只有 4 个位置**，且都是"功能"维度而不是"游戏"维度：`时间 / 日程 / 备忘 / 战况`。「战况」当前只有 HD2 一个模块；将来若接入第二个有实时数据的游戏，就在该面板内用分段控件切，而不是加 rail tab。
- **产品命名**：界面标题改为中性的 `TACTICAL COMMAND // 战术终端`，但 **`APP_ID`/`APP_NAME`/`%LOCALAPPDATA%\\HelldiversTactical`/互斥量/安装目录先保持不变**——改名要迁移用户数据（settings.json、日志、缓存、webview 存储），属于独立风险项，放到 M4 单独评估（见 D9）。用户感知的是标题，不是文件夹名。

### 2.1 新增文件清单（预估）

| 文件 | 职责 | 约行数 |
|---|---|---|
| `hd2/store.py` | SQLite 连接、`PRAGMA journal_mode=WAL`、schema v1、迁移函数 | 180 |
| `hd2/notes.py` | 备忘录 CRUD、标签、状态、提醒队列 | 160 |
| `hd2/events.py` | 关注管理、赛程合并/去重、倒计时计算 | 220 |
| `hd2/scheduler.py` | 任务表 + tick 循环 + 退避 + 单线程执行 | 140 |
| `hd2/providers/lolesports.py` | lolesports 赛程接口、联赛/队伍映射、中文名归一 | 180 |
| `hd2/providers/liquipedia.py` | R6 赛事页解析（data-timestamp）、限流器 | 200 |
| `web/js/core.js` / `notes.js` / `events.js` | 前端模块 | 250 / 240 / 260 |
| `hd2/modules/registry.py` + `base.py` + `hd2_mod.py` | 游戏模块注册与 HD2 适配器 | 60 / 40 / 120 |
| `data/games.json` | 游戏注册表（驱动 rail、pack、wiki 列表） | 数据 |
| `data/i18n/esports-zh.json` | 联赛 / 队伍中文叫法映射 | 数据 |
| `docs/ROADMAP-v2.md` | 本文 | — |

---

## 3. 共性地基（M0，必须先做）

### 3.1 存储：SQLite（stdlib `sqlite3`）

放在 `USER_DIR / "hd2.db"`（`config.py` 的 `USER_DIR` 已有"不可写则回退 TEMP"逻辑，直接复用）。

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);           -- schema_version 等
CREATE TABLE notes (
  id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'todo',
  game TEXT, due_utc TEXT, pinned INTEGER NOT NULL DEFAULT 0,
  created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE matches (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, game TEXT, tournament TEXT, series TEXT,
  team_a TEXT, team_b TEXT, start_utc TEXT, end_utc TEXT, stream TEXT, format TEXT,
  payload TEXT, fetched_utc TEXT
);
CREATE TABLE follows (
id TEXT PRIMARY KEY, kind TEXT NOT NULL,      -- 'team' | 'league' | 'match'
  provider TEXT NOT NULL, ref TEXT NOT NULL, label TEXT, added_utc TEXT NOT NULL,
  UNIQUE(kind, provider, ref)
);
CREATE TABLE http_cache (
  url TEXT PRIMARY KEY, etag TEXT, body TEXT, status INTEGER, fetched_utc TEXT, ttl_sec INTEGER
);
```

规则：
- 所有连接在主线程创建、`check_same_thread=False` + 一把 `threading.Lock`（当前架构是"一个循环线程 + Bridge 调用线程"，写入频率极低，够用）。
- 所有时间列存 **UTC ISO8601**（复用 `util.iso/parse_dt`），显示层再转本地。
- 软删除（`deleted=1`）+ 每次启动 `VACUUM` 之外不做清理，笔记数据量太小。
- settings.json 保持原样，**不迁移**（避免动 `load_settings` 的白名单逻辑，减少回归面）。

### 3.2 调度器

现状：`_loop()` 里 `time.sleep(poll_seconds)` 抓战况。改造为：

```python
class Scheduler:
    def __init__(self): self._jobs = []; self._stop = threading.Event()
    def add(self, name, interval, fn, *, run_now=False, backoff=(30,300)): ...
    def run(self):                      # 单线程，1s tick
        while not stop:
            for job in due_jobs(): safe_call(job)   # 异常→记录+退避，绝不退出线程
```

注册的任务：

| 任务 | 间隔 | 说明 |
|---|---|---|
| `war`（既有战况） | `poll_seconds`（60） | 原 `_loop` 逻辑搬进来，行为不变 |
| `notes_reminders` | 30s | 只查 DB（本地），零网络 |
| `events` | 仅手动 | 只在按 REFRESH 时执行（见 4.2），不开后台轮询 |

UI 线程安全：任务只改内存/DB，然后 `controller._push(payload)`；`evaluate_js` 必须在主线程（沿用现有 `_push` 的做法，它已经处理）。

### 3.3 HTTP 层（新增 `http_cached`）

```python
def http_cached(url, *, ttl, headers=None, timeout=10) -> tuple[dict|None, str]
    # 1) 命中 http_cache 且未过期 → 直接返回（网络全断也能看）
    # 2) 带 ETag/If-None-Match；304 → 刷新 fetched_utc
    # 3) 429/503 → 读 Retry-After，写回退时间，本次返回旧缓存
    # 4) 所有出站请求经过全局 RateLimiter（见难点 D1）
```

### 3.4 通知

- **应用内**：现有 `toast()` + 大窗 `#alert-log` 复用。
- **系统级**：pystray 图标 `notify(title, message)`（Windows 走 Shell_NotifyIcon 气泡），无新依赖。
- 策略：备忘录提醒 = 到点气泡 + 窗口内横幅（若窗口被移到屏幕边缘也不丢）；赛事提醒 = T-15min / T-0 各一次；同 id 只提醒一次（记在 `meta` 表）。

---

## 4. 三个功能的设计

### 4.1 游戏备忘录（Notes）

**用户故事**：我刷到一款想玩的游戏，30 秒内记下来，标"吃灰"；周末打开挂件能看到"该玩了"的清单；重要条目到点提醒我。

**数据模型**：`notes` 表 + `tags`（JSON 数组字符串，够用，不做多对多表）。

**状态机**：`todo`（想玩）→ `playing`（在玩）→ `shelved`（吃灰）→ `done`（通关/弃坑）。`shelved` 支持 `snooze_until`（"下个月再看"）。

**UI**：
- 小窗：新 tab「备忘」→ 显示置顶 1 条 + 计数（`3 TODO · 1 到点`）；点条目跳大窗。
- 大窗 NOTES 页：列表（按状态分组/标签筛选/搜索）+ 右侧编辑器（标题、正文、标签、状态、提醒时间、关联游戏）。
- 快捷键：`N` 新建，`Ctrl+Enter` 保存。


**带倒计时（赛季就用它）**：

- 任何笔记都能填「截止日期」，卡片显示 `剩 12 天` / `已过期 3 天`。
- 赛季不是独立功能，就是一条带截止日期的笔记（见 4.3）：`OW2 S19`、`R6 Y10S4`。
- 到期前 7/3/1 天托盘提醒；到期后转「该更新了」，一键顺延（OW2 +63 天 / R6 +91 天）。

**Bridge**：`notes_list(filter)` / `notes_get(id)` / `notes_save(note)` / `notes_delete(id)` / `notes_set_status(id, status)` / `notes_snooze(id, minutes)`。

**验收**：新增/编辑/删除/搜索在 1000 条数据下 <100ms；重启后数据在；提醒到点弹一次且只弹一次；正文里的 `<script>` 不执行（`textContent` 渲染）。

**难点**：无外部依赖，主要是 UI 工作量 + 富文本/换行的取舍（建议纯文本 + 简单 markdown 子集，不做 WYSIWYG）。

---

### 4.2 赛事卡片（无 key 方案：LoL 官方赛程 API + R6 Liquipedia）

**用户故事**：我关注 LPL 和 T1，挂件上显示「下一场 BLG vs EDG · 北京时间 02-08 19:00 · 还有 1天 03:12:44」；也可以只钉住某一场。

**实测结论（2026-11-27 本机 Node fetch 实测，全部不需要用户申请 key）**：

| 游戏 | 数据源 | 实测结果 | 结论 |
|---|---|---|---|
| 英雄联盟 | `esports-api.lolesports.com/persisted/gw/{getLeagues,getSchedule,getEventDetails}?hl=zh-CN`，请求头 `x-api-key: <网页版内置常量>` | 200；`getLeagues` 返回 50 个联赛，`region` 已是中文（中国大陆赛区 / 韩国赛区 / EMEA赛区）；`getSchedule?leagueId=` 可只取一个联赛；`blockName` 是中文（第 1 周 / 瑞士轮 / 淘汰赛）；队伍只给 `code`（JDG / BLG / T1） | **采用**；key 是 lolesports 网页端自带的公开常量，不是用户 key |
| 彩虹六号 | `liquipedia.net/rainbowsix/api.php?action=parse&format=json&page=Six%20Invitational/2026&prop=text` | 200；渲染后的 HTML 每场都带 `<span class="timer-object" data-timestamp="1770544800">`（Unix 秒），52 场全部有时间戳 | **采用**（读 data-timestamp 比解析 wikitext 稳） |
| 彩虹六号（备） | 同页 `action=query&prop=revisions&rvprop=content` 取 wikitext | 含 `|sdate=2026-02-02` / `|edate=2026-02-15`，以及 `{{Match |date=2026-02-08 - 13:15{{Abbr/CET}} |opponent1={{TeamOpponent|M80}}}}` | 备选：要自己处理时区缩写，不如 HTML |
| 直接抓维基 HTML | `liquipedia.net/rainbowsix/...` | 实测被反爬拦（返回 Are You Human? 挑战页） | **禁止**，只允许走 `api.php` |

**关键决定：不申请、也不需要 Liquipedia API key。** 赛事功能的联网入口只有上面两条；lolesports 侧是内置常量（设置项可覆盖）。整个流程没有“请填写 key”这一步，也不会因为没 key 而停用。

**交互（按你的要求重定）**

1. **关注目标三种，用户自选**（`follows` 表）：
   - `league` 联赛/赛事：LoL 存 `leagueId`（LPL = `98767991314006698`、LCK = `98767991310872058`、LEC = `98767991302996019`、LCS = `98767991299243165`、Worlds = `98767975604431411`、MSI = `98767991325878492`）；R6 存赛事页名（如 `Six Invitational/2026`）。
   - `team` 队伍：LoL 存队伍 code（`T1` / `BLG`）；R6 存 `TeamOpponent` 全名（如 `Spacestation Gaming`）。
   - `match` 单场：钉住一场（LoL 用 `match.id`；R6 用赛事页 + 场次序号）。
2. **只在按刷新时联网**：赛事面板右上角 `REFRESH`。`scheduler` 里的 `events` job 默认 **disabled**，不后台轮询；倒计时靠本地时钟每秒 tick，零网络请求。
3. **一律北京时间**：入库统一 `start_utc`（UTC），渲染转 UTC+8；卡片显示「北京时间 02-08 19:00」+「还有 1天 03:12:44」，满 24h 用“天”，不足一天用 HH:MM:SS。
4. **中文互联网常规叫法**（`data/i18n/esports-zh.json`）：
   - 联赛：`lpl→LPL`、`lck→LCK`、`lec→LEC`、`worlds→S赛`、`msi→MSI`、`first_stand→全球先锋赛`
   - R6 赛事：`Six Invitational→SI 邀请赛`、`Six Major→Major`、`Six Invitational/2026→2026 SI`
   - 队伍：优先简称（`Spacestation Gaming→SSG`、`Team BDS→BDS`、`G2 Esports→G2`、`Natus Vincere→NAVI`）；LoL 直接用 API 的 `code`（`BLG` / `T1`，正好是国内常用写法）
   - 未命中显示原文，**不猜、不机翻**

**一次刷新的请求预算**

| 来源 | 请求数 | 间隔 |
|---|---|---|
| lolesports | 每个关注联赛 1 次，单次刷新上限 5 | ≥1s |
| Liquipedia | 每个关注赛事页 1 次，单次刷新上限 3 | **≥2s（ToS 硬上限）** |

超预算的目标排队到下一次刷新；面板底部显示「已刷新 13:32 · 4 次请求」。

**数据流**
```
refresh_events() → providers/lolesports.py | providers/liquipedia.py
  → 归一化 → matches 表（game, source, league, block, team1, team2, start_utc, state, url）
  → 前端按 follows 过滤，取“下一个”+ 未来 7 天列表
```

**UI**
- 小窗「赛事」面板：一张“下一个”卡片（`BLG vs EDG` / 北京时间 / 倒计时 / BO3 / 赛事名）+ 未来 7 天的紧凑列表；底部 `已关注 3 · 刷新于 13:32`。赛季不在这里，它是备忘（见 4.3）。
- 大窗 EVENTS 页：三个关注 tab（联赛 / 队伍 / 比赛）+ 未来 7 天列表 + 每场「打开直播 / 加入日历」。
- 署名：LoL 卡片标 `Data from lolesports.com`，R6 卡片标 `Data from Liquipedia.net · CC-BY-SA` 并链回来源页。

**验收**
- 断网或刷新失败：显示上次数据 + `STALE`，绝不出现负数倒计时。
- 点刷新到出数据 < 3s（4 个目标）；连点只发一轮请求。
- R6 任意两次请求间隔 ≥2s（可断言）；LoL 一次刷新请求数 ≤ 关注联赛数。
- 倒计时与系统时钟误差 < 1s；KST / CET / CST 的场次全部正确换算成北京时间。

**难点**：D1（无 key 源稳定性）、D2（模型归一）、D3（时区）、D10（LoL 体量）、D11（中文名与赛事页清单维护）。

---

### 4.3 赛季倒计时：并入备忘录（2025-11-27 决策变更）

**决策**：取消独立的 `seasons` 功能、表和页面，**赛季 = 一条带截止日期的备忘录**。理由：

- 赛季数据没有稳定自动源（见下表），做成独立功能只会换来一堆维护代码和一个永远过期的内置 pack。
- 成本从「新表 + 新页面 + pack 格式 + 自动更新机制」降到「一个日期字段 + 一个倒计时」。
- 用户本来就要记「通行证还剩几天」这种临时事，它和「想玩的游戏」本来就是同一种东西。

**自动源实测（结论：没有更好的办法）**

| 候选 | 实测 | 结论 |
|---|---|---|
| Blizzard 官方 | `overwatch.blizzard.com/en-us/season/` 302 回首页，首页里只有无关的 Season 5 字样 | ❌ |
| Ubisoft 官方 | `.../siege/battle-pass` 软 404（返回整站导航页，正文只有页脚残留的 Y9S2） | ❌ |
| Liquipedia OW / R6 | 只有赛事页，没有赛季页（`Season 19` / `Y10S4` / `Year 10 Season` 全落空） | ❌ |
| Fandom / Wikipedia | 本机 fetch failed，且不保证覆盖未来的结束日 | ⚠️ |

所以「自动取赛季结束日」这件事，**没有免费且稳定的源**；硬做只是把失效时间往后推几个月，仍然要人兜底。

**做法（并入备忘录后只需要这些）**

1. 笔记多一个可选字段 `due_utc`：卡片显示 `剩 12 天` / `已过期 3 天`，小窗「备忘」面板顶部固定显示最近到期的 1–2 条。
2. 提醒：到期前 7 / 3 / 1 天各一次托盘气泡；已弹状态持久化（`reminded_at`），重启不重弹。
3. **防遗忘（替代自动同步）**：到期条目不消失，转成「已过期 · 该更新了」卡片 + 两个按钮——
   - 「下一个赛季」：按周期一键顺延（OW2 **+63 天**、R6 **+91 天**，即实测的 9 周 / 13 周），二次确认后写入。
   - 「手动修改」：改日期。
4. 新建时给两条快捷模板：「守望先锋赛季」「R6 赛季」，预填 `OW2 Sx` / `R6 YxSy` 与 +63/+91 天，用户改个数字就行。

**验收**：到期条目不静默消失；顺延 ≤2 次点击；提醒只弹一次（重启不重复）；改系统时间不产生重复提醒。

**难点**：D5（提醒与过期处理）。

---

### 4.4 UI 空间方案（必须先定，否则前端返工）

现状：小窗内容区 **342×126**，rail 36px，两个 tab 各 38px。三功能要加位。

| 方案 | 做法 | 代价 | 评价 |
|---|---|---|---|
| **A. 图标化 rail + 4 tab** | tab 高度 38→24px、图标 12px + 悬浮 tooltip；`时间 / MO / 备忘 / 赛事` | 点击目标变小 | **推荐**：不动窗口尺寸，用户已认可的 380×160 不变 |
| B. 加高窗口到 380×190 | 5 个 tab 各 26px | 用户要重排桌面、与"紧凑"诉求冲突 | 备选 |
| C. 二级 segmented | rail 保持 2 个，内容区里再分段 | 层级深，小窗空间依旧 | 不推荐 |
| D. 轮播（tab 滚动） | rail 可滚动 | 隐藏功能，可发现性差 | 不推荐 |

**推荐组合：A**：rail 4 个图标 tab（时间 / MO / 备忘 / 赛事）；不再做二级 segmented（赛季已并入备忘，见 4.3）。视觉沿用现有 `.widget-tab.is-active` 的黄条 + 底色。

大窗侧边导航从 4 项扩到 6 项（COMMAND / MAP / INTEL / CHRONO / NOTES / EVENTS），沿用现有 tab 机制即可。

---

## 5. 难点清单（按严重度）

### D1. 无 key 数据源的稳定性（高）

- **R6 只有 Liquipedia `api.php` 一条路**：全站 ≤1 请求/2s；`action=parse` 一页 HTML 实测 **1,816,603 字节**（SI2026），必须只取 `data-timestamp` 与队伍名、不整页入库，解析失败要能退回缓存。
- **R6 没有“全部赛事”的稳定入口**：只能靠 `S-Tier Tournaments` 页 + 用户手动关注的赛事页；赛事改名/归档会让关注失效 → 关注项必须可编辑、可删除，失效时标红而不是静默消失。
- **lolesports 的 key 是网页端常量**：可能随前端发版轮换（多年未变）→ 常量放配置、设置里可覆盖；401/403 时提示“赛程接口变了”并降级为缓存，不反复重试。
- **反爬**：`liquipedia.net` 普通 HTML 页面有人机验证（实测返回 Are You Human? 挑战页），**不要写 HTML 抓取器**，只允许 `api.php`。
- **合规**：署名 + 链回 + 非商用（CC-BY-SA）；请求头必须带能识别应用与联系方式的 UA。

### D2. 赛事数据模型不统一（高）

不同 wiki 的 `match` 返回字段差异（`opponent1/opponent2` vs `team1/team2`、`date` vs `dateexact`、BO 格式在 `extradata`），TBD 赛程大量存在（`date` 为空 → 不能倒计时，只能显示"待定"）。对策：
- 归一化层 + 单测固定 3~5 份真实响应样本（脱敏存档 `tests/fixtures/liquipedia/*.json`）。
- `start_utc` 为空 = 未定，UI 显示"待定"，**不要用抓取时间兜底**。

### D3. 时区（低）

- 内部只存 UTC，展示**一律北京时间（UTC+8）**：`datetime.astimezone(timezone(timedelta(hours=8)))`。不需要 IANA 数据库，所以**也不用打 tzdata**（省掉一个 PyInstaller 坑）。
- 但两个源的时间口径不同，解析层必须统一：lolesports 给 UTC 字符串，Liquipedia 的 `data-timestamp` 是 Unix 秒（天然 UTC）——**先落 `start_utc` 再换算，解析层不做任何本地化**。
- 倒计时格式化统一一个函数：≥24h 用「还有 1天 03:12:44」，<24h 用「还有 03:12:44」，避免"还剩 27 小时"这种歧义。

### D4. 网络不可用 / 长尾失败（中）

- 429、Cloudflare 5xx、DNS 失败、公司代理：都要退化到缓存 + 角标 `STALE`，且**不能每 10 分钟重试到被封**（指数退避：30s → 2m → 10m → 30m，上限）。
- 时钟睡眠唤醒后，错过的事件（比赛已开赛 3 小时）不能补弹一堆通知；策略：`now - start > 6h` 静默丢弃，只记进历史。

### D5. 提醒与过期处理（中）

- 到期提醒最容易变成噪音：必须「只弹一次」并持久化已弹状态（`notes.reminded_at`），重启 / 换机器不能重弹。
- 用户几天不看挂件时提醒会堆积：同一时刻合并成一条（「3 条到期」），不要连弹 3 次。
- 过期条目不删除，转为「该更新了」卡片——这是赛季手动维护能成立的唯一原因；做不好就退化成「忘了填就永远显示过期」。

### D6. 存储与打包（中低）

- SQLite 在 `--onedir` 里没有额外二进制（stdlib 自带 `_sqlite3`），但 PyInstaller 需要确认 `sqlite3` 被收进（一般自动）。
- WAL 模式会生成 `hd2.db-wal/-shm`；杀进程后残留属正常，下次打开自动恢复。
- 用户数据目录已有"不可写则回退 TEMP"逻辑，DB 也走同一目录，注意**回退后数据会"消失"**（换目录）——在 UI 上显示当前数据目录，避免用户困惑。

### D7. 前端可维护性（中）

- `app.js` 926 行 + 20 处 `innerHTML` 拼接。直接再加 3 个模块，会变成 2000 行单文件。
- 建议 M0 就拆：`core.js`（state/api/toast/mqtt? 无）→ `widget.js`（小窗 + rail）→ `notes.js` / `events.js`（大窗页面）。用 `<script type="module">`（页面由本地 HTTP 服务提供，ES module 可用）。
- 用户输入一律走 `textContent`，表格渲染用 `document.createElement` 或统一 `esc()` 转义函数。

### D8. 功能蔓延（中）

三个功能都"看起来小"，加起来是：DB + 调度 + 两个外部数据源 + 4 个新页面 + 新通知路径。**必须按里程碑分批发版**，每批都能独立回滚（新功能用 settings 开关 `features.notes/events`，默认关）。

---

### D9. 产品化/命名与模块边界（本轮新增，中高）

- 现在 `APP_ID`、`APP_NAME`、安装目录、互斥量、用户数据目录全都叫 Helldivers；"HD2 只是模块"意味着**迟早要改名**。改名的真实代价：用户设置、日志、缓存、WebView2 存储（含 `localStorage` 里的 tab/主题选择）都在旧目录，硬改 = 用户丢配置。
- 建议：**1.x 只改界面标题**（`TACTICAL COMMAND // 战术终端`），路径不动；2.0 再做"复制旧目录 → 新 ID → 写迁移标记"的迁移。本文档按前者规划。
- 模块边界的诱惑：很容易顺手把 `api.py` 拆开重写。**不要**——它 905 行且字段被前端 20 多处消费，M0 只加适配器，不动实现。

### D10. LoL 赛程体量与多时区（本轮新增，中高）

- LoL 一天几十场、横跨 KST/CET/CN/BR 时区，且 `date` 字段精度只到"天 + UTC 时间"，联赛经常临时改期。
- 若按"关注队伍 → 拉全部比赛"实现，一次刷新就是上百条，且每次都要分页。必须：**默认只显示未来 7 天 + 关注的联赛/队伍**，tier 过滤在服务端参数里做（`max_tier`），不要拉回来再过滤。
- R6 相反：场次少，但赛事跨度长（Six Invitational 持续两周），卡片要能显示"赛程中"而不是只显示"下一场"。

### D11. 中文名与赛事页清单的维护（中低）

- 中文叫法要**人工判断**，不是翻译任务：`worlds→S赛`、`first_stand→全球先锋赛`、`Six Invitational→SI 邀请赛`、`Spacestation Gaming→SSG`；映射错比显示英文更糟。
- 对策：映射表随版本发布，只覆盖常看的几十个；未命中一律显示原文；放 `data/i18n/esports-zh.json`，界面提供“重命名”。
- R6 赛事页清单同理：内置一份本年度主要赛事（SI / 两个 Major / 各地区联赛），其余靠用户搜索添加。

## 6. 里程碑

| 里程碑 | 内容 | 交付物 | 预估 |
|---|---|---|---|
| **M0 地基 + 平台化** | 前端模块拆分、`store.py` + schema、`scheduler.py`、`http_cached` + RateLimiter、**`games.json` + 模块注册表 + HD2 适配器**、功能开关、标题改中性名 | 现有功能零回归（战况/置顶/拖动/时钟全过一遍）；关掉 HD2 模块后程序仍可运行 | 1.5 天 |
| **M1 备忘录（含倒计时 / 提醒 / 赛季）** | notes service + `due_utc` 倒计时 + 7/3/1 天提醒 + 过期顺延 + 小窗第 3 个 tab + 大窗 NOTES 页 | 可独立发版；赛季不再单列功能 | 2–2.5 天 |
| **M2 赛事（R6 + LoL，无 key）** | lolesports + Liquipedia 两个 provider、限流、关注管理（联赛/队伍/单场）、手动刷新、北京时间倒计时、中文名映射、署名 | 无 key 也能跑；断网显示缓存 | 3–4 天 |
| **M3 打磨** | 通知聚合、导出备份、文档 / HANDOVER 更新、安装包升级、（可选）改名与数据迁移评估 | 1.1.0 / 1.2.0 发布 | 1.5 天 |

合计约 **8–9 人日**（含平台化）。**1.1.0 = M0+M1**（零外部依赖，先把多游戏终端 + 备忘录 + 赛季倒计时立起来，一周内可发）；**1.2.0 = M2+M3**（赛事要联网，单独一版，风险隔离）。

---

## 7. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Liquipedia 收紧 ToS / 需商业授权 | 中 | R6 赛事不可用 | 数据层抽象，换源不换 UI；保底可手动钉比赛 |
| lolesports 内置常量轮换 | 中 | LoL 赛程 401 | 常量放配置可覆盖；失败降级缓存并提示 |
| 赛季日期忘记更新 | 高 | 倒计时错误 | 到期卡片「该更新了」+ 一键顺延；不自动猜 |
| SQLite 被杀软锁 | 低 | 启动失败 | 重试 + 回退到 TEMP 目录 + 明确报错 |
| 小窗空间不足导致返工 | 中 | 前端重写 | M0 先定 4.4 方案（建议 A）再动 UI |
| 打包遗漏 sqlite3 | 中 | 运行期崩 | 在 build.ps1 加"启动冒烟测试"步骤 |

---

## 8. 待拍板的决策（需要你确认）

**已定（2025-11-27）**：

- 赛事：**彩虹六号：围攻 + 英雄联盟**。
- 赛季：**并入备忘录**（截止日期 + 倒计时 + 到期提醒），不单列功能。
- 定位：**HD2 是模块，不是核心**；1.x 只改界面标题，目录 / APP_ID 不动。
- 数据源：**不需要你申请任何 key**——LoL 走 lolesports 公开赛程接口，R6 走 Liquipedia `api.php`（见 4.2 实测）。
- 刷新方式：**只在按刷新键时联网**，不做后台轮询。
- 时间：**一律北京时间**（UTC+8），倒计时用本地时钟。
- 名称：**中文互联网常规叫法**（映射表，未命中显示原文）。
- 关注粒度：**用户自选**，支持 联赛 / 赛事、队伍、单场比赛 三种。

**仍待你确认**：

1. **默认关注哪些**：LoL 默认 LPL + LCK + S赛？R6 跟 SI / Major 还是某个地区联赛、跟哪些队？
2. **提醒形式**：托盘气泡够用，还是要独立弹窗（需要额外做无边框窗口）？
3. **窗口尺寸**：保持 380×160（方案 A），还是加高到 190 放 5 个 tab（方案 B）？
4. **备份 / 导出**：备忘录要不要一键导出 JSON / Markdown？
5. **英文名**：R6 队伍名除映射表里的简称外，保留英文原名可以吗？

## 9. 下一步（**等你说开工之后**才执行）

1. 定稿 4.4 UI 方案 + 第 8 节决策 → 写进本文档。
2. M0：拆 `app.js` → `core.js/widget.js`，加 `store.py`（只建表不写业务）+ `scheduler.py`（把现有 60s 轮询搬进去），跑一遍现有功能回归。
3. M1：备忘录 CRUD + 小窗 tab（先只读展示，后加编辑）。
4. 每完成一个里程碑：更新 `HANDOVER.md` 的架构章节 + 本文档的"已完成"标记。