# 莲心 Live2D 本机运行资产

当前正式运行目录：`E:\Desktop\Lianxin-AI\Live2D`。启动器与角色窗口默认从这里读取资源，不再依赖 `assets/live2d` 或娜迦参考目录。

- `config.json`：模型和 Core 的相对路径，以此目录为基准，不依赖启动时的工作目录。
- `model/`：当前已验收 v17 的模型、贴图、表情与动作。导出文件名仍沿用 `v14___Layout`，不代表模型版本退回。
- `live2dcubismcore.min.js`：从本机原先使用的 Core 复制，保留原文件版权和许可声明；仅用于本机运行，不作为项目自有开源代码发布。
- `pixi.min.js`、`cubism4.min.js` 及对应 LICENSE：现有播放器依赖的原样副本。

迁移只复制资产，未改动口型、摆臂或模型内容；旧目录与 PSD2Live/Krita 源工程均保留。播放器界面代码仍位于 `gui/galgame/live2d_web/`，不必复制到资产目录。

本目录的模型、Core 和生成配置默认不提交 Git。备份/换电脑请复制整个目录，不能只复制 `.model3.json`；对外发布前须单独确认模型及 Live2D Core 的授权条件。

完整使用与重新准备步骤见 `docs/莲心Live2D接入说明.md`。
