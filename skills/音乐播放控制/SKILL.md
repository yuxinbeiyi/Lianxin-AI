---
name: 音乐播放控制
description: 莲心音乐盒控制（播放/暂停/切歌/私人漫游/音量/歌单查询）
version: 2.0
auto_activate: true
---

# 音乐播放控制

激活此技能后，你可以通过莲心音乐盒播放、控制音乐。

## 能力说明

- **播放控制**：播放、暂停、下一首、上一首
- **模式切换**：切换循环模式
- **私人漫游**：使用网易云私人 FM 持续推荐，支持开始和退出；退出不停止当前歌曲、不改变账号喜好
- **音量调节**：增加/减小音量
- **信息查询**：查看歌单、当前播放状态、播放统计

## 使用场景

- 用户说"放首歌听" → `control_music("play")`
- 用户说"下一首" → `control_music("next")`
- 用户说"开启漫游/随便推荐点歌" → `control_music("roam-start")`
- 用户说"退出漫游" → `control_music("roam-stop")`
- 用户说"声音大点" → `control_music("volume_up")`
- 用户问"在放什么歌" → `get_music_status()` 获取播放状态
- 用户问"歌单有什么" → `get_music_playlist()` 获取歌单
- 用户问"听了多久音乐" → `get_music_stats()` 获取统计
