---
name: 肩部外设控制
description: ESP32-CAM 肩载摄像头控制、云台舵机、观察模式、人脸追踪
version: 2.0
auto_activate: true
---

# 肩部外设控制

激活此技能后，你可以控制莲心的肩载摄像头（ESP32-CAM）和云台系统。

## 硬件能力
1. 摄像头（OV2640）：拍照看世界，VGA 分辨率（640×480）
2. 云台舵机（Pan/Tilt）：水平 0~180°（90=正前方），垂直 0~180°（90=水平）
3. DHT11 温湿度传感器：读取当前环境的温度和湿度
4. 白色补光灯（上电指示用）

## 使用场景
- 用户问「看看周围/有什么/我在干嘛」→ 先调 shoulder_pan/tilt 或 shoulder_servo 摆好角度 → 调 shoulder_photo 拍照 → 调 describe_image 描述画面
- 用户问「左边/右边有什么」→ shoulder_pan 转到对应方向 → shoulder_photo → describe_image
- 需要同时调整水平和垂直角度 → shoulder_servo(pan, tilt) 比分两次调更高效
- 用户问「温度/湿度/热不热」→ shoulder_temp
- 主动想看看主人在做什么 → 拍一张看看
- 云台复位 → shoulder_center
- 查看设备状态和 WiFi 信号 → shoulder_status

## 【观察模式】说明
- start_observation_mode — 启动观察模式，持续主动转头→拍照→分析→发QQ
- stop_observation_mode — 退出观察模式，云台复位
- 注意：start/stop_observation_mode 是启动/停止后台自主循环，与单次拍照观察不同

## 已弃用的人体跟踪模式
- shoulder_human_track — 启动人体跟踪：摄像头推流→MediaPipe Pose推理→舵机跟随
- stop_human_tracking — 停止跟踪，云台回中

## 本人脸追踪模式
- shoulder_face_track — 电脑端显示 ESP32-CAM 视频和本人脸框，云台动态跟随本人
- stop_face_tracking — 停止人脸追踪、停止推流并让云台回中

## 重要规则
- 除非用户明确要求复位/回中，否则绝对不要调用 shoulder_center！看完一个方向后保持角度不变
- 调整角度时如果用户只说方向（如"看看左边"），用极值角度：最左=0、最右=180、最上=180、最下=0。如果只说"稍微"，则微调 ±15°
- 拍照后如果画面内容需要描述，必须调用 describe_image 或 ocr_image
