# 小胖庄园素材说明

## 小胖庄园人物原画

- [经典莓果原稿](xiaopang/player/character-sheet.png)、[小胖庄园主原稿](characters/sources/xiaopang.png)、[粉樱礼帽原稿](characters/sources/rose_mage.png)：均由用户提供，原始文件保持不变。
- 游戏改用 [characters](characters/) 下的规范化派生素材：36×48 帧、脚底锚点 (18,46)、8 列、1px 透明间隔；每个角色含三方向 idle / walk 共 18 帧，左向默认镜像右向。
- 转换做了最近邻缩放、逐动画行尺寸校准、超宽发型横向适配、24 色共享调色板与二值透明处理；去掉红色生成边缘和低透明度杂点，未作平滑或羽化。不是重新手绘的完整动作集。
- 可用 [转换脚本](../../tools/build_estate_characters.py) 重建。完整规则、运行时回退和后续扩展说明见 [皮肤系统文档](../../docs/estate-skins.md)。

## Kenney Tiny Farm

- 作者：Kenney
- 原始地址：https://kenney.nl/assets/tiny-farm
- 许可证：Creative Commons CC0 1.0 Universal
- 使用文件：`kenney/tiny-farm.png`（原包 `Tilemap/tilemap.png`）
- 修改情况：未修改原图；在 Canvas 中按整数或近整数倍缩放、裁切绘制。

## Kenney Tiny Town

- 作者：Kenney
- 原始地址：https://kenney.nl/assets/tiny-town
- 许可证：Creative Commons CC0 1.0 Universal
- 使用文件：`kenney/tiny-town.png`（原包 `Tilemap/tilemap.png`）
- 修改情况：未修改原图；在 Canvas 中按整数或近整数倍缩放、裁切绘制。

两个素材包的许可证文本分别见 `kenney/License-tiny-farm.txt` 与 `kenney/License-tiny-town.txt`。建筑主体、角色动画、水面、土地、交互反馈与界面仍由项目内 Canvas/CSS 程序绘制。

## 小胖庄园原创像素素材

- 作者：小胖庄园项目所有者（用户提供）
- 来源：`xiaopang-estate-pixel-assets.zip` 与 `xiaopang-priority-assets-reviewed.zip`
- 使用文件：`xiaopang/` 下的作物、鱼类、收藏品、矿物和鱼竿透明 PNG。
- 修改情况：游戏直接使用压缩包内的 `game-ready` 规范图；运行时仅进行最近邻缩放，没有重新生成或平滑处理。
- 未接入内容：人物整图及矿镐、鱼饵、矿格、炸弹和特效总览仅为参考图，未复制到运行时资源。
