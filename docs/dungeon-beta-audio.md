# 地下城 Beta 音效

本轮为 `dungeon-beta.html` 动作原型补上音效。**没有音频文件**：全部用 WebAudio 实时合成，因此不依赖美术交付，也不增加资源体积；日后要换成真实音频，见文末的交接说明。

## 两套入口与共享设置

音效沿用全站既有的 [game-audio.js](../../assets/js/game-audio.js)（游戏厅、庄园在用），**不新建第二套引擎、不新建第二份音量设置**。玩家在任一页面调整音量或静音，写的是同一份 `localStorage.gameAudioSettings`，地下城立即跟随。

- `assets/js/game-audio.js`：共享引擎。新增 `playActionSound(cue)` 与动作音效表。与回合制 `playCue` 的区别是**不打断正在播放的声音**、允许多声部叠加，并有 16 声部的安全上限。同时新增 `getGameAudioSettings` / `setGameAudioEnabled` / `setGameAudioVolume` / `unlockGameAudio` 四个设置接口。
- `assets/js/dungeon/dgn-beta-audio.js`：地下城适配层。负责把高频事件合并成合理次数、按需动态加载共享引擎、在没有 WebAudio 时静默降级。它**不在模块顶层 import 共享引擎**，因此可以脱离浏览器直接单测。
- `dungeon-beta.html`：标题栏的 🔊 按钮复用共享的音效设置弹窗（`#gameAudioSettingsModal`，与游戏厅同一套 DOM id 和交互）。静音或音量为 0 时按钮显示 🔇 并带上当前音量。

共享引擎不再 import `assets/js/core.js`：回合制提示音需要的“当前用户/是否观战”改由 `setGameAudioSession` 注入（见 `assets/js/main.js`）。这样独立页面也能直接复用引擎，不必先拉起游戏厅的大厅状态。

## 合成口径

一次挥砍命中多只敌人、同一帧拾取多份材料只会响一次：`dgn-beta-audio.js` 对 `swing` / `shoot` / `hit` / `kill` / `pickup` 做 45ms 窗口合并，`levelup`、`victory`、`craft` 等一次性提示音从不合并。数值与波形是本游戏配置，不是任何官方素材。

| 事件 | 音效 | 触发点 |
| --- | --- | --- |
| `waveStart` / `boss` / `levelup` / `death` | 开波、首领登场、升级、阵亡 | `dgn-beta-arena.js` |
| `swing` / `shoot` / `hit` / `kill` / `hurt` / `pickup` | 挥砍、射击、命中、击杀、受击、拾取 | `dgn-beta-arena.js` |
| `waveClear` / `victory` | 本波结束、通关（入口区分两者） | `dgn-beta-main.js` |
| `buy` / `reroll` / `select` / `craft` / `error` | 购买、重抽、选择、改造成功、操作失败 | `dgn-beta-shop.js` |

浏览器自动播放策略要求先有用户手势：点“进入遗迹”时会同时解锁音频。页面切到后台或静音时，已排定的声音立即停止；恢复前台后的下一次输入自动解锁。缺少 WebAudio 时全部调用变成空操作，玩法不受影响。

## 验证

```sh
python tests/test_frontend.py                     # 入口可达、import/导出、页面资源
node tests/test_dungeon_beta_audio.cjs            # 适配层合并/节流（不需要浏览器）
python scripts/preview_game.py --no-open --no-replace --port 8020
node tests/test_dungeon_beta_audio_ui.cjs         # 真实 WebAudio 输出、接线、静音持久化、降级
```

`test_dungeon_beta_audio_ui.cjs` 需要 Chrome；可用 `CHROME_PATH` 指定，用 `BETA_URL` 指向预览地址。它逐个播放 `DUNGEON_CUES` 里的每个音效，因此音效名在适配层与共享引擎之间写歪会直接失败。

## 换成真实音频的交接

合成音效只是原型的占位。要换成录音素材：在 `playActionSound` 的这一层按 `cue` 名到 `assets/dungeon/beta/audio/` 取文件播放，保留 `playActionSound` 的声部上限与 `dgn-beta-audio.js` 的合并口径即可，调用点（竞技场、商店、入口）不用改。当前没有音频文件，也没有 `<audio>` 标签；`scripts/preview_game.py` 的静态白名单已经允许 `.mp3/.wav/.ogg`。
