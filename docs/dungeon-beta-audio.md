# 地下城 Beta 音效与背景音乐

`dungeon-beta.html` 动作原型的声音分两块：**音效**全部用 WebAudio 实时合成，没有音频文件；**背景音乐**提供内置合成曲，并支持免费曲库与玩家自己的本地曲库。

## 音效

音效沿用全站既有的 [game-audio.js](../../assets/js/game-audio.js)（游戏厅、庄园在用），**不新建第二套引擎、不新建第二份音量设置**。玩家在任一页面调整音量或静音，写的是同一份 `localStorage.gameAudioSettings`，地下城立即跟随。

- `assets/js/game-audio.js`：共享引擎。新增 `playActionSound(cue)` 与动作音效表。与回合制 `playCue` 的区别是**不打断正在播放的声音**、允许多声部叠加，并有 16 声部的安全上限。同时提供 `getGameAudioSettings` / `setGameAudioEnabled` / `setGameAudioVolume` / `unlockGameAudio` / `getSharedAudioGraph` / `isGameAudioAudible`。
- `assets/js/dungeon/dgn-beta-audio.js`：地下城音效适配层。负责把高频事件合并成合理次数、按需动态加载共享引擎、在没有 WebAudio 时静默降级。它**不在模块顶层 import 共享引擎**，因此可以脱离浏览器直接单测。
- `dungeon-beta.html`：标题栏的 🔊 按钮复用共享的音效设置弹窗（`#gameAudioSettingsModal`，与游戏厅同一套 DOM id 和交互）。静音或音量为 0 时按钮显示 🔇 并带上当前音量。

共享引擎不再 import `assets/js/core.js`：回合制提示音需要的“当前用户/是否观战”改由 `setGameAudioSession` 注入（见 `assets/js/main.js`）。这样独立页面也能直接复用引擎，不必先拉起游戏厅的大厅状态。

### 合成口径

一次挥砍命中多只敌人、同一帧拾取多份材料只会响一次：`dgn-beta-audio.js` 对 `swing` / `shoot` / `hit` / `kill` / `pickup` 做 45ms 窗口合并，`levelup`、`victory`、`craft` 等一次性提示音从不合并。数值与波形是本游戏配置，不是任何官方素材。

| 事件 | 音效 | 触发点 |
| --- | --- | --- |
| `waveStart` / `boss` / `levelup` / `death` | 开波、首领登场、升级、阵亡 | `dgn-beta-arena.js` |
| `swing` / `shoot` / `hit` / `kill` / `hurt` / `pickup` | 挥砍、射击、命中、击杀、受击、拾取 | `dgn-beta-arena.js` |
| `waveClear` / `victory` | 本波结束、通关（入口区分两者） | `dgn-beta-main.js` |
| `buy` / `reroll` / `select` / `craft` / `error` | 购买、重抽、选择、改造成功、操作失败 | `dgn-beta-shop.js` |

浏览器自动播放策略要求先有用户手势：点“进入遗迹”时会同时解锁音频。页面切到后台或静音时，已排定的声音立即停止；恢复前台后的下一次输入自动解锁。缺少 WebAudio 时全部调用变成空操作，玩法不受影响。

## 背景音乐

标题栏的 🎵 按钮打开背景音乐面板：曲目单选、**独立的音乐音量**、是否跟随场景自动切换、自动切换用哪套曲源，以及“从本地添加曲目”。选择与音量存在 `localStorage.dungeonBetaBgm`，与游戏厅的音效设置互不干扰。

### 三层曲源

| 层 | 内容 | 是否入库 | 依赖 |
| --- | --- | --- | --- |
| 内置合成曲 | 遗迹回廊 / 幽深地窖 / 巨物来袭 / 篝火歇脚，四首可无缝循环的合成曲 | 代码即内容 | 无，离线可用 |
| 免费曲库 | `assets/dungeon/beta/bgm/library.json` 登记的自由许可曲目 | 只入库清单与说明，音频文件本地放置 | 曲目文件，见该目录 [README](../../assets/dungeon/beta/bgm/README.md) |
| 我的本地曲库 | 玩家在面板里挑选的自己机器上的音频 | 不入库、不上传，存在浏览器 IndexedDB | 无 |

