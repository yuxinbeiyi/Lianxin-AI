---
triggers: ["播放", "暂停", "音乐", "漫游", "私人FM", "私人 FM", "下一首", "音量", "歌单", "随机播放", "control_music", "歌", "曲子", "切歌", "上一首", "静音", "声音", "听", "放", "放歌", "放点", "来点", "听歌", "听点", "换一首", "换歌", "循环", "列表", "播放器", "播放列表"]
---

## 音乐控制指南

## 控制
- 播放 → control_music(action="play")
- 暂停 → control_music(action="pause")
- 下一首 → control_music(action="next")
- 开始漫游/私人 FM → control_music(action="roam-start")
- 退出漫游（保留当前播放，不修改账号喜好）→ control_music(action="roam-stop")
- 音量调大 → control_music(action="volume_up")
- 随机播放 → control_music(action="loop")  // 切换三种模式

## 查询
- 现在在放什么 → get_music_status
- 歌单有什么 → get_music_playlist
- 最常听哪首 → get_music_stats
禁止凭空回答，必须依赖工具返回数据。
