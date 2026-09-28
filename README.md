# Infinite Canvas 桌面版（AI Studio 打包版）

基于开源项目 **Infinite Canvas** 打包增强的本地桌面应用：一键启动、内置本地助手服务与 ComfyUI 对接，
支持 API / ModelScope / RunningHub / 本地 ComfyUI / 即梦 CLI 等多种生成方式（图片、视频、大语言模型）。

## 来源与许可（必读）

- **原项目**：<https://github.com/hero8152/Infinite-Canvas>（作者：hero8152）
- 本仓库是原项目的二次开发打包版，依照原项目 LICENSE 要求：**保持开源并注明来源作者**。
- 原 LICENSE 随仓库保留（见 [LICENSE](LICENSE)）：允许个人与公司内部使用；**禁止**在未获授权的情况下
  修改封装为商业产品，商用须取得原作者授权。

## 更新机制

- 应用内「检查更新」读取本仓库 `VERSION` 与本地版本比较；
- 「一键更新」从本仓库下载并替换程序文件（`static/`、`main.py`、`AI-Studio-Desktop.pyw`、`VERSION`），
  更新前自动在 `AI-Studio-Data/update_backups/` 留还原点，不影响用户数据与生成历史。

## 仓库里有什么 / 没有什么

仓库内容即「更新通道」：程序代码、页面（`static/`）、工作流、启动脚本与使用文档。

以下内容**不在**仓库里（属于本机运行数据或体积过大的资源，首次安装请使用完整分发包）：

| 目录 / 文件 | 说明 |
|---|---|
| `python/` | 内嵌 Python 运行时（约 64 MB，随分发包安装） |
| `AI-Studio-Data/` | 用户数据：模板、历史、触发词、缓存、浏览器 profile 等，程序自动重建 |
| `data/` | 应用运行数据（画布、会话、API 配置等） |
| `assets/` | 素材库输入/输出与默认素材（约 270 MB） |
| `output/` | 生成结果输出目录 |

## 快速开始

1. 首次安装：使用完整分发包解压到任意目录（项目内代码均为相对路径）。
2. 双击 `启动桌面版.bat`（或 `python\pythonw.exe AI-Studio-Desktop.pyw`）。
3. 详细使用见 [新手运行与使用教程.md](新手运行与使用教程.md)；macOS 见 [MAC-使用说明.md](MAC-使用说明.md)。

端口：后端 3000、桌面助手 8317、ComfyUI 8188。
