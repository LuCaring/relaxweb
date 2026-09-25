# 臭企鹅宠物素材

宠物 ID 为 `stinky_penguin`。`stinky-penguin-sheet.png` 是用户提供的透明原图（1122 × 1402），直接用于地图绘制，无需重新缩放保存。

图集为 4 列 × 5 行：前四行依次为下、左、右、上方向的四帧行走动画；第五行为四帧睡眠动画。运行时逐格裁切，使用最近邻缩放。地图绘制入口为 `drawPetAsset(ctx, pet, tick, "stinky_penguin")`。
