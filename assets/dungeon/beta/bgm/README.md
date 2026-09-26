# 地下城 Beta 背景音乐曲库

这个目录放 `dungeon-beta.html` 可选用的背景音乐。**音频文件本身不在版本库里**（见下方 `.gitignore`），只有 `library.json` 清单和本说明入库。

## 两种来源

| 来源 | 存放位置 | 是否入库 | 怎么加 |
| --- | --- | --- | --- |
| 自由许可曲目 | 本目录的音频文件 + `library.json` | 清单入库，音频文件本地 | 按下方"安装免费曲目"，或直接往 `library.json` 追加一条 |
| 你自己的曲目 | 浏览器 IndexedDB（游戏内面板点"从本地添加"） | 不入库、不上传 | 游戏内在背景音乐面板里选文件即可 |

自己加的文件只存在你自己浏览器里，不会进仓库、不会上传服务器、不会推到 GitHub。

## 安装免费曲目

清单里的 4 首都是 **Kevin MacLeod** 的作品，**CC BY 4.0**（可免费商用，必须署名）。下载到本目录即可被游戏识别：

```powershell
$base = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
$dest = "assets/dungeon/beta/bgm"
$files = @{
  "Ossuary 1 - A Beginning.mp3" = "ossuary-1-a-beginning.mp3"
  "Chee Zee Caves V2.mp3"       = "chee-zee-caves-v2.mp3"
  "8bit Dungeon Boss.mp3"       = "8bit-dungeon-boss.mp3"
  "Mystery Bazaar.mp3"          = "mystery-bazaar.mp3"
}
foreach ($pair in $files.GetEnumerator()) {
  Invoke-WebRequest -UseBasicParsing ($base + [uri]::EscapeDataString($pair.Key)) `
    -OutFile (Join-Path $dest $pair.Value)
}
```

或者不下载，直接用游戏内面板的"从本地添加曲目"挑你自己的音乐文件（那样连上面这一步都不需要）。

也可以直接在浏览器里打开 [incompetech 曲库](https://incompetech.com/music/royalty-free/music.html) 搜索曲名下载，然后按 `library.json` 里的 `file` 字段放到本目录。

**署名是硬性要求。** 游戏内背景音乐面板会显示每首曲目的作者与许可（它们来自 `library.json` 的 `author`/`license` 字段），所以只要不改这些字段，界面上就一直带着署名。若要商用且不想署名，需要向作者购买免署名授权。

## 追加曲目

在 `library.json` 的 `tracks` 数组里加一条：

```json
{
  "id": "唯一-id",
  "file": "assets/dungeon/beta/bgm/你的文件名.mp3",
  "title": "显示名",
  "scene": "explore | exploreAlt | boss | shop",
  "gain": 1.0,
  "desc": "一句话说明",
  "author": "作者",
  "license": "许可名",
  "licenseUrl": "许可链接",
  "source": "来源页"
}
```

- `scene` 决定"跟随场景自动切换"时它属于哪个场景；留空则该曲只能手动选。
- `gain` 是相对响度补偿，1.0 为基准，可在 0~2 之间微调（不同来源的母带响度差异很大）。
- `id` 前缀 `local:` 是保留给浏览器内曲库的，清单里不要用。
- 音频格式支持 mp3 / ogg / wav / m4a / flac / opus / webm。

## 其他可用的自由许可来源

- [incompetech](https://incompetech.com/music/royalty-free/music.html)：Kevin MacLeod，CC BY 4.0，约 2000 首，有[机器可读目录](https://incompetech.com/music/royalty-free/pieces.json)。
- [OpenGameArt](https://opengameart.org/)：逐条标注许可，筛选 CC0 的可免署名。
- [Kenney](https://kenney.nl/assets)：音频素材以 CC0 为主，偏音效与短过场。
- [Free Music Archive](https://freemusicarchive.org/)：许可混杂，注意避开 **NC**（禁止商用）条目。

选曲入库前请逐个确认许可可商用、并保留来源与署名信息。
