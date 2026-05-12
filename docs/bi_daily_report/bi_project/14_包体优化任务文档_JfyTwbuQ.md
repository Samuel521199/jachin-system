## 同步元数据

```json
{
  "title": "包体优化任务文档",
  "node_token": "JfyTwbuQvijuAUkYZ5QlpQPighh",
  "obj_type": "bitable",
  "obj_token": "P9CEb53hbaUNrNsb0o4lsdCIgNh",
  "space_id": "7511668436745863206",
  "source": "child_of:B19Iww8tBiXZqfky1hhlIZ6kg0P",
  "depth": 1,
  "seed_url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpI8lyYw"
}
```

---

# 包体优化任务文档

## 节点信息

- obj_type: `bitable`
- obj_token: `P9CEb53hbaUNrNsb0o4lsdCIgNh`
- space_id: `7511668436745863206`
- has_child: `True`

# 多维表格 包体优化任务文档

- app_token: `P9CEb53hbaUNrNsb0o4lsdCIgNh`

## 子表 1: 2. 优化任务进度表

- 记录数（本次拉取上限 2000）: 19

| 待办事项 | 是否已完成 | 状态 | 执行人 | 备注 |
| --- | --- | --- | --- | --- |
| Tongits资源优化计划-Spine | True | 🔴P0-高优-其他游戏 | Makoto |  |
| 占位 | False | 🟢P1-低优 |  |  |
| E-Color迁移 | True | 🔴P0-高优-主包 | Lin; hex; Gordon; Buck; Nathan | 对业务流程无影响，中台和运维单独给Color配个地址即可 |
| 全游戏资源打包TexturePacket |  | 🟣P2-可不处理 | annaanna; Buck; Nathan; Gordon; Eugene; Makoto | 首次加载的极致优化，涉及所有游戏 |
| 表情包优化1-美术压缩 | True | 🔴P0-高优-主包 | Makoto |  |
| 表情包优化1-前端适配 | True | 🔴P0-高优-主包 | Buck |  |
| 客户端无用资源整理保存后剔除 | True | 🔴P0-高优-主包 | Buck |  |
| 客户端引擎配置修改 | True | 🔴P0-高优-主包 | Nathan | Color需要再单独适配引擎裁切 |
| 客户端打包配置修改 | True | 🔴P0-高优-主包 | Nathan; Gordon |  |
| Tongits资源优化计划-大图 | True | 🔴P0-高优-其他游戏 | Makoto |  |
| Tongits资源优化计划-碎图 | True | 🔴P0-高优-其他游戏 | Makoto |  |
| Tongits资源优化计划-挑战Spine特殊处理 | True | 🔴P0-高优-其他游戏 | Makoto |  |
| 表情包优化2-表情包下载任务移除出首页加载，实现懒加载 | True | 🔴P0-高优-主包 | Buck | 首次加载的极致优化，所有游戏都能吃到红利 |
| Color资源优化-Spine修改 | True | 🔴P0-高优-主包 | Gordon; Makoto | Loading + 碎图 |
| Pusoy资源优化-Loading整理 | True | 🔴P0-高优-主包 | Makoto; Gordon |  |
| Tongits牌类复用模式修改 | True | 🔴P0-高优-主包 | Lucy; Buck |  |
| Tongits规则页历史记录页资源修改 | True | 🔴P0-高优-主包 | Buck; Lucy |  |
| Tongits-spine资源全量替换，挑战模块重写部分逻辑 | True | 🔴P0-高优-主包 | Makoto; Buck |  |
| Pusoy资源替换 | True | 🔴P0-高优-主包 | Gordon |  |

## 子表 2: Bundle明细 2026.5.4

- 记录数（本次拉取上限 2000）: 14