`library.json` 里每首曲目都带 `author` / `license` / `licenseUrl` / `source` 字段，面板会把这些署名显示出来——使用 CC BY 素材时这是许可的硬性要求，改字段等于去掉署名。

### 实现

- `assets/js/dungeon/dgn-beta-bgm.js`：曲目数据、乐句生成与播放引擎。曲目数据与 `notesForStep()`（第 N 步该弹哪些音）是**纯函数**，可脱离浏览器单测；`sceneTrackForScene()` 同样纯函数，负责“场景 + 波次 → 曲目”。
- `assets/js/dungeon/dgn-beta-bgm-library.js`：曲库读写。清单走 `fetch`，玩家自己的曲目走 IndexedDB，元数据与音频字节分两个 store，列曲目时不会把几十 MB 音频读进内存。它只交原始字节，解码由引擎负责。
- `assets/js/dungeon/dgn-beta-bgm-ui.js`：面板界面。所有文本用 `textContent` 写入——本地曲目的文件名来自玩家设备，不能当 HTML 拼。

音乐链路是 `声源 → bgmGain → masterGain → destination`：全站静音与音效音量照样生效，而音乐音量由 `bgmGain` 独立控制。调度用「提前 0.45s 排期 + 110ms 轮询」的标准 WebAudio 做法；静音、切后台、上下文未解锁时立刻收声，条件恢复后自动续播。单音出错只丢弃该音，不会打断调度或把异常抛进定时器。

### 场景自动切换

`dgn-beta-main.js` 在开波、进商店、回到标题/结算时调用 `setBgmScene()`：普通波次用探索曲（按波次在「遗迹回廊」和「幽深地窖」间轮换），首领波（每 3 波）用「巨物来袭」，商店用「篝火歇脚」。关闭自动切换即可锁定一首。切换曲源时若该场景在曲库里没有对应曲目，会**回落到内置合成曲**，不会变成没声音。

## 验证

```sh
python tests/test_frontend.py                     # 入口可达、import/导出、页面资源
node tests/test_dungeon_beta_audio.cjs            # 音效合并/节流（不需要浏览器）
node tests/test_dungeon_beta_bgm.cjs              # 乐句可循环性、场景映射、清单一致性（不需要浏览器）
python scripts/preview_game.py --no-open --no-replace --port 8020
node tests/test_dungeon_beta_audio_ui.cjs         # 真实 WebAudio 输出、接线、静音持久化、降级
node tests/test_dungeon_beta_bgm_ui.cjs           # 音乐发声与音量链路、本地曲库增删与持久化、场景切换
```

三个浏览器测试都需要 Chrome；可用 `CHROME_PATH` 指定，用 `BETA_URL` 指向预览地址。`test_dungeon_beta_audio_ui.cjs` 会逐个播放 `DUNGEON_CUES` 里的每个音效，音效名在适配层与共享引擎之间写歪会直接失败；`test_dungeon_beta_bgm.cjs` 会校验 `library.json` 每条记录合法、署名齐全、场景角色覆盖，以及目录里出现的音频都必须登记在清单里。

免费曲库的播放断言在音频文件缺失时会自动跳过（文件按设计不入库），因此克隆后直接跑不会误报。

## 换成真实音频的交接

音效目前全是合成占位。要换成录音素材：在 `playActionSound` 这一层按 `cue` 名到 `assets/dungeon/beta/audio/` 取文件播放，保留它的声部上限与 `dgn-beta-audio.js` 的合并口径即可，调用点（竞技场、商店、入口）不用改。背景音乐这边换曲不用改代码：把文件放进 `assets/dungeon/beta/bgm/` 并在 `library.json` 追加一条记录，或在面板里直接添加本地曲目。

`scripts/preview_game.py` 的静态白名单已经允许 `.mp3/.wav/.ogg/.json`。