| Bundle | 优化前(MiB) | 优化后(MiB) | 变化(MiB) | 变化(Bytes) | 文件数 | 改动摘要 | 修改范围 | 本周重心 | SourceID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 游戏-bato | 12.5644254684448 | 11.1951684951782 | -1.3692569732666 | -1435770 | 47 | 新增17 / 删17 / 重命名13 | 无用资源引用删除 | 压缩资源 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW5IZzoxYmIwZTkwNmVkZDkyNGExYzM0MTEwZmY0YmI4MTExODox |
| 游戏-bingoflash | 19.2792663574219 | 19.2820177078247 | 0.00275135040283203 | 2885 | 27 | 新增12 / 删6 / 重命名9 | 新游戏; 还未压缩 | 压缩资源 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW5TYTo2MzcxMWJiMmMxMGNkMGYwNTcxZGUxODBmM2E0ZmM4Yzox |
| 游戏-bingoshow | 7.98373413085937 | 7.18941497802734 | -0.794319152832031 | -832904 | 43 | 新增16 / 删16 / 重命名11 | 新游戏; 已压缩 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW5aRDozNzg1YWQyODMzNzA3MTQ2ZDUzYTQ4NmE3ZGUwNTI3OTox |
| 游戏-ecolor | 30.7001438140869 | 10.5581865310669 | -20.14195728302 | -21120373 | 178 | 新增41 / 删123 / 重命名14 | 引擎压缩; 资源压缩 | 迁移; 建立新库 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW82VDpkNjg0MzAyODM5NzMxZTlmNWY2ZjJjYTk4MzUxZWE2NDox |
| internal | 0.932785034179687 | 0.924921989440918 | -0.00786304473876953 | -8245 | 4 | 新增1 / 删1 / 重命名2 | 引擎内部的资源; 减少引用 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9iQTo5MTU5YjA3ZTA1NmZlOGJhNWNkMzQyNWVlYTFjNTA2Mzox |
| main | 0.609677314758301 | 0.556279182434082 | -0.0533981323242188 | -55992 | 3 | 重命名3 | 引擎内部的资源; 减少引用 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9pNjo5YTc4ZWIxMGYwNWU4YTk3ZjZhMTU0NmEwZDA3ZmZhNzox |
| 游戏-mines | 10.7884273529053 | 9.94664764404297 | -0.841779708862305 | -882670 | 58 | 新增22 / 删22 / 重命名14 | 无用资源引用删除 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9tQjpmMDQ5YTU5NmJkYmRkZmUyZTYyZGJlMTg0YTY5ZjYwMTox |
| 游戏-pusoy | 17.58740234375 | 7.8776798248291 | -9.7097225189209 | -10181382 | 154 | 新增59 / 删78 / 重命名17 | 无用资源引用删除; 资源压缩; 引用修改 | 优化Spine | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9yZDo5NmRjZWM3NGE2YzA3OTA0OTE3YTAyNDMwNWQ3Y2JjNzox |
| resources | 6.23647975921631 | 3.36656284332275 | -2.86991691589355 | -3009326 | 443 | 新增134 / 删293 / 重命名16 | 大黄脸表情包优化 | 移出大黄脸; 大黄脸改为懒加载 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW92RDpkNDE4ZjRjZTI4YjgyMzA3MjgwYmJiOTBkM2RlNTA4Yjox |
| 游戏-solitaire | 14.8911046981812 | 11.6258039474487 | -3.26530075073242 | -3423916 | 69 | 新增26 / 删32 / 重命名11 | 无用资源引用删除 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9BYjpkOTI2MTE4ODkxYmZiMDczZmE5M2Y1ODA5NjFhMGQ0NDox |
| 游戏-texas | 8.74050617218018 | 2.94157123565674 | -5.79893493652344 | -6080624 | 78 | 新增36 / 删37 / 重命名5 | 无用资源引用删除 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9FQzo2YzBjMTIwYjEwOGFkMWJkNWMxMjMyZDM0MDI4NmU5ODox |
| 游戏-texasplus | 15.4855012893677 | 9.63022422790527 | -5.8552770614624 | -6139703 | 48 | 新增8 / 删36 / 重命名4 | 无用资源引用删除 | 资源继续压缩 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9KODozYTQwOGVkMDcwYTgzMTg2NTg4YTc0NzYwZTQyZjA1Njox |
| 游戏-tongits | 12.089282989502 | 10.6491928100586 | -1.44009017944336 | -1510044 | 79 | 新增28 / 删26 / 重命名25 | 无用资源引用删除; 向美术提交资源变更申请和新的资源优化规则 | 本周重点; 替换资源和spine; 设计复杂则改为代码生成; 尽量减少无用资源增加 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9OSDo4MTg4NWZiNzI0ZGQxYmIxOGI1Nzk0ZjA4YzNiZmE1Mjox |
| 游戏-unleash | 12.1364488601685 | 10.4537725448608 | -1.68267631530762 | -1764414 | 60 | 新增23 / 删29 / 重命名8 | 无用资源引用删除 | 保持 | NzYzNTkwODY4Nzk5MDcwNTg5MDpyZWMyN2tIckhLNW9TZzplMzk5OWFhNTcyOWI4MDhhODkzZjFkYWI5NzRmNzEzZTox |


## 子页面列表


- **bundle修改前后大小表** — `BrC4wrJi8iKNIIkOvpilCkQfgWd` (bitable)

- **资源优化任务协作分工状态及处理后数据汇总** — `X0OgwYvIBiLFPKk3YCklY6jpg0e` (docx)
