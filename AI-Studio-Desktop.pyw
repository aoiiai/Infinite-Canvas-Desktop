# -*- coding: utf-8 -*-
"""
AI Studio 桌面版启动器

把项目的网页界面包装成一个独立的桌面应用：
  1. 静默启动项目自带的 Python + main.py（不弹黑框）
  2. 等本地服务就绪
  3. 用 Edge/WebView2 的 --app 模式开一个无地址栏的原生窗口
  4. 窗口关闭后自动结束后台服务

不修改项目里的任何文件：后端就是原来那个 main.py，服务器照常在 3000 端口跑，
`host="0.0.0.0"` 也保持原样，所以局域网共享、Chrome 插件、PS 面板、
ComfyUI 桥接、自更新这些能力都不受影响。桌面版自己的运行日志、缓存和浏览器
配置都放在项目内的 AI-Studio-Data\（2026-09-25 前在 %LOCALAPPDATA%\AIStudioDesktop\，
首次启动自动搬旧数据；浏览器 profile 不搬、在项目内重建）。

用法：双击本文件（或 pythonw.exe 本文件）。
把本文件放到项目根目录（与 main.py 同级）即可自动定位；
否则设置环境变量 AI_STUDIO_HOME 指向项目根目录。
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

APP_NAME = "AI Studio"
PORT = 3000
# 3000 被别的程序占着时的备用端口（依次试第一个空闲的）。
# 只在「桌面版自己起后端」时才会用到：3000 空着就还是走 3000，
# 所以外部插件 / 局域网共享那些指向 3000 的用法完全不受影响。
FALLBACK_PORTS = (3001, 3002, 3003, 3004)
HOST = "127.0.0.1"
STARTUP_TIMEOUT = 120          # 等待服务就绪的上限（秒）
WINDOW_TIMEOUT = 40            # 等待窗口出现（秒）
POLL = 2.5                     # 轮询窗口状态的间隔（秒）
CONFIRM_GONE = 4               # 连续多少次确认「确实全关了」才收工（≈10 秒）
WINDOW_SIZE = "1560,980"

# 要隐藏的界面功能（对应 index.html 里 switchUI(this, 'xxx') 的 id）。
# 留空列表 = 不隐藏任何东西。
FEATURE_HIDES = ["canvas"]     # canvas = 侧栏「无限画布」

# 左下角作者组件：作者名「wuli大雄」+ 4 个社交图标（B站/小红书/YouTube/X），
# 折叠状态下显示的是同一个组件里的 D、X 两个字母。整块没有 JS 引用，可直接删掉。
AUTHOR_BOX_TAG = '<div class="author-box">'

# 「更多设置」折叠组：里面其实只有 3 个选项（黑夜模式 / 中文 / 工作流设置）。
# 这里把折叠去掉，让这 3 项直接显示，并隐掉折叠按钮。
SETTINGS_GROUP_FIND = '<div class="settings-fold-group is-collapsed" id="settings-fold-group">'
SETTINGS_GROUP_OPEN = ('<div class="settings-fold-group" id="settings-fold-group" '
                       'style="max-height:none;opacity:1;pointer-events:auto">')
SETTINGS_TOGGLE_FIND = '<button class="side-pill settings-fold-toggle" id="settings-fold-toggle"'
SETTINGS_TOGGLE_HIDE = ('<button class="side-pill settings-fold-toggle" id="settings-fold-toggle" '
                        'style="display:none"')
SETTINGS_RESTORE_FIND = ("const savedCollapsed = "
                         "localStorage.getItem(SIDEBAR_SETTINGS_COLLAPSED_KEY) !== '0';")
SETTINGS_RESTORE_SET = "const savedCollapsed = false;"

# 侧栏底部还要删掉的几项。都已确认 JS 侧有 null 保护，删掉不会让脚本报错。
PROJECT_BTN_TAG = '<button id="github-entry-btn"'            # 项目主页（GitHub）
VERSION_BADGE_TAG = '<div id="project-version-badge"'        # 版本号角标 v08.28
LANG_BTN_TAG = '<button class="side-pill" onclick="toggleLanguage()" id="lang-toggle-btn"'  # 中英文切换

# 「黑夜模式」要移到底部：把它从设置分组里挪到 .side-actions 的末尾
THEME_BTN_TAG = '<button class="side-pill" onclick="toggleTheme()" id="theme-toggle-btn"'
SIDE_ACTIONS_MARKER = 'class="side-actions"'

# ---------- 自定义新增的「视频生成」页（MiniMax H3）----------
# 页面本体放在 static/video.html。static/ 会被应用自更新整个替换掉，
# 所以这里额外在用户目录留一份备份，发现 static/ 里没了就恢复回去。
NEW_PAGE = "video.html"
NEW_PAGE_MARKER = "视频生成"          # 用来判断恢复回来的文件是不是我们那份

# ---------- 自定义新增的「ComfyUI 设置」页 ----------
# 和「视频生成」页一样：文件在 static/ 里，用户目录留一份备份，自更新把 static/
# 整个换掉之后自动恢复。这一页管 ComfyUI 安装路径（LoRA 扫描源 / 参考图直传 /
# 模型核对），数据走启动器的助手服务，后端 main.py 一行没改。
SETTINGS_PAGE = "comfy-settings.html"
SETTINGS_PAGE_MARKER = "ComfyUI 设置"
SETTINGS_PAGE_ID = "comfy-settings"
# 插在侧栏底部「API 设置」那颗按钮前面
SETTINGS_NAV_ANCHOR = ('<button class="side-pill" onclick="switchUI(this, \'api-settings\')"')
SETTINGS_NAV_HTML = '''                <button class="side-pill" onclick="switchUI(this, '%s')" type="button" title="ComfyUI 设置">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="4" y="4" width="16" height="16" rx="2"></rect>
                        <rect x="9" y="9" width="6" height="6"></rect>
                        <path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"></path>
                    </svg>
                    <span class="side-pill-text">ComfyUI 设置</span>
                </button>
''' % SETTINGS_PAGE_ID
SETTINGS_IFRAME_AFTER = "frame-api-settings"

# ---------- 备用端口用的后端包装脚本 ----------
# main.py 里 uvicorn 的 port=3000 是写死的、且不能改（自更新会覆盖）。
# 3000 被别的程序占着时，用这个脚本 import main 把同一个 app 换到备用端口上跑。
# 和两个自建页面一样：根目录放一份，用户目录留一份备份，被自更新冲掉就恢复。
BACKEND_SCRIPT = "AI-Studio-Backend.py"
BACKEND_SCRIPT_MARKER = "AI-Studio-Backend"

# ---------- 「生成结果栏」通用组件 ----------
# 生成结果列表抽成的独立文件（视频生成页在用，以后文生图页复用同一个）。
# 和两个自建页面一样：static/ 里一份、数据目录留一份备份，被自更新冲掉就恢复。
RESULT_DOCK_JS = "result-dock.js"
RESULT_DOCK_MARKER = "ResultDock.mount"      # 判断恢复回来的文件是不是我们那份
WORKFLOW_AUTOFIELDS_JS = "workflow-autofields.js"
WORKFLOW_AUTOFIELDS_MARKER = "WorkflowAutoFields"    # 判断恢复回来的文件是不是我们那份
# 上次实际用的后端端口。窗口还开着时靠它知道该连哪个端口（端口可能不是 3000）。
LAST_PORT_FILE = "last_port.txt"

# ---------- 提示词模板 / 生成历史 ----------
# main.py 一行都不能改（自更新会覆盖），所以这两份数据不走后端，
# 由助手服务直接读写用户目录下的 JSON，网页通过 127.0.0.1:8317 访问。
TEMPLATES_FILE = "prompt_templates.json"
PROMPT_HISTORY_FILE = "prompt_history.json"
PROMPT_HISTORY_MAX = 500        # 落盘最多留多少条，超了从最旧的开始丢
PROMPT_HISTORY_RETURN = 200     # 一次最多返回多少条给界面
UI_STATE_FILE = "ui_state.json"  # 记住用户上次的参数（比例/清晰度/时长/种子 + LoRA 选择）
LORA_TRIGGERS_FILE = "lora_triggers.json"  # 用户编辑过的触发词（按 LoRA id 存）
LORA_CIVITAI_CACHE = "lora_civitai_cache.json"  # 哈希反查 Civitai 的结果缓存
MODEL_OVERRIDES_FILE = "model_overrides.json"   # 保存的模型替换（按工作流名存）

# 上一版方案留下的东西：界面原本是注入到「工作流设置」页标题栏里的，现在改成
# 独立页面了。这个常量只用来在启动时把残留的脚本文件清掉。
PATH_UI_FILE = "comfy-path-ui.js"

# 要对 index.html 做的接线：导航项 + iframe + 两个页面白名单
NEW_PAGE_ID = "video"
NAV_GROUP_MARKER = 'id="local-nav-group"'      # 插到这个分组里（「本地功能」）
IFRAME_AFTER_ID = "frame-angle"                # 插在「角度控制」的 iframe 之后
NAV_ITEM_HTML = '''                    <div class="nav-item" onclick="switchUI(this, 'video')">
                        <svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="2" y="5" width="13" height="14" rx="2"></rect>
                            <polygon points="15 10 22 7 22 17 15 14"></polygon>
                        </svg>
                        <span class="nav-text">视频生成</span>
                    </div>'''

# ---------- 本地助手服务 ----------
# 网页（file:// 或 127.0.0.1:3000）没法读你硬盘上的 LoRA 目录，所以由启动器
# 这个本地 Python 进程提供一个小接口：扫 LoRA、出封面、列工作流。
# 只监听本机回环地址，随应用一起起停。页面通过固定端口访问。
HELPER_HOST = "127.0.0.1"
HELPER_PORT = 8317
LORA_EXTS = (".safetensors", ".ckpt", ".pt", ".sft", ".bin")
COVER_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")
# C 站的视频预览封面（LoRA Manager 也会把它下到 LoRA 同目录）。
# 2026-09-25 用户提醒「有的封面是视频」：之前只认图片扩展名，291 个视频封面被当成"没有封面"。
COVER_VIDEO_EXTS = (".mp4", ".webm")

# ---------- ComfyUI 安装路径（界面在「工作流设置」页标题栏）----------
# 用户在界面上填的 ComfyUI 安装目录，连同「启动桌面版时自动拉起 ComfyUI」
# 的开关，一起存在用户目录，不和项目文件混在一起（项目文件会被自更新覆盖）。
# 由本进程的助手服务读写，所以后端 main.py 一行都不用改。
COMFY_CONFIG_FILE = "comfyui.json"
COMFY_DEFAULT_ADDRESS = "127.0.0.1:8188"
# 认作「模型文件」的扩展名，用来在工作流里挑出模型引用、再核对本地有没有
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft",
              ".onnx", ".safetensors.index.json")
# 工作流里「值填的是模型文件名」的输入字段 → ComfyUI models 下的候选子目录。
# 只在「当前模型本地找不到、定位不到目录」时用来猜该去哪个目录列可替换项。
MODEL_FIELD_DIRS = {
    "ckpt_name": ("checkpoints",),
    "lora_name": ("loras",),
    "vae_name": ("vae",),
    "unet_name": ("unet", "diffusion_models"),
    "model_name": ("diffusion_models", "unet"),
    "clip_name": ("clip", "text_encoders"),
    "clip_name1": ("clip", "text_encoders"),
    "clip_name2": ("clip", "text_encoders"),
    "clip_name3": ("clip", "text_encoders"),
    "text_encoder_name": ("text_encoders", "clip"),
    "clip_vision_name": ("clip_vision",),
    "control_net_name": ("controlnet",),
    "controlnet_name": ("controlnet",),
    "style_model_name": ("style_models",),
    "embedding_name": ("embeddings",),
    "gligen_name": ("gligen",),
    "ipadapter_file": ("ipadapter",),
    "upscale_model_name": ("upscale_models",),
    "sam_model_name": ("sams",),
    "vocoder_name": ("vocoder",),
    "audio_vae_name": ("audio_encoders", "vae"),
}
# 从 ComfyUI 安装目录往下找模型/输入目录时认的写法（含大小写变体）
COMFY_MODEL_SUBDIRS = ("models", "Models")
COMFY_LORA_SUBDIRS = (("models", "loras"), ("models", "Lora"), ("models", "LoRA"),
                      ("models", "loras_extra"))
COMFY_INPUT_SUBDIRS = (("input",), ("Input",))

# 桌面版自己的数据目录：放在项目文件夹里（2026-09-25 用户要求，不许占 C 盘）
DATA_DIR_NAME = "AI-Studio-Data"
# 旧位置：C 盘 %LOCALAPPDATA%\<这个名字>；首次启动会把里面的数据一次性搬过来
LEGACY_DIR_NAME = "AIStudioDesktop"
# 认「自己开的应用窗口」用的标记（ASCII，会出现在 --user-data-dir 里，避开编码问题）
PROFILE_MARKER = DATA_DIR_NAME

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def state_dir() -> str:
    """桌面版自己的数据目录：<项目>\AI-Studio-Data\。

    2026-09-25 之前放在 %LOCALAPPDATA%\AIStudioDesktop（C 盘，用户空间很紧张）。
    现在日志、缓存、模板、记录、以及浏览器 profile 全在项目文件夹里，
    首次启动自动把旧目录里的数据搬一次（见 _migrate_legacy_state）。
    """
    base = globals().get("PROJECT_DIR") or os.path.dirname(os.path.abspath(sys.argv[0]))
    path = os.path.join(base, DATA_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    _migrate_legacy_state(path)
    return path


_MIGRATED = False


def _migrate_legacy_state(new_dir: str) -> None:
    """把旧位置（%LOCALAPPDATA%）的数据搬进新目录，每个进程只跑一次。

    只补新目录里缺的文件；只搬文件、不搬子目录 —— profile/ 是浏览器缓存，
    重建即可（顺带把 C 盘那几百 MB 释放掉）。搬完不删旧目录，留给用户确认。
    """
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return
    old = os.path.join(base, LEGACY_DIR_NAME)
    if not os.path.isdir(old) or os.path.abspath(old) == os.path.abspath(new_dir):
        return
    moved, failed = [], []
    try:
        names = sorted(os.listdir(old))
    except Exception:
        return
    for name in names:
        src = os.path.join(old, name)
        dst = os.path.join(new_dir, name)
        if not os.path.isfile(src) or os.path.exists(dst):
            continue
        try:
            shutil.copy2(src, dst)
            moved.append(name)
        except Exception as exc:
            failed.append("%s (%s)" % (name, exc))
    if moved:
        log("数据目录已迁到项目内：搬入 %d 个文件（%s）" % (len(moved), "、".join(moved)))
    if failed:
        log("以下文件没能搬过来：%s" % "；".join(failed))


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(os.path.join(state_dir(), "desktop.log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def fatal(msg: str) -> None:
    """pythonw 启动时没有控制台，出错必须弹窗，否则用户什么都看不到。"""
    log("错误: " + msg)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, APP_NAME + " — 启动失败", 0x10)
    except Exception:
        pass


def project_dir() -> str:
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    candidates = [
        here,
        os.environ.get("AI_STUDIO_HOME") or "",
        os.getcwd(),
        r"F:\无限画布",
    ]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "main.py")):
            return os.path.abspath(c)
    fatal(
        "找不到 main.py。\n\n"
        "请把 AI-Studio-Desktop.pyw 放到项目根目录（与 main.py 同级），"
        "或设置环境变量 AI_STUDIO_HOME 指向项目根目录。"
    )
    sys.exit(1)


PROJECT_DIR = project_dir()


# ---------- 界面功能隐藏 ----------
# 静态前端目录在应用自更新时会被整个删掉重建，所以在 static/index.html 上的改动
# 会被更新抹掉。这里的做法是每次启动都检查一遍、缺了就补上，因此更新后依然生效。
# 每次只做两处最小改动，都可随时手工还原：
#   1. 从 PAGE_IDS 白名单里摘掉该 id
#      —— 启动恢复上次页面时会校验这个白名单，switchUI 也会校验，
#         所以摘掉之后「记住的画布页」和任何残留调用都会自动回落到默认页。
#   2. 给对应的侧栏项加 style="display:none"

def _strip_div(html: str, open_tag: str) -> str:
    """删掉第一个以 open_tag 开头的 <div> 及其配对 </div>（正确处理嵌套）。"""
    i = html.find(open_tag)
    if i == -1:
        return html
    depth = 0
    p = i
    while p < len(html):
        nxt_open = html.find("<div", p)
        nxt_close = html.find("</div>", p)
        if nxt_close == -1:
            return html                      # 结构异常，宁可不动
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            p = nxt_open + 4
        else:
            depth -= 1
            p = nxt_close + 6
            if depth == 0:
                return html[:i] + html[p:]
    return html


def _strip_button(html: str, open_tag: str) -> str:
    """删掉第一个以 open_tag 开头的 <button>...</button>（button 不会嵌套）。"""
    i = html.find(open_tag)
    if i == -1:
        return html
    close = html.find("</button>", i)
    if close == -1:
        return html
    return html[:i] + html[close + len("</button>"):]


def _div_span(html: str, marker: str):
    """返回包含 marker 的那个 <div> 的范围 (开标签起, 配对 </div> 之后)。

    注意 marker 可能本身就是 '<div ...>' 开标签，也可能只是标签内的属性片段。
    前者要直接以 marker 位置为起点，否则会往上找到外层的 div（会误删整块）。
    """
    i = html.find(marker)
    if i == -1:
        return None
    start = i if html.startswith("<div", i) else html.rfind("<div", 0, i)
    if start == -1:
        return None
    depth = 0
    p = start
    while p < len(html):
        nxt_open = html.find("<div", p)
        nxt_close = html.find("</div>", p)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            p = nxt_open + 4
        else:
            depth -= 1
            p = nxt_close + 6
            if depth == 0:
                return start, p
    return None


def _insert_before_div_close(html: str, marker: str, block: str):
    """把 block 插到「包含 marker 的那个 div」的收尾 </div> 之前（即作为最后一个子元素）。"""
    span = _div_span(html, marker)
    if not span:
        return html, False
    close_at = span[1] - len("</div>")
    # 取收尾 </div> 那一行的缩进，让插入的内容对齐
    line_start = html.rfind("\n", 0, close_at) + 1
    indent = html[line_start:close_at]
    if indent.strip():
        indent = ""
    body = "\n".join(indent + ln.strip() if ln.strip() else ln
                     for ln in block.strip().splitlines())
    return html[:line_start].rstrip("\n") + "\n" + body + "\n" + html[line_start:], True


def _ensure_static_file(fname: str, marker: str, verbose: bool = False) -> bool:
    """保证 static/<fname> 存在，并在用户目录留一份备份。

    文件本体在 static/ 里（正常可以手改），但应用自更新会整个替换 static/，
    所以文件没了就从备份恢复，文件在就把备份刷成最新。
    """
    dst = os.path.join(PROJECT_DIR, "static", fname)
    bak = os.path.join(state_dir(), fname)
    try:
        if os.path.isfile(dst):
            current = open(dst, encoding="utf-8", errors="replace").read()
            if marker not in current:
                return False          # 不是我们的文件，别乱动
            old = None
            if os.path.isfile(bak):
                old = open(bak, encoding="utf-8", errors="replace").read()
            if current != old:
                with open(bak, "w", encoding="utf-8") as fh:
                    fh.write(current)
                if verbose:
                    print("已更新备份: %s" % bak)
            return False
        if os.path.isfile(bak):
            shutil.copy2(bak, dst)
            return True
    except Exception as exc:
        log("处理 %s 失败: %s" % (fname, exc))
    return False


def _ensure_new_page(verbose: bool = False) -> bool:
    """保证自建的页面 / 组件文件都在（视频生成 / ComfyUI 设置 / 生成结果栏组件）。"""
    restored = _ensure_static_file(NEW_PAGE, NEW_PAGE_MARKER, verbose)
    if _ensure_static_file(SETTINGS_PAGE, SETTINGS_PAGE_MARKER, verbose) and verbose:
        print("已恢复页面: static/%s" % SETTINGS_PAGE)
    if _ensure_static_file(RESULT_DOCK_JS, RESULT_DOCK_MARKER, verbose) and verbose:
        print("已恢复组件: static/%s" % RESULT_DOCK_JS)
    if _ensure_static_file(WORKFLOW_AUTOFIELDS_JS, WORKFLOW_AUTOFIELDS_MARKER, verbose) and verbose:
        print("已恢复组件: static/%s" % WORKFLOW_AUTOFIELDS_JS)
    return restored


def ensure_backend_script(verbose: bool = False) -> bool:
    """保证备用端口用的后端包装脚本在。

    它在项目根目录（不是 static/），自更新按清单覆盖根目录文件时可能把它冲掉，
    所以和两个自建页面一样：用户目录留一份备份，没了就从备份恢复。
    返回 True 表示这次是从备份恢复的。
    """
    dst = os.path.join(PROJECT_DIR, BACKEND_SCRIPT)
    bak = os.path.join(state_dir(), BACKEND_SCRIPT)
    try:
        if os.path.isfile(dst):
            current = open(dst, encoding="utf-8", errors="replace").read()
            if BACKEND_SCRIPT_MARKER not in current:
                return False          # 不是我们的文件，别乱动
            old = None
            if os.path.isfile(bak):
                old = open(bak, encoding="utf-8", errors="replace").read()
            if current != old:
                with open(bak, "w", encoding="utf-8") as fh:
                    fh.write(current)
                if verbose:
                    print("已更新备份: %s" % bak)
            return False
        if os.path.isfile(bak):
            shutil.copy2(bak, dst)
            return True
    except Exception as exc:
        log("处理 %s 失败: %s" % (BACKEND_SCRIPT, exc))
    return False


def _local_fixed_drives():
    """本机固定磁盘的盘符（跳过网络盘和光驱，免得探测时卡住）。"""
    import ctypes
    import string

    out = []
    for letter in string.ascii_uppercase:
        root = "%s:\\" % letter
        if not os.path.isdir(root):
            continue
        try:
            kind = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        except Exception:
            kind = 3
        if kind in (2, 4, 5):          # 可移动 / 网络 / 光驱
            continue
        out.append(root)
    return out


# ---------- ComfyUI 安装路径：配置读写与派生目录 ----------

def comfy_config_path() -> str:
    return os.path.join(state_dir(), COMFY_CONFIG_FILE)


def _clean_dir(value) -> str:
    """把用户填的路径规整一下：去引号、展开变量、去掉结尾斜杠。"""
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    text = os.path.expanduser(os.path.expandvars(text))
    text = text.rstrip("\\/") or text
    return text


def load_comfy_config() -> dict:
    cfg = {"install_dir": "", "auto_start": False, "civitai_domain": "civitai.red"}
    path = comfy_config_path()
    if os.path.isfile(path):
        try:
            data = json.load(open(path, encoding="utf-8")) or {}
        except Exception as exc:
            log("读 %s 失败: %s" % (COMFY_CONFIG_FILE, exc))
            data = {}
        cfg["install_dir"] = _clean_dir(data.get("install_dir"))
        cfg["auto_start"] = bool(data.get("auto_start"))
        dom = str(data.get("civitai_domain") or "").strip().lower()
        cfg["civitai_domain"] = dom if dom in ("civitai.com", "civitai.red") else "civitai.red"
    return cfg


def save_comfy_config(data: dict) -> dict:
    cfg = load_comfy_config()
    if "install_dir" in (data or {}):
        cfg["install_dir"] = _clean_dir((data or {}).get("install_dir"))
    if "auto_start" in (data or {}):
        cfg["auto_start"] = bool((data or {}).get("auto_start"))
    if "civitai_domain" in (data or {}):
        dom = str((data or {}).get("civitai_domain")).strip().lower()
        cfg["civitai_domain"] = dom if dom in ("civitai.com", "civitai.red") else "civitai.red"
    path = comfy_config_path()
    payload = {
        "install_dir": cfg["install_dir"],
        "auto_start": cfg["auto_start"],
        "civitai_domain": cfg.get("civitai_domain", "civitai.red"),
        "_说明": ("install_dir 是 ComfyUI 的安装目录（里面应当有 main.py 和 models 文件夹）；"
                  "auto_start 为 true 时，启动桌面版会顺带把 ComfyUI 拉起来。"
                  "界面在侧栏「ComfyUI 设置」页，也可以直接在那一页点「浏览…」改。"),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return cfg


def comfy_install_dir() -> str:
    return load_comfy_config()["install_dir"]


def _first_existing_dir(base: str, groups) -> str:
    for parts in groups:
        p = os.path.join(base, *parts)
        if os.path.isdir(p):
            return p
    return ""


def comfy_loras_dirs(install_dir: str) -> list:
    p = _first_existing_dir(install_dir, COMFY_LORA_SUBDIRS)
    return [p] if p else []


def comfy_input_dir(install_dir: str) -> str:
    return _first_existing_dir(install_dir, COMFY_INPUT_SUBDIRS)


def comfy_models_dir(install_dir: str) -> str:
    return _first_existing_dir(install_dir, ((m,) for m in COMFY_MODEL_SUBDIRS))


def _count_files(root: str, exts=None, max_depth: int = 4) -> int:
    n = 0
    if not root or not os.path.isdir(root):
        return 0
    try:
        for current, dirs, files in os.walk(root):
            if current[len(root):].count(os.sep) >= max_depth:
                dirs[:] = []
            for fn in files:
                if not exts or fn.lower().endswith(exts):
                    n += 1
    except OSError:
        return n
    return n


_comfy_detect_cache = {"at": 0.0, "dirs": None}


def detect_comfy_installs(force: bool = False) -> list:
    """在本机固定磁盘上找 ComfyUI 安装目录（要有 main.py 和 models 才算）。

    只在没配路径时才需要，所以结果缓存 10 分钟，避免界面刷新时反复走盘。
    """
    now = time.time()
    if (not force and _comfy_detect_cache["dirs"] is not None
            and now - _comfy_detect_cache["at"] < 600):
        return _comfy_detect_cache["dirs"]
    found = []
    for root in _local_fixed_drives():
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if "comfyui" not in name.lower():
                continue
            base = os.path.join(root, name)
            for cand in (base, os.path.join(base, "ComfyUI")):
                if not os.path.isfile(os.path.join(cand, "main.py")):
                    continue
                if not os.path.isdir(os.path.join(cand, "models")):
                    continue
                if cand not in found:
                    found.append(cand)
    _comfy_detect_cache.update({"at": now, "dirs": found})
    log("探测到 ComfyUI 安装目录: %s" % (found or "无"))
    return found


def comfy_report() -> dict:
    """把「这个路径到底管不管用」一次性说清楚，界面直接拿来显示。"""
    install = comfy_install_dir()
    cfg = load_comfy_config()
    out = {
        "install_dir": install,
        "auto_start": cfg["auto_start"],
        "configured": bool(install),
        "exists": bool(install) and os.path.isdir(install),
        "main_py": False, "run_bat": "", "loras_dir": "", "input_dir": "",
        "models_dir": "", "loras": 0, "models": 0,
        "lora_folders": lora_folders(),
        "address": comfy_address(),
        "address_local": _address_is_local(comfy_address()),
        "running": False, "problems": [], "detected": [],
    }
    if not install:
        out["problems"].append("还没填 ComfyUI 安装目录")
        out["running"] = _port_open(comfy_address())
        out["detected"] = detect_comfy_installs()
        return out
    if not out["exists"]:
        out["problems"].append("这个目录不存在")
        return out

    out["main_py"] = os.path.isfile(os.path.join(install, "main.py"))
    for name in COMFY_LAUNCH_BATS:
        if os.path.isfile(os.path.join(install, name)):
            out["run_bat"] = name
            break
    out["loras_dir"] = (comfy_loras_dirs(install) or [""])[0]
    out["input_dir"] = comfy_input_dir(install)
    out["models_dir"] = comfy_models_dir(install)
    out["loras"] = _count_files(out["loras_dir"], LORA_EXTS)
    # 只数模型文件：models 目录下还有 .json/.txt 之类的配置，全算进去会和
    # 模型核对那边报的「索引 N 个」对不上，看着像 bug
    out["models"] = _count_files(out["models_dir"], MODEL_EXTS)
    out["running"] = _port_open(comfy_address())

    if not out["main_py"] and not out["run_bat"]:
        out["problems"].append("目录里找不到 main.py 或启动用 .bat，可能填到了上层目录")
    if not out["models_dir"]:
        out["problems"].append("目录里没有 models 文件夹")
    if not out["loras_dir"]:
        out["problems"].append("models 下没有 loras 文件夹")
    if not out["input_dir"]:
        out["problems"].append("目录里没有 input 文件夹（直传参考图会退回走接口上传）")
    return out


def comfy_ping() -> dict:
    """轻量探活：只 TCP 探一次 ComfyUI 端口，**不扫盘**（2026-09-26 加）。

    ⚠ 页面右上角那个「就绪」指示灯要**每几秒**探一次，**不能**拿 `/comfyui/status` 来轮询 ——
    `comfy_report()` 会遍历 LoRA 和模型目录（1542 个 LoRA + 1696 个模型文件），
    几秒一次等于拿磁盘当鼓敲。要"重"的那份报告走 `/comfyui/status`，只要"通不通"就走这里。
    """
    addr = comfy_address()
    return {
        "ok": True,
        "running": _port_open(addr),
        "address": addr,
        "address_local": _address_is_local(addr),
        "install_dir": comfy_install_dir(),
    }


def comfy_address() -> str:
    """ComfyUI 后端地址：读项目的 API/.env，读不到就用默认值。"""
    try:
        env_path = os.path.join(PROJECT_DIR, "API", ".env")
        if os.path.isfile(env_path):
            for line in open(env_path, encoding="utf-8-sig", errors="replace"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "COMFYUI_INSTANCES":
                    first = (v.strip().split(",") or [""])[0].strip()
                    if first:
                        return re.sub(r"^https?://", "", first).rstrip("/")
    except Exception as exc:
        log("读 API/.env 里的 COMFYUI_INSTANCES 失败: %s" % exc)
    return COMFY_DEFAULT_ADDRESS


def _address_is_local(addr: str) -> bool:
    host = (addr or "").split(":")[0].strip().lower()
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _port_open(addr: str, timeout: float = 0.7) -> bool:
    if not addr or ":" not in addr:
        return False
    host, _, port = addr.rpartition(":")
    try:
        s = socket.socket()
        s.settimeout(timeout)
        ok = s.connect_ex((host.strip(), int(port))) == 0
        s.close()
        return ok
    except Exception:
        return False


# ---------- B：启动 / 探测 ComfyUI ----------
COMFY_LAUNCH_BATS = ("run_nvidia_gpu.bat", "run_nvidia_gpu_fast_fp16_accumulation.bat",
                     "run_cpu.bat", "run.bat")
_comfy_launch = {"pid": 0, "at": 0.0, "cmd": ""}


def comfy_launch_command(install: str):
    """决定用哪条命令拉起 ComfyUI。

    返回 {cmd, cwd, label}，拿不到就返回 None。分两种情况找：
      · 整合包：启动脚本（run_nvidia_gpu.bat 之类）和 python_embeded 在 ComfyUI 的
        上一层目录里，所以要把上一级也当候选。用户填的往往是 ...\\ComfyUI 这一层。
      · 自己装的：目录里有 main.py，用同目录/上一层的 Python 跑。
    找不到就老实返回 None——绝不用应用自己的 Python 去跑 ComfyUI，那肯定起不来。
    """
    if not install or not os.path.isdir(install):
        return None
    bases = [install]
    parent = os.path.dirname(os.path.abspath(install))
    if parent and os.path.isdir(parent) and os.path.abspath(parent) != os.path.abspath(install):
        bases.append(parent)

    # 1) 官方启动脚本优先（它会自己带上环境变量和参数）
    for base in bases:
        for name in COMFY_LAUNCH_BATS:
            bat = os.path.join(base, name)
            if os.path.isfile(bat):
                return {"cmd": ["cmd", "/c", bat], "cwd": base, "label": name}

    # 2) 自带 Python + main.py
    main_py = os.path.join(install, "main.py")
    if os.path.isfile(main_py):
        rels = (os.path.join("python_embeded", "python.exe"),
                os.path.join("venv", "Scripts", "python.exe"),
                os.path.join(".venv", "Scripts", "python.exe"),
                "python.exe")
        for base in bases:
            for rel in rels:
                exe = os.path.join(base, rel)
                if os.path.isfile(exe):
                    return {"cmd": [exe, "main.py"], "cwd": install,
                            "label": os.path.relpath(exe, base)}
    return None


def comfy_start(force: bool = False) -> dict:
    """拉起 ComfyUI（已经在跑就直接返回）。新开一个控制台窗口，方便看它的日志。"""
    install = comfy_install_dir()
    addr = comfy_address()
    if _port_open(addr) and not force:
        return {"ok": True, "started": False, "already": True,
                "message": "ComfyUI 已经在 %s 上跑着" % addr, "status": comfy_report()}
    if not install:
        return {"ok": False, "started": False,
                "message": "先在「ComfyUI 设置」里填 ComfyUI 安装目录", "status": comfy_report()}
    if not os.path.isdir(install):
        return {"ok": False, "started": False,
                "message": "安装目录不存在：%s" % install, "status": comfy_report()}
    spec = comfy_launch_command(install)
    if not spec:
        return {"ok": False, "started": False,
                "message": ("在这个目录（以及它的上一层）里找不到 run_*.bat，也找不到可用的 "
                            "Python，没法自动启动。手动开一下吧，路径填对就行。"),
                "status": comfy_report()}
    try:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        proc = subprocess.Popen(spec["cmd"], cwd=spec["cwd"], creationflags=flags,
                                stdin=subprocess.DEVNULL)
    except Exception as exc:
        return {"ok": False, "started": False, "message": "启动失败：%s" % exc,
                "status": comfy_report()}
    _comfy_launch.update({"pid": proc.pid, "at": time.time(),
                          "cmd": " ".join(spec["cmd"]), "cwd": spec["cwd"]})
    log("已启动 ComfyUI: %s (pid %d, cwd %s)" % (_comfy_launch["cmd"], proc.pid, spec["cwd"]))
    return {"ok": True, "started": True, "pid": proc.pid,
            "message": "已启动 ComfyUI（跑的是 %s），首次加载模型要等一会儿" % spec["label"],
            "command": _comfy_launch["cmd"], "cwd": spec["cwd"], "status": comfy_report()}


def comfy_autostart() -> None:
    """按配置在后台把 ComfyUI 拉起来（只在没在跑的时候动手）。"""
    try:
        cfg = load_comfy_config()
        if not cfg["auto_start"] or not cfg["install_dir"]:
            return
        addr = comfy_address()
        if _port_open(addr):
            log("ComfyUI 已在 %s 运行，跳过自动启动。" % addr)
            return
        res = comfy_start()
        log(res.get("message") or "自动启动 ComfyUI 结束")
    except Exception as exc:
        log("自动启动 ComfyUI 失败: %s" % exc)


# ---------- C：直接把参考图写进 ComfyUI 的 input 目录 ----------

def _safe_input_name(name: str) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    if not base:
        base = "studio_ref.png"
    if len(base) > 120:
        stem, ext = os.path.splitext(base)
        base = stem[:100] + ext
    return base


def write_comfy_input(name: str, data: bytes) -> dict:
    install = comfy_install_dir()
    if not install:
        return {"ok": False, "message": "还没配 ComfyUI 安装目录"}
    in_dir = comfy_input_dir(install)
    if not in_dir:
        return {"ok": False, "message": "安装目录下没有 input 文件夹"}
    if not _address_is_local(comfy_address()):
        # 后端不在本机时，写本地磁盘没用，交给接口上传那条路
        return {"ok": False, "message": "ComfyUI 后端不在本机，改用接口上传"}
    safe = _safe_input_name(name)
    target = os.path.join(in_dir, safe)
    try:
        with open(target, "wb") as fh:
            fh.write(data)
    except Exception as exc:
        return {"ok": False, "message": "写入失败：%s" % exc}
    return {"ok": True, "name": safe, "path": target, "bytes": len(data),
            "message": "已写入 %s" % safe}


# ---------- D：核对工作流要的模型本地有没有 ----------

_model_index_cache = {"at": 0.0, "key": "", "root": "", "rel": set(), "base": set(), "count": 0}


def _models_index(force: bool = False) -> dict:
    root = comfy_models_dir(comfy_install_dir() or "")
    if not root:
        return {"root": "", "rel": set(), "base": set(), "count": 0}
    now = time.time()
    if (not force and _model_index_cache["key"] == root
            and now - _model_index_cache["at"] < 60):
        return _model_index_cache
    rel, base = set(), set()
    try:
        for current, dirs, files in os.walk(root):
            if current[len(root):].count(os.sep) >= 5:
                dirs[:] = []
            for fn in files:
                low = fn.lower()
                if not low.endswith(MODEL_EXTS):
                    continue
                full = os.path.join(current, fn)
                rel.add(os.path.relpath(full, root).replace("\\", "/").lower())
                base.add(low)
    except OSError as exc:
        log("扫模型目录失败: %s" % exc)
    _model_index_cache.update({"at": now, "key": root, "root": root, "rel": rel,
                               "base": base, "count": len(base)})
    return _model_index_cache


def collect_model_refs(workflow) -> list:
    """从工作流里挑出所有「值是模型文件名」的输入。"""
    out = []
    if not isinstance(workflow, dict):
        return out
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        title = ""
        meta = node.get("_meta") or node.get("meta")
        if isinstance(meta, dict):
            title = str(meta.get("title") or "")
        ctype = str(node.get("class_type") or node.get("type") or "")
        for field, value in inputs.items():
            if not isinstance(value, str) or not value.strip():
                continue
            low = value.strip().lower()
            if not low.endswith(MODEL_EXTS):
                continue
            out.append({"node": str(nid), "class_type": ctype, "title": title,
                        "field": str(field), "value": value.strip()})
    return out


def check_workflow_models(name: str = "", workflow=None) -> dict:
    """返回工作流引用的模型里，本地缺哪些。"""
    if workflow is None and name:
        p = os.path.join(PROJECT_DIR, "workflows", name.replace("/", os.sep))
        if not os.path.isfile(p):
            return {"ok": False, "message": "找不到工作流文件：%s" % name}
        try:
            workflow = json.load(open(p, encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "message": "解析工作流失败：%s" % exc}
    if workflow is None:
        return {"ok": False, "message": "没有给工作流"}

    idx = _models_index()
    install = comfy_install_dir()
    if not install:
        return {"ok": False, "message": "还没配 ComfyUI 安装目录"}
    if not idx["root"]:
        return {"ok": False, "message": "在安装目录下找不到 models 文件夹"}

    refs, missing, seen = collect_model_refs(workflow), [], set()
    for r in refs:
        key = (r["node"], r["field"], r["value"])
        if key in seen:
            continue
        seen.add(key)
        low = r["value"].lower()
        if low in idx["rel"] or os.path.basename(low) in idx["base"]:
            continue
        missing.append(r)
    return {"ok": True, "workflow": name, "models_root": idx["root"],
            "indexed": idx["count"], "refs": len(seen), "missing": missing,
            "message": ("缺 %d 个模型" % len(missing)) if missing else "模型齐了"}


def check_all_workflows() -> dict:
    """一次把所有工作流的模型齐备情况过一遍（模型索引只建一次，很快）。"""
    install = comfy_install_dir()
    if not install:
        return {"ok": False, "message": "还没配 ComfyUI 安装目录"}
    idx = _models_index()
    if not idx["root"]:
        return {"ok": False, "message": "在安装目录下找不到 models 文件夹"}

    wf_dir = os.path.join(PROJECT_DIR, "workflows")
    rows, bad = [], []
    if os.path.isdir(wf_dir):
        for root, dirs, files in os.walk(wf_dir):
            for fn in sorted(files, key=str.lower):
                if not fn.lower().endswith(".json") or fn.lower().endswith(".config.json"):
                    continue
                rel = os.path.relpath(os.path.join(root, fn), wf_dir).replace("\\", "/")
                try:
                    wf = json.load(open(os.path.join(root, fn), encoding="utf-8"))
                except Exception as exc:
                    rows.append({"name": rel, "ok": False, "message": "解析失败：%s" % exc})
                    continue
                refs, missing = collect_model_refs(wf), []
                seen = set()
                for r in refs:
                    key = (r["node"], r["field"], r["value"])
                    if key in seen:
                        continue
                    seen.add(key)
                    low = r["value"].lower()
                    if low in idx["rel"] or os.path.basename(low) in idx["base"]:
                        continue
                    missing.append(r["value"])
                rows.append({"name": rel, "ok": True, "refs": len(seen),
                             "missing": missing, "missing_count": len(missing)})
                if missing:
                    bad.append({"name": rel, "missing": missing})
    return {"ok": True, "models_root": idx["root"], "indexed": idx["count"],
            "workflows": len(rows), "bad": bad, "rows": rows,
            "message": ("有 %d 个工作流缺模型" % len(bad)) if bad else "所有工作流的模型都齐"}


# SHBrowseForFolder 用到的常量与结构（ctypes 直接调 shell32，不依赖 tkinter）
BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def _find_own_dialog():
    """找本进程弹出来的那个对话框窗口（类名 #32770）。只认自己的进程，绝不碰别人的窗口。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    mine = os.getpid()
    hits = []

    def _cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != mine:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "#32770":
            return True
        hits.append(hwnd)
        return False

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                          wintypes.LPARAM)(_cb), 0)
    return hits[0] if hits else None


def _force_foreground(hwnd) -> None:
    """把窗口硬拽到最前。

    对话框没 owner 时（有 owner 会弹不出来，见下面注释）Windows 不会把它激活，
    结果就是它开在应用窗口后面、还要用户去任务栏点。这里先挂到前台线程上再
    SetForegroundWindow（不挂的话这个调用会被系统静默忽略），再用 topmost 兜一下。
    """
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if tid_fg and tid_fg != tid_me:
            attached = bool(user32.AttachThreadInput(tid_fg, tid_me, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        # topmost 一下再撤掉：只是借它把窗口顶到最前，不留下"总在最前"的后遗症
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(tid_fg, tid_me, False)
            except Exception:
                pass


def _watch_dialog(stop: "threading.Event", tries: int = 150) -> None:
    """等自己的对话框出现后把它抢到前台（对话框是阻塞弹的，只能另开线程盯着）。"""
    for _ in range(tries):
        if stop.is_set():
            return
        hwnd = _find_own_dialog()
        if hwnd:
            _force_foreground(hwnd)
            return
        time.sleep(0.12)


def pick_folder(initial: str = "") -> dict:
    """弹一个原生「选择文件夹」对话框（浏览器给不了真实路径，只能这边弹）。

    用 shell32 的 SHBrowseForFolderW：项目自带的嵌入式 Python 没有 tkinter
    （确认过 `import tkinter` 会失败），所以不能用 filedialog，只能走 ctypes。
    助手服务是 ThreadingHTTPServer，这里会在请求线程里 CoInitialize 再弹框。
    """
    import ctypes
    from ctypes import wintypes

    class _BROWSEINFOW(ctypes.Structure):
        _fields_ = [("hwndOwner", wintypes.HWND),
                    ("pidlRoot", ctypes.c_void_p),
                    ("pszDisplayName", wintypes.LPWSTR),
                    ("lpszTitle", wintypes.LPCWSTR),
                    ("ulFlags", ctypes.c_uint),
                    ("lpfn", ctypes.c_void_p),
                    ("lParam", ctypes.c_void_p),
                    ("iImage", ctypes.c_int)]

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    # 必须声明 restype：默认按 c_int 处理会把 64 位指针截断，拿回来就是野指针
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BROWSEINFOW)]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    shell32.SHParseDisplayName.restype = ctypes.c_long

    # 不要传 owner：实测把应用窗口（另一个进程）当 owner 时 SHBrowseForFolderW
    # 会立刻返回 NULL，框根本弹不出来。不传 owner 时正常弹，且会自己抢到前台。
    ole32.CoInitialize(None)
    root_pidl = ctypes.c_void_p()
    stop = threading.Event()
    watcher = threading.Thread(target=_watch_dialog, args=(stop,), daemon=True)
    watcher.start()
    try:
        start = initial if (initial and os.path.isdir(initial)) else ""
        if start:
            # 让对话框一开始就停在这个目录
            shell32.SHParseDisplayName(ctypes.c_wchar_p(start), None,
                                       ctypes.byref(root_pidl), 0, None)

        buf = ctypes.create_unicode_buffer(260)
        bi = _BROWSEINFOW()
        bi.hwndOwner = 0
        bi.pidlRoot = root_pidl
        bi.pszDisplayName = ctypes.cast(buf, wintypes.LPWSTR)
        bi.lpszTitle = "选择 ComfyUI 安装目录（里面应有 main.py 和 models 文件夹）"
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE

        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return {"ok": True, "cancelled": True, "message": "没选"}

        out = ctypes.create_unicode_buffer(1024)
        ok = shell32.SHGetPathFromIDListW(ctypes.c_void_p(pidl), out)
        ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        if not ok or not out.value:
            return {"ok": False, "cancelled": False, "message": "没拿到选中的路径"}
        path = _clean_dir(out.value)
    except Exception as exc:
        return {"ok": False, "cancelled": False, "message": "弹选择框失败：%s" % exc}
    finally:
        stop.set()
        try:
            if root_pidl:
                ole32.CoTaskMemFree(root_pidl)
        except Exception:
            pass
        try:
            ole32.CoUninitialize()
        except Exception:
            pass

    log("用户在对话框里选了: %s" % path)
    return {"ok": True, "cancelled": False, "path": path, "report": comfy_report_for(path)}


def comfy_report_for(path: str) -> dict:
    """临时把某条路径当成安装目录来体检（不落盘、不改配置）。"""
    real = load_comfy_config()
    try:
        save_comfy_config({"install_dir": path})
        return comfy_report()
    finally:
        save_comfy_config({"install_dir": real["install_dir"],
                           "auto_start": real["auto_start"]})


def _detect_lora_folders():
    """找常见的 ComfyUI 安装里的 models/loras。"""
    found = []
    for root in _local_fixed_drives():
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if "comfyui" not in name.lower():
                continue
            base = os.path.join(root, name)
            for sub in (os.path.join("ComfyUI", "models", "loras"),
                        os.path.join("models", "loras"),
                        os.path.join("ComfyUI", "models", "Lora")):
                p = os.path.join(base, sub)
                if os.path.isdir(p) and p not in found:
                    found.append(p)
    return found


def lora_folders() -> list:
    """LoRA 目录清单。

    优先用界面上填的 ComfyUI 安装目录（它的 models/loras 说了算），
    没填或那里没有 loras 文件夹时，才回退到 loras.json / 自动探测。
    """
    install = comfy_install_dir()
    if install and os.path.isdir(install):
        from_install = [d for d in comfy_loras_dirs(install) if d]
        if from_install:
            return from_install

    cfg = os.path.join(state_dir(), "loras.json")
    if os.path.isfile(cfg):
        try:
            data = json.load(open(cfg, encoding="utf-8")) or {}
            folders = [str(f) for f in (data.get("folders") or []) if str(f).strip()]
            return [f for f in folders if os.path.isdir(f)] or []
        except Exception as exc:
            log("读 loras.json 失败: %s" % exc)
            return []
    detected = _detect_lora_folders()
    try:
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"folders": detected,
                       "备注": "把 LoRA 目录填到 folders 里，可以多个。改完重启桌面版生效。",
                       "_说明": "自动探测到的目录已填好；如果你把 ComfyUI 放在别处，在这里改。"},
                      fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    log("已生成 LoRA 目录配置: %s（探测到 %d 个）" % (cfg, len(detected)))
    return detected


def _cover_url(q_id: str) -> str:
    """页面拿到的封面地址是「HELPER_BASE + cover」拼出来的，所以这里必须给相对 URL。
    原来直接回磁盘路径，拼出来是坏地址（http://127.0.0.1:8317D:\...），
    于是所有「有本地预览图」的 LoRA 封面全是空占位图。"""
    import urllib.parse
    return "/cover?id=" + urllib.parse.quote(q_id, safe="")


COVER_FAIL_TTL = 3600          # 封面下载失败后 1 小时内不再试
_cover_fail_streak = 0         # 本轮连续失败次数；网络整体不通时别把整轮耗在超时上


def _cover_render_url(url: str, cover_type: str) -> str:
    """复刻 LoRA Manager 的写法：把 `original=true` 换成 450px 优化版。
    图更小、下得更快，C 站也是这么给前端用的。"""
    if "/original=true" not in url:
        return url
    rep = ("/transcode=true,width=450,optimized=true" if cover_type == "video"
           else "/width=450,optimized=true")
    return url.replace("/original=true", rep, 1)


def _cover_target(full: str, url: str, cover_type: str) -> str:
    """封面存哪：和 LoRA 同目录、同名（LoRA Manager 的做法，用户也这么要求）。
    这样封面是本地的，页面、ComfyUI、别的插件都能直接用，也不怕图床被墙。"""
    import urllib.parse
    base, _ = os.path.splitext(full)
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if cover_type == "video":
        ext = ext if ext in (".mp4", ".webm") else ".mp4"
    elif ext not in (".jpeg", ".jpg", ".png", ".webp", ".gif"):
        ext = ".jpeg"
    return base + ext


def _download_cover(full: str, url: str, cover_type: str) -> tuple:
    """把封面图下到 LoRA 同目录。返回 (是否成功, 错误信息)。"""
    global _cover_fail_streak
    if not url:
        return False, "no url"
    dst = _cover_target(full, url, cover_type)
    if os.path.isfile(dst):
        return True, ""
    import urllib.request
    err = ""
    for cand in (_cover_render_url(url, cover_type), url):
        try:
            req = urllib.request.Request(cand, headers={"User-Agent": "Mozilla/5.0 AIStudioHelper"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read(24 * 1024 * 1024)
            if len(data) < 512:
                err = "内容太小"
                continue
            tmp = dst + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dst)
            _cover_fail_streak = 0
            return True, ""
        except Exception as exc:
            err = str(exc)[:60]
    _cover_fail_streak += 1
    return False, err


def _cover_budget_ok() -> bool:
    """本轮还能不能再试下载封面（连续失败太多说明图床不通，别把整轮耗在超时上）。"""
    return _cover_fail_streak < 5


def _cover_mark_skipped() -> None:
    global _cover_fail_streak
    if _cover_fail_streak == 5:
        log("封面连续下载失败，本轮不再尝试（可能图床不通）")
    _cover_fail_streak += 1


def _cover_is_video(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in COVER_VIDEO_EXTS


def _cover_for(path: str):
    """找 LoRA 的封面：**同目录同名**，图片（.png/.jpg/.jpeg/.webp/.gif/.avif）或
    视频（.mp4/.webm —— C 站的视频预览，LoRA Manager 下到同目录的那批），
    也认 xxx.preview.<ext> 写法。"""
    base, _ = os.path.splitext(path)
    for ext in COVER_EXTS + COVER_VIDEO_EXTS:
        for cand in (base + ext, base + ".preview" + ext):
            if os.path.isfile(cand):
                return cand
    return None


_lora_cache = {"at": 0.0, "data": None}


def scan_loras(force: bool = False, ttl: float = 20.0) -> dict:
    """扫描所有 LoRA 目录。带短缓存，避免界面反复刷新时反复走盘。"""
    now = time.time()
    if not force and _lora_cache["data"] and now - _lora_cache["at"] < ttl:
        return _lora_cache["data"]

    folders = lora_folders()
    civ_cache = _read_dict_store(LORA_CIVITAI_CACHE)   # 整个循环只读一次
    items = []
    for ri, folder in enumerate(folders):
        for root, dirs, files in os.walk(folder):
            depth = root[len(folder):].count(os.sep)
            if depth > 4:
                dirs[:] = []
                continue
            for fn in files:
                if not fn.lower().endswith(LORA_EXTS):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, folder).replace("\\", "/")
                try:
                    st = os.stat(full)
                    size, mtime = st.st_size, int(st.st_mtime)
                except OSError:
                    size, mtime = 0, 0
                cover = _cover_for(full)
                cover_type = "image"
                if cover:
                    # 视频封面必须标成 video，否则页面会拿 <img> 去加载 mp4，照样显示不出来
                    cover_type = "video" if _cover_is_video(cover) else "image"
                    cover = _cover_url("%d:%s" % (ri, rel))    # 必须是相对 URL，见 _cover_url
                if not cover:
                    # 本地没有封面图：用「获取」时同步回来的 Civitai 官方媒体
                    # （可能是图片，也可能是 C 站的视频预览）
                    ent = civ_cache.get("%d:%s" % (ri, rel))
                    if isinstance(ent, dict) and ent.get("ok") and ent.get("cover_url"):
                        cover = ent["cover_url"]
                        cover_type = ent.get("cover_type") or "image"
                items.append({
                    "id": "%d:%s" % (ri, rel),        # 给封面接口用
                    "rel": rel,                       # 相对目录，可直接填进工作流 lora_name
                    "name": os.path.splitext(fn)[0],
                    "folder": folder,
                    "size": size,
                    "mtime": mtime,
                    "cover": cover,
                    "cover_type": cover_type,
                })
    items.sort(key=lambda x: x["rel"].lower())
    data = {"folders": folders, "loras": items, "scanned_at": int(now),
            "config_path": os.path.join(state_dir(), "loras.json")}
    _lora_cache.update({"at": now, "data": data})
    return data


def list_workflow_files() -> list:
    """列项目 workflows/ 下的全部工作流（包含内置的，应用自己的接口会把内置的过滤掉）。"""
    wf_dir = os.path.join(PROJECT_DIR, "workflows")
    out = []
    if not os.path.isdir(wf_dir):
        return out
    for root, dirs, files in os.walk(wf_dir):
        for fn in sorted(files):
            if not fn.endswith(".json") or fn.endswith(".config.json"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), wf_dir).replace("\\", "/")
            cfg = os.path.join(root, fn[:-5] + ".config.json")
            title = rel[:-5]
            fields = 0
            if os.path.isfile(cfg):
                try:
                    c = json.load(open(cfg, encoding="utf-8")) or {}
                    title = c.get("title") or title
                    fields = len(c.get("fields") or [])
                except Exception:
                    pass
            out.append({"name": rel, "title": title, "fields": fields,
                        "is_builtin": "/" not in rel})
    return out


# ================= 提示词模板 / 生成历史 / 模型替换 =================
# 这三块的数据都在用户目录，由助手服务读写 —— 后端 main.py 一行都不用动。

def _store_file(fname: str) -> str:
    return os.path.join(state_dir(), fname)


def _load_store(fname: str) -> list:
    """读一个 JSON 数组文件。读不到或坏了都当空列表，别把界面搞崩。"""
    path = _store_file(fname)
    if not os.path.isfile(path):
        return []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        log("读 %s 失败: %s" % (fname, exc))
        return []
    return data if isinstance(data, list) else []


_STORE_LOCK = threading.Lock()      # 同进程内的写者排队（批量获取 / 详情页重取）


def _save_store(fname: str, items: list) -> None:
    """先写临时文件再替换，避免写一半留下半个坏 JSON。

    加锁 + 替换重试：缓存文件十几 MB、写的人多，之前并发写直接把它写坏过
    （一个线程在写 tmp、另一个在 replace → WinError 32/5，最后落盘的是残文件，
    触发词和远端封面全丢）。替换失败大多是瞬时占用，等 0.3 秒重试。
    """
    path = _store_file(fname)
    tmp = path + ".tmp"
    with _STORE_LOCK:
        for attempt in range(5):
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(items, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
                return
            except Exception as exc:
                if attempt == 4:
                    log("写 %s 失败: %s" % (fname, exc))
                else:
                    time.sleep(0.3)


def _new_id(prefix: str) -> str:
    return "%s%s-%s" % (prefix, time.strftime("%Y%m%d%H%M%S"), os.urandom(3).hex())


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 提示词模板 ----------

def list_templates() -> list:
    return _load_store(TEMPLATES_FILE)


def templates_action(body: dict) -> dict:
    """action: save（不带 id 是新增，带 id 是覆盖）| delete"""
    body = body or {}
    action = str(body.get("action") or "").strip()
    items = _load_store(TEMPLATES_FILE)

    if action == "save":
        text = str(body.get("text") or "")
        if not text.strip():
            return {"ok": False, "message": "提示词是空的，没什么可存的", "items": items}
        name = str(body.get("name") or "").strip()
        if not name:
            first = (text.strip().splitlines() or [""])[0].strip()
            name = (first[:24] + "…") if len(first) > 24 else (first or "未命名模板")
        tid = str(body.get("id") or "").strip()
        hit = None
        for it in items:
            if tid and str(it.get("id")) == tid:
                hit = it
                break
        if hit is None:
            hit = {"id": _new_id("t"), "created": _now_text()}
            items.insert(0, hit)
        hit["name"] = name
        hit["text"] = text
        # 可选 loras：[{rel, name, strength}]。界面每次保存都带完整状态，
        # 所以没带 loras 就视为「纯提示词模板」，清掉旧值保持幂等。
        raw_loras = body.get("loras")
        if isinstance(raw_loras, list):
            clean = []
            for it in raw_loras[:32]:
                if not isinstance(it, dict):
                    continue
                rel = str(it.get("rel") or "").strip()
                if not rel:
                    continue
                try:
                    strength = float(it.get("strength", 1.0))
                except (TypeError, ValueError):
                    strength = 1.0
                strength = min(1.5, max(0.0, strength))
                clean.append({"rel": rel, "name": str(it.get("name") or rel)[:120],
                              "strength": round(strength, 2)})
            hit["loras"] = clean
        else:
            hit.pop("loras", None)
        # 分类 / 收藏 / 封面（2026-09-26 加）：页面每次保存都会带上这些键；**没带就保留原值**
        # —— 重命名那条路只传 name，不能把已有的分类和收藏抹掉。
        # 2026-09-28 起分类可由用户自定义：不再写死 act/char/video，只校验「这个 key 现在确实
        # 存在」（见 _load_template_cats）；空串 / 已删掉的 key 一律当"未分类"。
        if "category" in body:
            cat = str(body.get("category") or "").strip().lower()
            hit["category"] = cat if any(x["key"] == cat for x in _load_template_cats()) else ""
        if "fav" in body:
            hit["fav"] = bool(body.get("fav"))
        if "cover" in body:
            # 封面可能是「导入的图片」的 data URL（几百 KB），别截断成 200 字符
            hit["cover"] = str(body.get("cover") or "").strip()[:400000]
        hit.setdefault("category", "")
        hit.setdefault("fav", False)
        hit.setdefault("cover", "")
        hit["updated"] = _now_text()
        _save_store(TEMPLATES_FILE, items)
        return {"ok": True, "items": items, "id": hit["id"],
                "message": "已存为模板「%s」" % name}

    if action == "delete":
        tid = str(body.get("id") or "").strip()
        left = [it for it in items if str(it.get("id")) != tid]
        if len(left) == len(items):
            return {"ok": False, "message": "没找到这个模板", "items": items}
        _save_store(TEMPLATES_FILE, left)
        return {"ok": True, "items": left, "message": "模板已删除"}

    return {"ok": False, "message": "不认识的 action: %s" % action, "items": items}


# ---------- 提示词模板「分类」（2026-09-28）----------
# 原来是写死的三个（前端 TPL_CATS + 这里的白名单），现在允许用户自己新建 / 改名 / 删除。
# 存 state_dir() 下；列表顺序 = 页面上的页签顺序。key 是稳定标识（内置 act/char/video，
# 自定义的用 u+随机），**改名只动 name、不动 key** —— 否则已存进模版里的 category 会全部失联。
TEMPLATE_CATS_FILE = "template_categories.json"

_DEFAULT_TEMPLATE_CATS = [
    {"key": "act", "name": "动作"},
    {"key": "char", "name": "角色"},
    {"key": "video", "name": "视频"},
]


def _load_template_cats() -> list:
    data = _read_dict_store(TEMPLATE_CATS_FILE)
    items = data.get("items")
    if not isinstance(items, list):
        # 文件还不存在（第一次用）→ 用内置那三个。注意**空列表是有效值**：
        # 用户把分类删光了就该是空的，不能把内置的又变回来。
        return [dict(x) for x in _DEFAULT_TEMPLATE_CATS]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        name = str(it.get("name") or "").strip()
        if key and name:
            out.append({"key": key[:40], "name": name[:24]})
    return out


def _save_template_cats(items: list) -> None:
    _save_store(TEMPLATE_CATS_FILE, {"items": items})


def template_cats_get() -> dict:
    return {"ok": True, "items": _load_template_cats()}


def template_cats_action(body: dict) -> dict:
    """action: add {name} | rename {key,name} | delete {key}

    删除时把该分类下的模版改回「未分类」—— 不然那些模版会挂在一个不存在的 key 上，
    页面上就成了"看不见"的孤儿。
    """
    body = body or {}
    action = str(body.get("action") or "").strip()
    items = _load_template_cats()
    keys = [x["key"] for x in items]

    def _dup(name, skip=None):
        return any(x["name"] == name and x["key"] != skip for x in items)

    if action == "add":
        name = str(body.get("name") or "").strip()[:24]
        if not name:
            return {"ok": False, "message": "分类名不能为空", "items": items}
        if _dup(name):
            return {"ok": False, "message": "已经有「%s」这个分类了" % name, "items": items}
        key = "u" + os.urandom(4).hex()
        while key in keys:
            key = "u" + os.urandom(4).hex()
        items.append({"key": key, "name": name})
        _save_template_cats(items)
        return {"ok": True, "items": items, "key": key, "message": "已新建分类「%s」" % name}

    key = str(body.get("key") or "").strip()
    if key not in keys:
        return {"ok": False, "message": "没找到这个分类", "items": items}

    if action == "rename":
        name = str(body.get("name") or "").strip()[:24]
        if not name:
            return {"ok": False, "message": "分类名不能为空", "items": items}
        if _dup(name, skip=key):
            return {"ok": False, "message": "已经有「%s」这个分类了" % name, "items": items}
        for x in items:
            if x["key"] == key:
                x["name"] = name
        _save_template_cats(items)
        return {"ok": True, "items": items, "message": "已改名为「%s」" % name}

    if action == "delete":
        items = [x for x in items if x["key"] != key]
        _save_template_cats(items)
        moved = 0
        tpls = _load_store(TEMPLATES_FILE)
        for t in tpls:
            if isinstance(t, dict) and str(t.get("category") or "") == key:
                t["category"] = ""
                moved += 1
        if moved:
            _save_store(TEMPLATES_FILE, tpls)
        msg = "分类已删除" + ("，%d 个模版已回到「未分类」" % moved if moved else "")
        return {"ok": True, "items": items, "message": msg}

    return {"ok": False, "message": "不认识的 action: %s" % action, "items": items}


# ---------- 上次参数（比例 / 清晰度 / 时长 / 种子 / LoRA 选择）----------
# 注意不能用 _load_store：那套是为「JSON 数组」设计的（模板/历史），
# 遇到字典会整个丢掉。这里单独读，只认字典。

def list_ui_state() -> dict:
    path = _store_file(UI_STATE_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        log("读 %s 失败: %s" % (UI_STATE_FILE, exc))
        return {}
    return data if isinstance(data, dict) else {}


def _clean_ui_loras(raw) -> list:
    """LoRA 选择只留 rel / name / strength（页面靠 rel 回扫描结果里找回封面和 id）。
    强度夹到 0–1.5、最多 60 条 —— 存档是给界面读的，不让奇怪的东西塞进来。"""
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw[:60]:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("rel") or "").strip()
        if not rel:
            continue
        try:
            w = float(it.get("strength", 1.0))
        except (TypeError, ValueError):
            w = 1.0
        out.append({"rel": rel, "name": str(it.get("name") or rel),
                    "strength": min(1.5, max(0.0, round(w, 2)))})
    return out


def save_ui_state(body: dict) -> dict:
    """只认白名单字段，逐个覆盖；没带的字段保持原值。"""
    body = body or {}
    cur = list_ui_state()
    for k in ("ratio", "megapixels", "duration", "seed"):
        if k in body:
            cur[k] = body[k]
    if "loras" in body:
        cur["loras"] = _clean_ui_loras(body.get("loras"))
    cur["updated"] = _now_text()
    _save_store(UI_STATE_FILE, cur)
    return {"ok": True, "state": cur}


# ---------- LoRA 触发词 / Civitai 信息 ----------
# 数据来源（按优先级）：
#   1. 用户在详情页编辑过的（存 lora_triggers.json，始终最优先）
#   2. 旁车文件：Civitai Helper 存的 <文件名>.civitai.info / .json（含 trainedWords + modelId）
#   3. safetensors 头部 __metadata__（部分下载器会写 civitai 信息；ss_tag_frequency
#      是训练标签频次，拿来当「词语建议」）
# 触发词不是必须存在的数据，所有失败都安静降级为「没找到」。

def _read_dict_store(fname: str) -> dict:
    path = _store_file(fname)
    if not os.path.isfile(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log("读 %s 失败: %s" % (fname, exc))
        # 文件坏了就退回上次快照（每次「获取 Civitai」开跑前会自动存一份 .bak）。
        # 2026-09-25 真的丢过一次 Civitai 缓存（1300 条触发词/封面），加这道兜底。
        bak = path + ".bak"
        if os.path.isfile(bak):
            try:
                data = json.load(open(bak, encoding="utf-8"))
                if isinstance(data, dict) and data:
                    log("已用快照恢复 %s（%d 条）" % (fname, len(data)))
                    return data
            except Exception:
                pass
        return {}


def _safetensors_header_meta(path: str) -> dict:
    """读 safetensors 头部：前 8 字节是小端 u64 的头长度，跟着一段 JSON。"""
    try:
        with open(path, "rb") as fh:
            n = int.from_bytes(fh.read(8), "little")
            if n <= 0 or n > 64 * 1024 * 1024:
                return {}
            raw = fh.read(n)
        data = json.loads(raw.decode("utf-8", "replace"))
        meta = data.get("__metadata__")
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _lora_detect_info(full: str):
    """返回 (触发词列表, civitai地址, 来源, 示例图列表)。都找不到就空。"""
    words, url, source, images = [], "", "none", []
    base = os.path.splitext(full)[0]
    for cand, kind in ((base + ".civitai.info", "civitai.info"), (base + ".json", "json")):
        if not os.path.isfile(cand):
            continue
        try:
            d = json.load(open(cand, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        tw = d.get("trainedWords")
        if isinstance(tw, list):
            words = [str(w).strip() for w in tw if str(w).strip()][:64]
        mid = d.get("modelId") or d.get("model_id")
        vid = d.get("id")
        if mid:
            try:
                url = "https://%s/models/%d" % (_civitai_domain(), int(mid))
                if vid:
                    url += "?modelVersionId=%d" % int(vid)
            except (TypeError, ValueError):
                url = ""
        imgs = d.get("images")
        if isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, dict):
                    u = it.get("url")
                    # 每张示例图可能带 meta（C 站生成参数：prompt/负参/步数/采样器…）
                    meta = it.get("meta")
                    meta = meta if isinstance(meta, dict) else None
                else:
                    u, meta = it, None
                if isinstance(u, str) and u.startswith("http"):
                    images.append({"url": u, "meta": meta})
                if len(images) >= 30:
                    break
        if words or url or images:
            return words, url, kind, images

    meta = _safetensors_header_meta(full)
    civ = meta.get("civitai")
    if isinstance(civ, str) and civ.strip():
        try:
            civ = json.loads(civ)
        except Exception:
            civ = {}
    if isinstance(civ, dict):
        tw = civ.get("trainedWords")
        if isinstance(tw, list):
            words = [str(w).strip() for w in tw if str(w).strip()][:64]
        mid = civ.get("modelId")
        if mid:
            try:
                url = "https://%s/models/%d" % (_civitai_domain(), int(mid))
            except (TypeError, ValueError):
                url = ""
        if words or url:
            return words, url, "header", images

    # 没有官方触发词时：从训练标签频次推断（C 站的触发词就是这么算的）
    tags = _tags_from_freq(full, 20)
    if tags:
        return tags, url, "tags", images
    return words, url, "none", images


_TAG_BLOCK = {"masterpiece", "best quality", "worst quality", "low quality",
              "normal quality", "high quality", "quality", "4k", "8k", "highscore",
              "ultra detailed", "hyper detailed", "highres", "hi-res", "absurdres", "hd"}


def _tags_from_freq(full: str, limit: int = 20) -> list:
    """从 safetensors 头的 ss_tag_frequency（训练标签频次）推断触发词。
    C 站的 trainedWords 本来就是靠这个算的，取高频标签并滤掉纯质量词。"""
    meta = _safetensors_header_meta(full)
    raw = meta.get("ss_tag_frequency")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        freq = json.loads(raw)
    except Exception:
        return []
    counts = {}
    if isinstance(freq, dict):
        for dataset in freq.values():
            if isinstance(dataset, dict):
                for tag, c in dataset.items():
                    try:
                        counts[tag] = counts.get(tag, 0) + int(c)
                    except (TypeError, ValueError):
                        pass
    ranked = [t.strip() for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])
              if t.strip() and t.strip().lower() not in _TAG_BLOCK]
    return ranked[:limit]


def _lora_tag_suggestions(full: str, exclude: list) -> list:
    """从头部 ss_tag_frequency 聚合高频训练标签，当「词语建议」。"""
    meta = _safetensors_header_meta(full)
    raw = meta.get("ss_tag_frequency")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        freq = json.loads(raw)
    except Exception:
        return []
    counts = {}
    if isinstance(freq, dict):
        for dataset in freq.values():
            if isinstance(dataset, dict):
                for tag, c in dataset.items():
                    try:
                        counts[tag] = counts.get(tag, 0) + int(c)
                    except (TypeError, ValueError):
                        pass
    ex = {w.lower() for w in exclude}
    out = [t.strip() for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])
           if t.strip() and t.strip().lower() not in ex]
    return out[:40]


def _lora_file_by_id(q_id: str):
    """跟 /cover 一样的定位规则：id = <目录序号>:<相对路径>，只许在配置目录里。"""
    if ":" not in q_id:
        return None
    try:
        ri, rel = q_id.split(":", 1)
        folder = lora_folders()[int(ri)]
    except Exception:
        return None
    full = os.path.abspath(os.path.join(folder, rel.replace("/", os.sep)))
    if not full.startswith(os.path.abspath(folder)) or not os.path.isfile(full):
        return None
    return full


def _sha256_of_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _slim_meta(meta) -> dict:
    """Civitai 的图片 meta 里常带完整 ComfyUI 工作流图（comfy 字段，单张能到 1MB），
    我们只用到 prompt / 负向提示词 和几个短参数，其余一律丢掉，省磁盘。"""
    if not isinstance(meta, dict):
        return {}
    keep = {}
    for k, v in meta.items():
        if k in ("prompt", "negativePrompt", "negative_prompt"):
            if isinstance(v, str):
                keep[k] = v[:4000]
            continue
        if isinstance(v, (dict, list)) or v is None:
            continue                       # comfy / resources 这类大结构直接丢
        sv = str(v)
        if len(sv) <= 100:                 # 界面只显示短参数（步数、采样器、种子…）
            keep[k] = v
    return keep


_domain_ok = {}          # 域名可达性（每轮获取测一次；被墙的直接跳过，别每个 LoRA 都等超时）


def _civitai_backoff(ent) -> float:
    """失败后的冷却时间：C 站明确没收录（404）→ 7 天；其它失败 → 1 小时。"""
    return 7 * 86400.0 if "404" in str((ent or {}).get("err") or "") else 3600.0


def _probe_domain(dom: str) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request("https://%s/api/v1/models?limit=1" % dom,
                                     headers={"User-Agent": "Mozilla/5.0 AIStudioHelper"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status < 500
    except Exception:
        return False


def _civitai_bases() -> list:
    """要试的 API 域名：先按可达性筛一遍（配置的域名排第一）。"""
    dom = _civitai_domain()
    out = []
    for d in [dom] + [x for x in ("civitai.com", "civitai.red") if x != dom]:
        ok = _domain_ok.get(d)
        if ok is None:
            ok = _probe_domain(d)
            _domain_ok[d] = ok
            log("C 站域名 %s 可达: %s" % (d, ok))
        if ok:
            out.append(d)
    return out or [dom]      # 都探不通就用配置的那个，让错误信息照常出来


def _civitai_by_hash_cached(full: str, q_id: str, force: bool = False,
                            cache: dict = None) -> dict:
    """复刻 LoRA Manager 的办法：算文件 SHA256 → 拿哈希去 Civitai 反查版本数据
    （官方触发词 / 模型页 / 带生成参数的示例图）。结果按 文件大小+mtime 缓存，
    失败结果 1 小时内不重复打网络。force=True 时无视缓存强制重查（详情页的
    「重新获取」按钮用）。"""
    result = {"ok": False}
    try:
        st = os.stat(full)
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        return result
    own_cache = cache is None
    if own_cache:
        # 批量获取时由调用方把缓存传进来（整批只读一次）；原来这里每个文件都读一次
        # 十几 MB 的缓存 —— 1542 次读盘，又慢，又是并发写坏的根源之一。
        cache = _read_dict_store(LORA_CIVITAI_CACHE)
    ent = cache.get(q_id)
    if not force and isinstance(ent, dict) and ent.get("size") == size and ent.get("mtime") == mtime:
        if ent.get("ok"):
            return ent                                  # 缓存命中，秒回
        if time.time() - float(ent.get("try_at", 0)) < _civitai_backoff(ent):
            return ent                                  # 失败过（404 等 7 天，其它 1 小时）
    try:
        sha = _sha256_of_file(full)
    except Exception as exc:
        result.update({"try_at": time.time(), "err": str(exc)[:80]})
        cache[q_id] = result
        if own_cache:
            _save_store(LORA_CIVITAI_CACHE, cache)
        return result
    import urllib.error
    import urllib.request
    bases = ["https://%s/api/v1" % d for d in _civitai_bases()]
    for base in bases:
        try:
            req = urllib.request.Request(
                base + "/model-versions/by-hash/" + sha,
                headers={"User-Agent": "Mozilla/5.0 AIStudioHelper"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and data.get("modelId"):
                result["words"] = [str(x).strip() for x in (data.get("trainedWords") or [])
                                   if str(x).strip()][:64]
                result["model_id"] = int(data["modelId"])
                result["version_id"] = int(data["id"])
                _imgs = data.get("images") or []
                if isinstance(_imgs, list) and _imgs and isinstance(_imgs[0], dict):
                    result["cover_url"] = _imgs[0].get("url") or ""   # 远端封面（Civitai 官方媒体）
                    result["cover_type"] = _imgs[0].get("type") or "image"  # image / video
                imgs = []
                for it in (data.get("images") or [])[:20]:
                    if isinstance(it, dict) and isinstance(it.get("url"), str):
                        imgs.append({"url": it["url"],
                                     "meta": _slim_meta(it.get("meta")),
                                     "type": it.get("type") or "image"})
                result["images"] = imgs
                result["ok"] = True
                break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # C 站明确没收录这个模型：换域名也是白等（那个域名还被墙），直接收工
                result["err"] = "404 未收录"
                break
            result["err"] = "HTTP %s" % exc.code
        except Exception as exc:
            result["err"] = str(exc)[:80]
    result.update({"size": size, "mtime": mtime, "sha256": sha,
                   "try_at": time.time()})
    cache[q_id] = result
    if own_cache:
        _save_store(LORA_CIVITAI_CACHE, cache)
    return result


LORA_SEARCH_CACHE = {}


def lora_search(q: str, site: str = "civitai") -> dict:
    """本地缺失的 LoRA：**程序自己去搜**（Civitai / HuggingFace 的公开 API）。

    用户 2026-09-26 明确要求："本地没有的 lora 我是让你去搜，别写个让用户去哪里搜"。
    结果按 查询+站点 缓存 10 分钟，避免同一个名字被反复打。
    """
    q = (q or "").strip()
    site = "hf" if str(site or "").strip().lower() in ("hf", "huggingface") else "civitai"
    if not q:
        return {"ok": False, "message": "没有搜索词", "items": [], "site": site, "q": q}
    key = site + "|" + q.lower()
    hit = LORA_SEARCH_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    import urllib.request as _ur, urllib.parse as _up
    items, err = [], ""
    try:
        if site == "hf":
            url = ("https://huggingface.co/api/models?search=%s&limit=6"
                   % _up.quote(q))
        else:
            url = ("https://%s/api/v1/models?query=%s&types=LORA&limit=6"
                   % (_civitai_domain(), _up.quote(q)))
        req = _ur.Request(url, headers={
            "User-Agent": "AI-Studio-Helper/1.0", "Accept": "application/json"})
        with _ur.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        if site == "hf":
            for it in (data or [])[:6]:
                mid = it.get("modelId") or it.get("id") or ""
                items.append({"name": mid, "url": "https://huggingface.co/" + mid,
                              "extra": "downloads %s" % (it.get("downloads") or 0)})
        else:
            for it in ((data or {}).get("items") or [])[:6]:
                mid = it.get("id")
                st = it.get("stats") or {}
                items.append({"name": it.get("name") or "",
                              "url": "https://%s/models/%s" % (_civitai_domain(), mid),
                              "extra": "downloads %s / rating %s"
                                       % (st.get("downloadCount") or 0, st.get("ratingCount") or 0)})
    except Exception as e:
        err = str(e)
    payload = {"ok": not err, "site": site, "q": q, "items": items, "message": err}
    if not err:
        LORA_SEARCH_CACHE[key] = (time.time(), payload)
    return payload


def _civitai_domain() -> str:
    """用户在设置里选的 Civitai 域名（civitai.red / civitai.com）。"""
    dom = str(load_comfy_config().get("civitai_domain") or "civitai.red").strip().lower()
    return dom if dom in ("civitai.com", "civitai.red") else "civitai.red"


_lora_fetch_state = {"running": False, "stop": False, "total": 0, "done": 0, "current": ""}


def _lora_fetch_need(cache: dict, items: list) -> list:
    """挑出「还没齐」的那些，两种情况都要排：
      ① 没元数据（触发词/链接）；② 有元数据但封面还没落到本地。

    「有数据的别再去查一遍」：大小+mtime 命中且数据在 → 跳过；封面失败过的 1 小时内不重试。
    进度条反映的就是这两类真实待办量（原来会把 1542 个全排一遍，看着像每次都在重刷）。
    """
    need = []
    now = time.time()
    for qid, full in items:
        try:
            s = os.stat(full)
            size, mtime = s.st_size, int(s.st_mtime)
        except OSError:
            continue
        ent = cache.get(qid)
        fresh = (isinstance(ent, dict) and ent.get("size") == size
                 and ent.get("mtime") == mtime)
        meta_ok = fresh and bool(ent.get("ok"))
        cover_local = bool(_cover_for(full))
        cover_tried = fresh and (now - float(ent.get("cover_try_at", 0)) < COVER_FAIL_TTL)
        if meta_ok and (cover_local or not ent.get("cover_url") or cover_tried):
            continue                                  # 数据和封面都齐了（或封面刚试过）
        if not meta_ok and fresh and not ent.get("ok") and now - float(ent.get("try_at", 0)) < _civitai_backoff(ent):
            continue                                  # 刚失败过，1 小时内不重试
        need.append((qid, full))
    return need


def _lora_fetch_worker(only_qid=None):
    st = _lora_fetch_state
    st["err"] = ""
    try:
        cache = _read_dict_store(LORA_CIVITAI_CACHE)   # 整批只读一次
        if not only_qid:
            # 开跑前存一份快照：万一这轮把文件写坏了，下次读的时候能自动退回来
            try:
                shutil.copy2(_store_file(LORA_CIVITAI_CACHE),
                             _store_file(LORA_CIVITAI_CACHE) + ".bak")
            except Exception as exc:
                log("缓存快照失败: %s" % exc)
        _domain_ok.clear()          # 每轮重新测一遍域名可达性
        if only_qid:
            full = _lora_file_by_id(only_qid)
            if not full:
                st["err"] = "找不到文件: %s" % only_qid
                log("获取失败：%s" % st["err"])
                return
            items = [(only_qid, full)]
        else:
            lst = scan_loras(force=True)["loras"]
            items = [(l["id"], os.path.join(l["folder"], l["rel"].replace("/", os.sep)))
                     for l in lst]
            items = _lora_fetch_need(cache, items)
        st["total"] = len(items)
        st["done"] = 0
        for i, (qid, full) in enumerate(items, 1):
            if st["stop"]:
                break
            st["current"] = qid
            if os.path.isfile(full):
                try:
                    ent = _civitai_by_hash_cached(full, qid, force=False, cache=cache)
                except Exception as exc:
                    ent = {"ok": False}
                    log("获取单个失败 %s: %s" % (qid, exc))
                # 顺带把封面图下到 LoRA 同目录（LoRA Manager 的做法）。本地有图之后
                # 就不依赖被墙的图床了，页面也直接读本地图。
                if (isinstance(ent, dict) and ent.get("ok") and ent.get("cover_url")
                        and not _cover_for(full)):
                    if _cover_budget_ok():
                        okc, errc = _download_cover(full, ent["cover_url"],
                                                    ent.get("cover_type") or "image")
                        if okc:
                            ent.pop("cover_try_at", None)
                        else:
                            ent["cover_try_at"] = time.time()
                            log("封面下载失败 %s: %s" % (qid, errc))
                    else:
                        _cover_mark_skipped()
            st["done"] = i
            if i % 25 == 0:
                _save_store(LORA_CIVITAI_CACHE, cache)   # 定期落盘，不每步都写满盘
        _save_store(LORA_CIVITAI_CACHE, cache)
    except Exception:
        import traceback as _tb
        st["err"] = _tb.format_exc()[-400:]
        log("获取线程异常: " + st["err"])
    finally:
        st["running"] = False


def lora_fetch_start(only_qid=None) -> dict:
    st = _lora_fetch_state
    if st["running"]:
        if not st["stop"]:
            out = dict(st)                  # 已经在跑：把当前进度给它，别再开一个线程
            out.update({"ok": True})
            return out
        # 上一次刚被取消、线程还在收尾（可能正卡在某个请求上）：等它退出再开新的，
        # 否则这次获取会因为 stop 还挂着而立刻空转结束。
        for _ in range(30):
            if not st["running"]:
                break
            time.sleep(0.1)
        if st["running"]:
            out = dict(st)
            out.update({"ok": True, "busy": True})
            return out
    # 关键：先把 running 立起来再起线程。否则前端第一次轮询就看到 running=false，
    # 会以为已经获取完了 —— 进度窗 1 秒后自己关掉，而后台其实还在跑。
    st.update({"running": True, "stop": False, "err": "",
               "total": 0, "done": 0, "current": ""})
    threading.Thread(target=_lora_fetch_worker, args=(only_qid,), daemon=True).start()
    time.sleep(0.2)
    out = dict(st)
    out.update({"ok": True})
    return out


def lora_fetch_stop() -> dict:
    """请求停止获取：worker 处理完当前文件后跳出循环（不打断已发出的那个请求）。"""
    st = _lora_fetch_state
    if st["running"]:
        st["stop"] = True
    log("已请求停止 Civitai 获取")
    out = dict(st)
    out.update({"ok": True})
    return out


def lora_fetch_status() -> dict:
    st = dict(_lora_fetch_state)
    st["ok"] = True
    return st


# ---------- 模型替换（按工作流保存，下次自动带上） ----------

def model_overrides_get(name: str) -> dict:
    name = (name or "").strip()
    data = _read_dict_store(MODEL_OVERRIDES_FILE)
    ent = data.get(name)
    return {"ok": True, "name": name, "overrides": ent if isinstance(ent, dict) else {}}


def model_overrides_save(body: dict) -> dict:
    body = body or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "缺少工作流名"}
    ov = body.get("overrides")
    data = _read_dict_store(MODEL_OVERRIDES_FILE)
    if ov is None:
        data.pop(name, None)
    elif isinstance(ov, dict):
        clean = {}
        for k, v in ov.items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                clean[k] = v
        if clean:
            data[name] = clean
        else:
            data.pop(name, None)      # 空 = 清除保存
    else:
        return {"ok": False, "message": "overrides 必须是对象"}
    _save_store(MODEL_OVERRIDES_FILE, data)
    n = len(data.get(name) or {})
    return {"ok": True, "name": name, "count": n,
            "message": ("已保存 %d 个模型替换" % n) if n else "已清除保存的模型替换"}


# ---------- 素材库「收藏」（2026-09-28）----------
# 视频页弹窗和左栏素材库页**共用同一份收藏**，存 state_dir() 下（不是浏览器 localStorage，
# 免得清 profile / 换机器就丢）。收藏的条目在两处素材库里都排在前面。
ASSET_FAVORITES_FILE = "asset_favorites.json"


def _asset_favorite_ids() -> list:
    data = _read_dict_store(ASSET_FAVORITES_FILE)
    ids = data.get("ids")
    return [str(x) for x in ids if isinstance(x, str)] if isinstance(ids, list) else []


def asset_favorites_get() -> dict:
    return {"ok": True, "ids": _asset_favorite_ids()}


def asset_favorites_toggle(body: dict) -> dict:
    """单个 id 的收藏/取消收藏。做成"切换单个"而不是"整份覆盖"，
    是为了避免两个页面各存一份列表互相覆盖。"""
    body = body or {}
    item_id = str(body.get("id") or "").strip()
    if not item_id:
        return {"ok": False, "message": "缺少 id"}
    ids = _asset_favorite_ids()
    if item_id in ids:
        ids = [x for x in ids if x != item_id]
        fav = False
    else:
        ids.insert(0, item_id)
        fav = True
    _save_store(ASSET_FAVORITES_FILE, {"ids": ids})
    return {"ok": True, "id": item_id, "fav": fav, "ids": ids}


def _trig_prune_active(active, words) -> list:
    """激活表只保留「现在还在词表里」的词。

    C 站那种整段 tag 列表是一条存储、页面上按逗号拆成多个芯片，
    所以「在不在词表里」要按拆开后的词判断，不能只比原串。
    """
    pool = set()
    for w in (words or []):
        cur, depth = "", 0
        for ch in str(w):
            if ch in "(（":
                depth += 1
                cur += ch
            elif ch in ")）":
                depth = max(0, depth - 1)
                cur += ch
            elif ch in ",，" and depth == 0:
                if cur.strip():                 # 连续逗号产生的空段自动丢掉
                    pool.add(cur.strip().lower())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            pool.add(cur.strip().lower())
    out = []
    for a in (active or []):
        s = str(a)
        if s.strip().lower() in pool:
            out.append(s)
    return out


def lora_triggers_get(q_id: str, force: bool = False) -> dict:
    full = _lora_file_by_id(q_id)
    if not full:
        return {"ok": False, "message": "找不到这个 LoRA 文件"}
    words, url, source, images = _lora_detect_info(full)
    saved = _read_dict_store(LORA_TRIGGERS_FILE).get(q_id)
    notes = [""] * len(words)
    if isinstance(saved, dict):
        sw = saved.get("words")
        if isinstance(sw, list):
            words = [str(w) for w in sw]        # 用户编辑过的以用户为准
            source = "saved"
        sn = saved.get("notes")
        if isinstance(sn, list):
            notes = [str(x) for x in sn]
        if saved.get("url"):
            url = str(saved["url"])
    while len(notes) < len(words):
        notes.append("")
    notes = notes[:len(words)]
    # 详情页打开 = 纯本地读取（零网络等待）：只读「获取」时已落盘的缓存数据
    ent = _read_dict_store(LORA_CIVITAI_CACHE).get(q_id)
    fetched = bool(isinstance(ent, dict) and ent.get("ok"))
    if fetched:
        if not words and isinstance(ent.get("words"), list) and ent["words"]:
            words = list(ent["words"])
            source = "civitai"
        if not url and ent.get("model_id"):
            # URL 服务时按用户选的域名现拼（缓存里只存 id，切换域名立即生效）
            url = "https://%s/models/%d?modelVersionId=%d" % (
                _civitai_domain(), ent["model_id"], ent.get("version_id") or 0)
        if ent.get("images") and not images:
            images = ent["images"]
    if not url:
        # 没有明确的模型 ID 时，退化为按文件名在 Civitai 搜索
        from urllib.parse import quote
        name = os.path.splitext(os.path.basename(full))[0]
        url = "https://%s/search/%s" % (_civitai_domain(), quote(name))
    # 「激活过的触发词」记忆（页面上点芯片 / 清空激活时写，读取时一起带回）
    active = []
    if isinstance(saved, dict) and isinstance(saved.get("active"), list):
        active = [str(x) for x in saved["active"]]
    return {"ok": True, "id": q_id, "words": words, "notes": notes, "url": url,
            "source": source, "images": images, "fetched": fetched,
            "active": _trig_prune_active(active, words)}


def lora_triggers_save(body: dict) -> dict:
    body = body or {}
    q_id = str(body.get("id") or "")
    full = _lora_file_by_id(q_id)
    if not full:
        return {"ok": False, "message": "找不到这个 LoRA 文件"}
    store = _read_dict_store(LORA_TRIGGERS_FILE)
    prev = store.get(q_id) if isinstance(store.get(q_id), dict) else {}
    raw = body.get("words")
    if not isinstance(raw, list):
        # 只更新「激活状态」（页面上点一下芯片就写一次）时不必带上整份词表
        if isinstance(body.get("active"), list):
            # ⚠ 没有记录时**绝不能**补 words/notes 键：GET 见到 saved 里的 words（哪怕空数组）
            #   就以它为准，这个 LoRA 的触发词会被整个盖没。已有记录时也只原样保留。
            #   active 这里不裁剪 —— GET 会拿到真实词表，按同一套拆词规则再裁（_trig_prune_active）。
            rec = dict(prev)
            if isinstance(prev.get("words"), list):
                rec["words"] = [str(x) for x in prev["words"]]
            if isinstance(prev.get("notes"), list):
                rec["notes"] = [str(x) for x in prev["notes"]]
            rec["active"] = [str(x).strip()[:80] for x in body["active"][:64] if str(x).strip()]
            rec["updated"] = _now_text()
            store[q_id] = rec
            _save_store(LORA_TRIGGERS_FILE, store)
            return {"ok": True, "id": q_id, "active": rec["active"]}
        return {"ok": False, "message": "words 必须是数组"}
    words, seen = [], set()
    for w in raw[:64]:
        w = str(w).strip()[:80]
        if w and w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    raw_notes = body.get("notes")
    notes = []
    if isinstance(raw_notes, list):
        for i in range(min(len(words), len(raw_notes))):
            notes.append(str(raw_notes[i]).strip()[:120])
    while len(notes) < len(words):
        notes.append("")
    if isinstance(body.get("active"), list):
        active = [str(x).strip()[:80] for x in body["active"][:64] if str(x).strip()]
    else:
        active = [str(x) for x in (prev.get("active") or [])]
    rec = {"words": words, "notes": notes, "updated": _now_text(),
           "active": _trig_prune_active(active, words)}    # 词被删了，激活状态跟着走
    if prev.get("url"):
        rec["url"] = str(prev["url"])
    store[q_id] = rec
    _save_store(LORA_TRIGGERS_FILE, store)
    sugg = _lora_tag_suggestions(full, words)
    return {"ok": True, "id": q_id, "words": words, "notes": notes, "suggestions": sugg}


# ---------- 生成历史（提示词 + 当时参数）----------

def list_prompt_history(limit=None) -> list:
    try:
        n = int(limit) if limit else PROMPT_HISTORY_RETURN
    except (TypeError, ValueError):
        n = PROMPT_HISTORY_RETURN
    return _load_store(PROMPT_HISTORY_FILE)[:max(1, min(n, PROMPT_HISTORY_MAX))]


def app_history(type_name=None) -> list:
    '''读应用自己的 history.json（main.py 写的，最多留 5000 条）。

    为什么要绕一道：后端 /api/history 会把 `images` 为空的记录整条滤掉
    （`if item.get("images") and len(item["images"]) > 0`），而视频是存在 `videos`
    字段里的 ⇒ 视频记录永远进不了「生成结果」栏。main.py 不能改，所以助手直接读文件。
    最新在前；只回界面用得上的字段（省流量）。
    '''
    base = globals().get("PROJECT_DIR") or os.path.dirname(os.path.abspath(sys.argv[0]))
    try:
        with open(os.path.join(base, "history.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if type_name and str(item.get("type") or "") != type_name:
            continue
        out.append({
            "prompt": str(item.get("prompt") or ""),
            "videos": item.get("videos") or [],
            "images": item.get("images") or [],
            "outputs": item.get("outputs") or [],
            "items": item.get("items") or [],
            "timestamp": item.get("timestamp"),
        })
    try:
        out.sort(key=lambda x: float(x.get("timestamp") or 0), reverse=True)
    except Exception:
        pass
    return out[:500]


def app_history_delete(timestamp) -> dict:
    '''从应用自己的 history.json 里删掉一条记录（**只删记录，绝不删文件**）。

    为什么不用后端的 /api/history/delete：它会把记录里 `images` 指向的文件**从硬盘删掉**
    （用户 2026-09-26 明确要求「只删记录，不删文件」）。main.py 不许改，所以这里自己改文件。
    ⚠ 是「读-改-写」整份文件；main.py 那边只有一个**进程内**的 HISTORY_LOCK，所以理论上
    会和「正好同时完成的生成」抢写（窗口极小）。做法上尽量读完了立刻写，别在循环里反复读写。
    '''
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return {"ok": False, "message": "缺少时间戳"}
    base = globals().get("PROJECT_DIR") or os.path.dirname(os.path.abspath(sys.argv[0]))
    path = os.path.join(base, "history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"ok": False, "message": "读不到历史文件：%s" % e}
    if not isinstance(data, list):
        return {"ok": False, "message": "历史文件格式不对"}
    left = [x for x in data
            if not (isinstance(x, dict) and isinstance(x.get("timestamp"), (int, float))
                    and abs(float(x["timestamp"]) - ts) < 0.001)]
    removed = len(data) - len(left)
    if removed:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(left, f, ensure_ascii=False, indent=4)
            os.replace(tmp, path)
        except Exception as e:
            return {"ok": False, "message": "写不进去：%s" % e}
    return {"ok": True, "removed": removed, "left": len(left)}


def prompt_history_action(body: dict) -> dict:
    """action: add | delete | clear"""
    body = body or {}
    action = str(body.get("action") or "").strip()
    items = _load_store(PROMPT_HISTORY_FILE)

    if action == "add":
        entry = body.get("entry")
        if not isinstance(entry, dict):
            return {"ok": False, "message": "没有要记的内容", "items": items}

        # 去重：只挡「同一次跑被记了两次」（提示词 + 结果地址都一样）。
        # 2026-09-26 修：原来只比提示词 —— 同一个提示词再跑一次就整条不记，
        # 而「生成结果」栏的媒体是从记录里取的 ⇒ 重复同一个提示词生成的结果永远不出现
        #（用户报「生成结果只能出现2个」，第三次生成的 00238 就是这么丢的）。
        # 归一化只做「去首尾空白 + 连续空白合并成一个空格」，不做大小写/标点之类的模糊匹配。
        prompt_key = " ".join(str(entry.get("prompt") or "").split())
        result_key = str(entry.get("result") or "").strip()
        if prompt_key and result_key:
            for it in items:
                if (" ".join(str(it.get("prompt") or "").split()) == prompt_key
                        and str(it.get("result") or "").strip() == result_key):
                    return {"ok": True, "skipped": True, "id": it.get("id"),
                            "items": list_prompt_history(),
                            "message": "这次跑已经记过了，跳过"}

        rec = {
            "id": _new_id("h"),
            "at": str(entry.get("at") or _now_text()),
            "prompt": str(entry.get("prompt") or ""),
            "workflow": str(entry.get("workflow") or ""),
            "ratio": str(entry.get("ratio") or ""),
            "megapixels": entry.get("megapixels"),
            "duration": entry.get("duration"),
            "seed": entry.get("seed"),
            "refs": int(entry.get("refs") or 0),
            "loras": [str(x) for x in (entry.get("loras") or [])][:20],
            "models": [str(x) for x in (entry.get("models") or [])][:20],
            "result": str(entry.get("result") or ""),
            "status": str(entry.get("status") or "ok"),
            # 本次生成实际花了多久（秒）。前端在提交时记开始时间，完成后算出来传进来。
            "seconds": entry.get("seconds"),
        }
        items.insert(0, rec)
        del items[PROMPT_HISTORY_MAX:]
        _save_store(PROMPT_HISTORY_FILE, items)
        return {"ok": True, "items": list_prompt_history(), "id": rec["id"]}

    if action == "delete":
        rid = str(body.get("id") or "").strip()
        left = [it for it in items if str(it.get("id")) != rid]
        _save_store(PROMPT_HISTORY_FILE, left)
        return {"ok": True, "items": list_prompt_history()}

    if action == "clear":
        _save_store(PROMPT_HISTORY_FILE, [])
        return {"ok": True, "items": [], "message": "提示词记录已清空"}

    return {"ok": False, "message": "不认识的 action: %s" % action,
            "items": list_prompt_history()}


# ---------- 模型替换：每个模型引用能换成什么 ----------

_models_map_cache = {"at": 0.0, "key": "", "root": "", "rel": {}, "base": {}}


def _models_map(force: bool = False) -> dict:
    """models 下模型文件的索引：小写相对路径 / 小写文件名 → 真实完整路径。

    和 _models_index 的分工：那个只留小写集合回答「有没有」，这个要多存一份映射，
    才能反查出模型在哪个子目录、好列同目录的其它模型。缓存 60 秒。
    """
    root = comfy_models_dir(comfy_install_dir() or "")
    if not root:
        return {"root": "", "rel": {}, "base": {}}
    now = time.time()
    if (not force and _models_map_cache["key"] == root
            and now - _models_map_cache["at"] < 60):
        return _models_map_cache
    rel, base = {}, {}
    try:
        for current, dirs, files in os.walk(root):
            if current[len(root):].count(os.sep) >= 5:
                dirs[:] = []
            for fn in files:
                if not fn.lower().endswith(MODEL_EXTS):
                    continue
                full = os.path.join(current, fn)
                rel[os.path.relpath(full, root).replace("\\", "/").lower()] = full
                base.setdefault(fn.lower(), full)
    except OSError as exc:
        log("扫模型目录失败: %s" % exc)
    _models_map_cache.update({"at": now, "key": root, "root": root,
                              "rel": rel, "base": base})
    return _models_map_cache


def _models_in_dir(root: str, folder: str, limit: int = 600) -> list:
    """列某个目录（含子目录，最多 3 层）下的模型文件，返回相对 models 的路径。"""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    try:
        for current, dirs, files in os.walk(folder):
            if current[len(folder):].count(os.sep) >= 3:
                dirs[:] = []
            for fn in files:
                if fn.lower().endswith(MODEL_EXTS):
                    out.append(os.path.relpath(os.path.join(current, fn), root)
                               .replace("\\", "/"))
                    if len(out) >= limit:
                        return sorted(out, key=lambda s: s.lower())
    except OSError:
        pass
    return sorted(out, key=lambda s: s.lower())


def _load_workflow_dict(name: str):
    if not name or ".." in name or os.path.isabs(name):
        return None
    path = os.path.join(PROJECT_DIR, "workflows", name.replace("/", os.sep))
    if not os.path.isfile(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None

def workflow_config_save(body: dict) -> dict:
    """把工作流的配置直接写进它旁边那份 .config.json。

    ⚠ 为什么不走后端的 PUT /api/workflows/{name}/config：那条路经过 pydantic 模型，
    会**把模型里没声明的键全丢掉**（2026-09-25 实测：bind_prompt、以及任何自定义键都被剥掉）。
    bind_prompt 是「这条参数接到视频页提示词框」的开关，丢了提示词框就失效 ——
    所以助手这里直接写文件，所有键原样保留。

    只允许写项目里已存在的工作流，名字按后端那套规则校验，不碰别的地方。
    """
    body = body or {}
    name = str(body.get("name") or "").strip()
    cfg = body.get("config")
    if not isinstance(cfg, dict) or not cfg:
        return {"ok": False, "message": "config 必须是一个对象"}
    if (not name or ".." in name or os.path.isabs(name)
            or not re.match(r"^(?:(?:custom|自定义)/)?[a-zA-Z0-9_一-龥\.\-]+\.json$", name)):
        return {"ok": False, "message": "工作流名不合法"}
    wf_path = os.path.join(PROJECT_DIR, "workflows", name.replace("/", os.sep))
    if not os.path.isfile(wf_path):
        return {"ok": False, "message": "这个工作流不存在"}
    clean = dict(cfg)
    if not isinstance(clean.get("title"), str):
        clean["title"] = name.replace(".json", "")
    fields = clean.get("fields")
    clean["fields"] = [f for f in (fields if isinstance(fields, list) else [])
                       if isinstance(f, dict) and f.get("id")]
    cfg_path = wf_path[:-len(".json")] + ".config.json"
    tmp = cfg_path + ".tmp"
    with _STORE_LOCK:
        for attempt in range(5):
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(clean, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, cfg_path)
                return {"ok": True, "name": name, "fields": len(clean["fields"])}
            except Exception as exc:
                if attempt == 4:
                    log("写工作流配置失败 %s: %s" % (name, exc))
                    return {"ok": False, "message": "写配置失败: %s" % exc}
                time.sleep(0.3)


def model_options(workflow) -> dict:
    """给工作流里每个模型引用，列出「同目录下还能换成哪些模型」。

    只影响提交时的参数，不改工作流文件 —— 界面侧把它写进 params 就行。
    """
    idx = _models_map()
    root = idx["root"]
    if not root:
        return {"ok": False, "message": "还没配 ComfyUI 安装目录，或里面没有 models 文件夹"}

    items, seen = [], set()
    for r in collect_model_refs(workflow):
        key = (r["node"], r["field"])
        if key in seen:
            continue
        seen.add(key)
        low = r["value"].replace("\\", "/").lower()
        full = idx["rel"].get(low) or idx["base"].get(os.path.basename(low))
        if full:
            # 当前模型在本地找得到 —— 就列它所在的那个目录
            folders = [os.path.dirname(full)]
        else:
            # 本地找不到（模型缺失 / 名字对不上）：把字段名对应的候选目录全列出来。
            # 不能只取第一个：比如 unet_name 的候选是 unet + diffusion_models，
            # 模型很可能在后一个里，只扫前一个会给出空列表。
            folders = [os.path.join(root, sub)
                       for sub in MODEL_FIELD_DIRS.get(r["field"].lower(), ())
                       if os.path.isdir(os.path.join(root, sub))]
        options, seen_opt = [], set()
        for folder in folders:
            for rel in _models_in_dir(root, folder):
                key2 = rel.lower()
                if key2 in seen_opt:
                    continue
                seen_opt.add(key2)
                options.append(rel)
        options.sort(key=lambda s: s.lower())
        items.append({
            "node": r["node"],
            "field": r["field"],
            "title": r["title"],
            "class_type": r["class_type"],
            "value": r["value"],
            "found": bool(full),
            "dir": os.path.relpath(folders[0], root).replace("\\", "/") if folders else "",
            "dirs": [os.path.relpath(f, root).replace("\\", "/") for f in folders],
            "options": options,
        })
    return {"ok": True, "models_root": root, "items": items}


# ---------- ComfyUI 实时进度 ----------
# 进度必须由助手服务在后台代取，因为有两个坑叠在一起：
#
# ① 网页连不上 ComfyUI 的 /ws。ComfyUI 有个 origin_only_middleware，要求请求的
#    Origin 和 Host 的 hostname 一致（用来防「别的网站偷偷往 127.0.0.1 排队」）。
#    浏览器的 Origin 是 127.0.0.1:3001、Host 是 127.0.0.1:8188，端口不同 → 403。
#    Python 的 websockets 客户端不发 Origin 头，所以能连。
# ② 执行/进度事件**不是广播**。ComfyUI 用 send_sync(..., server.client_id)，
#    只发给「发起那次任务的那一端」。而 main.py 提交时用的是它自己启动时随机
#    生成的 UUID（`CLIENT_ID = str(uuid.uuid4())`），页面根本拿不到。
#    好在队列项和历史条目的 `extra_data.client_id` 里能读到它，而且这个值对
#    后端进程来说是常量 —— 助手学到一次就能一直用。
#
# 做法：后台线程连 WS，同时定期从 /queue（优先）和 /history 里学 client_id，
# 学到新的就换过去重连；事件缓存进 _comfy_prog，网页轮询 /comfyui/progress。
_comfy_prog = {
    "lock": threading.Lock(),
    "at": 0.0,            # 最近一次进度事件的时间（给前端判断新旧用）
    "stage": "idle",      # idle | start | node | sampling | error | done
    "value": 0,
    "max": 0,
    "node": "",
    "prompt_id": "",
    "client_id": "",      # 当前这条 WS 用的 clientId（= 提交者的那个）
    "connected": False,
    "error": "",          # 连接层的问题（比如没装 websockets），页面可据此降级
    # 诊断用（2026-09-25 加）：消息总数 / 最后一条消息的类型和时间（任何类型都算，
    # 包括 status、crystools 那种监控心跳）—— 用来分辨"连接是活的但没进度"和"连接死了"
    "msgs": 0,
    "last_type": "",
    "last_msg_at": 0.0,
}
_comfy_prog_started = {"flag": False}


def _comfy_prog_set(**kw) -> None:
    with _comfy_prog["lock"]:
        _comfy_prog.update(kw)
        _comfy_prog["at"] = time.time()


def _comfy_prog_tick(t: str) -> None:
    """只记诊断信息（消息数 / 最后一条类型），**不动 at** —— at 是页面判断"进度的新旧"用的。"""
    with _comfy_prog["lock"]:
        _comfy_prog["msgs"] = int(_comfy_prog.get("msgs") or 0) + 1
        _comfy_prog["last_type"] = t
        _comfy_prog["last_msg_at"] = time.time()


def _comfy_learn_client_id() -> str:
    """从 ComfyUI 的队列 / 历史里读出「提交者」的 client_id。

    队列优先（那是正在跑的，最可能就是本应用刚提交的）；队列空了退到最近一条历史。
    读不到就返回空串。
    """
    import urllib.request

    addr = comfy_address()

    def get(path):
        with urllib.request.urlopen("http://%s%s" % (addr, path), timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    try:
        q = get("/queue")
        for key in ("queue_running", "queue_pending"):
            for item in (q.get(key) or []):
                if isinstance(item, list) and len(item) > 3 and isinstance(item[3], dict):
                    cid = str(item[3].get("client_id") or "").strip()
                    if cid:
                        return cid
    except Exception:
        pass
    try:
        h = get("/history?max_items=1")
        for item in h.values():
            p = item.get("prompt")
            if isinstance(p, list) and len(p) > 3 and isinstance(p[3], dict):
                cid = str(p[3].get("client_id") or "").strip()
                if cid:
                    return cid
    except Exception:
        pass
    return ""


def _comfy_prog_loop() -> None:
    """后台线程：连 ComfyUI 的 /ws 拿进度，缓存进 _comfy_prog；断了自动重连。"""
    try:
        import asyncio
        import websockets
    except Exception as exc:
        _comfy_prog_set(error="缺 websockets 库，进度同步不可用: %s" % exc)
        log("进度同步不可用: %s" % exc)
        return

    def handle(msg) -> None:
        t = str(msg.get("type") or "")
        if t not in ("execution_start", "executing", "progress", "progress_state",
                     "execution_error", "execution_complete", "execution_success"):
            return                      # status / 各种监控心跳一律不管
        d = msg.get("data") or {}
        if not isinstance(d, dict):
            d = {}
        pid = str(d.get("prompt_id") or "")
        if t == "execution_start":
            _comfy_prog_set(stage="start", value=0, max=0, prompt_id=pid)
        elif t == "executing":
            if d.get("node") is None:
                return                  # node 为空 = 这一轮执行结束
            _comfy_prog_set(stage="node", node=str(d.get("node")), prompt_id=pid)
        elif t == "progress":
            _comfy_prog_set(stage="sampling",
                            value=int(d.get("value") or 0),
                            max=int(d.get("max") or 0),
                            node=str(d.get("node") or ""), prompt_id=pid)
        elif t == "progress_state":
            # 新版把各节点进度打包发过来；取「完成比例最高」的那个当总体进度
            best = None
            for nid, st in (d.get("nodes") or {}).items():
                if not isinstance(st, dict):
                    continue
                mx = int(st.get("max") or 0)
                if mx <= 0:
                    continue
                v = int(st.get("value") or 0)
                if best is None or v / mx > best[1] / best[2]:
                    best = (str(nid), v, mx)
            if best:
                _comfy_prog_set(stage="sampling", value=best[1], max=best[2],
                                node=best[0], prompt_id=pid)
            else:
                _comfy_prog_set(stage="node", prompt_id=pid)
        elif t == "execution_error":
            _comfy_prog_set(stage="error", prompt_id=pid)
        else:
            _comfy_prog_set(stage="done", prompt_id=pid)

    async def session(addr: str, cid: str) -> None:
        url = "ws://%s/ws?clientId=%s" % (addr, cid)
        started = time.monotonic()
        last_learn = time.monotonic()
        async with websockets.connect(url, open_timeout=6, ping_interval=20,
                                      ping_timeout=20, close_timeout=3) as ws:
            _comfy_prog_set(connected=True, error="", client_id=cid)
            while True:
                # ① 每 ~4 秒重学一次提交者的 client_id。
                #    ⚠ 不能像以前那样"等约 6 秒没消息才学"：这台机器装了 crystools，
                #    它每隔半秒广播一次监控数据，安静期永远不出现 —— 于是助手一直用
                #    占位 id（aistudio-helper），ComfyUI 不把进度发给它，页面就一直没进度。
                if time.monotonic() - last_learn > 4:
                    last_learn = time.monotonic()
                    new = await asyncio.to_thread(_comfy_learn_client_id)
                    if new and new != cid:
                        return                          # 提交者换人了（后端重启）→ 换连接
                # ② 单条连接最多活 150 秒就换新的：half-open（对端还在但什么都不发）用
                #    ping 不一定及时，定期重连最省事；进度是缓存式的，中间断两三秒不影响
                if time.monotonic() - started > 150:
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    _comfy_prog_set(connected=False, error="WS 断开: %s" % exc)
                    return
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                try:
                    _comfy_prog_tick(str(msg.get("type") or ""))
                except Exception:
                    pass
                try:
                    handle(msg)
                except Exception:
                    continue

    async def forever() -> None:
        while True:
            cid = await asyncio.to_thread(_comfy_learn_client_id)
            if not cid:
                # 还不知道提交者是谁（后端还没生成过任何东西），先连个占位 id
                cid = "aistudio-helper"
            try:
                await session(comfy_address(), cid)
            except Exception as exc:
                _comfy_prog_set(connected=False, error=str(exc))
            await asyncio.sleep(2)      # 连不上就 2 秒后重试

    try:
        asyncio.run(forever())
    except Exception as exc:
        log("进度同步线程退出: %s" % exc)


def start_comfy_progress() -> None:
    if _comfy_prog_started["flag"]:
        return
    _comfy_prog_started["flag"] = True
    threading.Thread(target=_comfy_prog_loop, daemon=True).start()


def comfy_progress() -> dict:
    with _comfy_prog["lock"]:
        out = {k: v for k, v in _comfy_prog.items() if k != "lock"}
    out["at"] = round(out["at"], 3)
    out["address"] = comfy_address()
    out["age"] = round(time.time() - out["at"], 1) if out["at"] else None
    return out


# ---------------- Seedance 桥接：把上游的异步生图伪装成标准 OpenAI 同步生图 ----------------
# 为什么要有它：main.py 是红线（见 AI-交接说明 §2，不能改），而原版「文生图」页走通用
# OpenAI 通道，只会 POST {base_url}/v1/images/generations（同步、响应里带图）。而
# seedance.nz 的图片接口是 /v1/image/generations（**单数 image、异步**：提交拿 task_id
# 再轮询）。这里做一次形状翻译 —— 原版页面把平台地址填成本机助手即可直接用，后端一个字不动。
#
# 平台侧配置（例）：base_url = http://127.0.0.1:8317 ，协议 openai，图片模式用默认的
# 「OpenAI 标准」，Key 填 seedance 的 sk-xxx（桥接直接用请求头里那把 key 转发上游）。
# 助手自己的兜底配置放 state_dir()/seedance_bridge.json（可选）。

def seedance_bridge_config() -> dict:
    cfg = {
        "upstream": "https://api.seedance.nz",
        "env_key": "API_PROVIDER_CUSTOM_API_KEY",   # 请求头没带 key 时，回退读应用 API/.env 的这一项
        "env_file": "API/.env",
        "timeout": 600,           # 等上游出图的总时长（秒）
        "poll_interval": 4,       # 轮询间隔（秒），上游建议 3~5 秒
    }
    path = os.path.join(state_dir(), "seedance_bridge.json")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
    except Exception as exc:
        log("读取 seedance_bridge.json 失败：%s" % exc)
    return cfg


def seedance_bridge_api_key(cfg: dict, auth_header: str = "") -> str:
    """优先用请求头里那把 Key（= 平台里配的那把），没有才回退读应用 API/.env。"""
    auth = str(auth_header or "").strip()
    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    if auth:
        return auth
    env_key = str(cfg.get("env_key") or "").strip()
    env_file = str(cfg.get("env_file") or "API/.env")
    path = env_file if os.path.isabs(env_file) else os.path.join(project_dir(), env_file)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f.read().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == env_key:
                    return v.strip().strip('"').strip("'")
    except Exception as exc:
        log("读 %s 失败：%s" % (path, exc))
    return ""


def seedance_bridge_http(url: str, payload=None, method: str = "GET", key: str = "", timeout: float = 60) -> dict:
    import urllib.error
    import urllib.request
    data = None
    headers = {"Accept": "application/json", "User-Agent": "AIStudioHelper"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError("上游 HTTP %s：%s" % (exc.code, detail or exc.reason))
    except Exception as exc:
        raise RuntimeError("连不上上游：%s" % exc)
    try:
        obj = json.loads(raw)
    except Exception:
        raise RuntimeError("上游返回的不是 JSON：%s" % raw[:200])
    if not isinstance(obj, dict):
        raise RuntimeError("上游返回结构异常：%s" % raw[:200])
    return obj


def seedance_bridge_size_metadata(body: dict) -> dict:
    """把 OpenAI 的 size / resolution 翻成上游的 metadata（上游 resolution 优先于宽高）。"""
    metadata = {}
    resolution = str(body.get("resolution") or "").strip().lower()
    if resolution in ("1k", "1.5k", "2k"):
        metadata["resolution"] = resolution
        return metadata
    match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", str(body.get("size") or ""))
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if 240 <= width <= 8192 and 240 <= height <= 8192:
            metadata["width"] = width
            metadata["height"] = height
    return metadata


def seedance_bridge_generate(body: dict, auth_header: str = "") -> dict:
    """标准 OpenAI 生图请求 → 上游异步提交+轮询 → 回标准 OpenAI 形状。"""
    cfg = seedance_bridge_config()
    key = seedance_bridge_api_key(cfg, auth_header)
    if not key:
        raise RuntimeError("没拿到 seedance 的 API Key（请求头没有 Bearer，也没读到应用 API/.env 里那一项）")
    upstream = str(cfg.get("upstream") or "").rstrip("/")
    model = str(body.get("model") or "").strip()
    if not model:
        raise RuntimeError("请求里没有 model")
    payload = {"model": model, "prompt": str(body.get("prompt") or "")}
    metadata = seedance_bridge_size_metadata(body)
    if metadata:
        payload["metadata"] = metadata
    created = seedance_bridge_http(upstream + "/v1/image/generations", payload, "POST", key)
    task_id = str(created.get("task_id") or created.get("id") or "").strip()
    if not task_id:
        raise RuntimeError("上游没返回 task_id：%s" % json.dumps(created, ensure_ascii=False)[:200])
    timeout = float(cfg.get("timeout") or 600)
    interval = max(1.0, float(cfg.get("poll_interval") or 4))
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        time.sleep(interval)
        last = seedance_bridge_http(upstream + "/v1/image/generations/" + task_id, None, "GET", key)
        data = last.get("data") if isinstance(last.get("data"), dict) else last
        status = str((data or {}).get("status") or "").upper()
        if status in ("SUCCESS", "SUCCEEDED", "COMPLETED"):
            url = str((data or {}).get("result_url") or "").strip()
            if not url:
                nested = (data or {}).get("data")
                if isinstance(nested, dict):
                    content = nested.get("content")
                    if isinstance(content, dict):
                        url = str(content.get("image_url") or "").strip()
            if not url:
                raise RuntimeError("任务成功但没有图片地址：%s" % json.dumps(last, ensure_ascii=False)[:200])
            return {"created": int(time.time()), "data": [{"url": url}], "bridge": {"task_id": task_id}}
        if status in ("FAILURE", "FAILED", "ERROR", "CANCELED", "CANCELLED"):
            reason = (data or {}).get("fail_reason") or (data or {}).get("message") or status
            raise RuntimeError("上游任务失败：%s" % reason)
    raise RuntimeError("等上游出图超时（%s 秒），task_id=%s（可改 state_dir()/seedance_bridge.json 的 timeout）" % (int(timeout), task_id))


def seedance_bridge_models(auth_header: str = "") -> dict:
    """把 /v1/models 透传给上游，好让应用里的「测试连接」能通。"""
    cfg = seedance_bridge_config()
    key = seedance_bridge_api_key(cfg, auth_header)
    upstream = str(cfg.get("upstream") or "").rstrip("/")
    return seedance_bridge_http(upstream + "/v1/models", None, "GET", key, timeout=30)


# ---------- 软件更新检查（2026-09-28）----------
# 更新源在 AI-Studio-Data\update_source.json 的 version_url：GitHub 仓库 raw 的
# VERSION 文件地址（如 https://raw.githubusercontent.com/<用户>/<仓库>/main/VERSION）。
# 没配 = 只回当前版本；拉取失败/超时不算错误，把原因带回给设置页显示。
_UPDATE_CONFIG_FILE = "update_source.json"


def _read_project_version() -> str:
    try:
        with open(os.path.join(PROJECT_DIR, "VERSION"), "r", encoding="utf-8") as fh:
            return (fh.read().strip().splitlines() or [""])[0].strip()
    except Exception:
        return ""


def _ver_newer(a: str, b: str) -> bool:
    """a 是否比 b 新：按数字分段比较（2026.9.1 > 2026.08.28），短的补 0；
    任一侧没有数字就不判新（避免纯字符串比较误报）。"""
    def tup(s: str):
        return tuple(int(p) for p in re.split(r"[^0-9]+", (s or "").strip()) if p)
    ta, tb = tup(a), tup(b)
    if not ta or not tb:
        return False
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return ta > tb


def check_update() -> dict:
    cur = _read_project_version()
    url = ""
    try:
        with open(os.path.join(state_dir(), _UPDATE_CONFIG_FILE), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict):
            url = str(cfg.get("version_url") or "").strip()
    except Exception:
        pass
    out = {"ok": True, "configured": bool(url), "current": cur}
    if not url:
        return out
    latest = ""
    err = ""
    # ① 主源：GitHub releases/latest API —— 实时、无 CDN 缓存。
    #    （2026-09-29 教训：jsDelivr @main 有小时级缓存，发版后用户迟迟查不到更新。）
    #    从 version_url 解析出仓库才走这步；拿不到（没 release/网络断）就退回备源。
    _rid, _zips, repo_tuple = _update_repo_from_url(url)
    if repo_tuple:
        _owner, _repo, _branch = repo_tuple
        meta_t = os.path.join(state_dir(), "_rel_meta.json")
        try:
            _curl_fetch("https://api.github.com/repos/%s/%s/releases/latest" % (_owner, _repo),
                        meta_t, 10, retries=2)
            with open(meta_t, "r", encoding="utf-8") as fh:
                latest = str(json.load(fh).get("tag_name") or "").lstrip("vV").strip()
            os.remove(meta_t)
        except Exception as exc:
            err = "releases 拉取失败：%s" % exc
    # ② 备源：version_url 文本（jsDelivr/raw 的 VERSION；有 CDN 缓存延迟，只兜底）
    if not latest:
        try:
            if _CURL_EXE:
                # curl 优先：受限网络按 TLS 指纹拦 python-urllib（见 _CURL_EXE 处的实测注释）
                tmp = os.path.join(state_dir(), "_ver_fetch.txt")
                try:
                    _curl_fetch(url, tmp, 8, retries=2)
                    with open(tmp, "r", encoding="utf-8", errors="replace") as fh:
                        latest = (fh.read().strip().splitlines() or [""])[0].strip()
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            else:
                import urllib.request as _ur  # 懒加载：只有真配了更新源才会走到
                req = _ur.Request(url, headers={"User-Agent": "AI-Studio-Desktop"})
                with _ur.urlopen(req, timeout=8) as resp:
                    latest = (resp.read(4096).decode("utf-8", errors="replace")
                              .strip().splitlines() or [""])[0].strip()
        except Exception as exc:
            out["ok"] = False
            out["error"] = ((err + "；") if err else "") + "拉取失败：%s" % exc
            return out
    out["latest"] = latest
    out["has_update"] = bool(latest) and _ver_newer(latest, cur)
    return out


# ---------- 从自己的仓库应用更新（2026-09-28）----------
# 「一键更新」走这里：把 update_source.json 的 version_url 解析出仓库 → 下载 zip → 校验 →
# 先在 AI-Studio-Data\update_backups\ 留还原点 → 替换白名单文件（main.py / VERSION /
# AI-Studio-Desktop.pyw / AI-Studio-Backend.py / static/**）。
# ⚠ 绝不能让用户走 main.py 的 /api/update-from-github：那条路硬编码拉原作者仓库
#   （hero8152/Infinite-Canvas），会把定制页面全部冲回原版。
# ⚠ 用户数据一律不碰：AI-Studio-Data\、data\、workflows\custom\、python\、assets\ 都不在白名单里。
_UPDATE_ALLOWED_ROOT_FILES = {"main.py", "VERSION", "AI-Studio-Desktop.pyw", "AI-Studio-Backend.py"}
_UPDATE_ZIP_MAX = 400 * 1024 * 1024      # 更新包下载/单文件解压上限
_UPDATE_ENTRIES_MAX = 8000               # 解压条目上限（zip 炸弹保护）
_UPDATE_BACKUP_KEEP = 10                 # 还原点保留个数（与 main.py 的口径一致）
_CURL_EXE = shutil.which("curl")         # Win10+ 自带。2026-09-29 实测：受限网络按 TLS 指纹拦
                                         # python-urllib（10054），curl 同一时刻同一 URL 大概率能过
                                         # → 更新链路一律优先 curl，urllib 只当 curl 缺失时的后备


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# 本进程启动时刻（模块导入时记一次），用来判断"磁盘上的启动器文件比本进程新"
# ⇒ 说明更新已经落盘但助手还在跑旧代码，必须重启应用。
_PROC_START = time.time()


def helper_code_stale() -> bool:
    """磁盘上的启动器比本进程新？（2026-09-29 用户连更新 3 次都没重启，助手一直是旧代码，
    新加的功能全报"连不上本地助手服务"，用户以为功能没做出来。）
    页面启动时问一次 /health，stale=True 就直接提示"请完全关窗重开"。"""
    try:
        return os.path.getmtime(os.path.abspath(__file__)) > _PROC_START + 2
    except Exception:
        return False

# —— 下载线路（2026-09-29 用户：GitHub 直连慢，接入国内镜像 + 自动探测本机代理）——
# 镜像站套在原始 GitHub URL 前面即可加速 release/raw 下载；镜像站经常换，直连永远压轴兜底。
# 2026-09-29 第二批（用户：更新慢得离谱 + 要在 ComfyUI 设置里选线路）：实测 ghfast.top 只有
# 59KB/s 而 gh-proxy.com 4.6MB/s，原来"哪条通走哪条"⇒ 默认选中了最慢的 ⇒ 改成先测速择优，
# 并且把线路表暴露给设置页让用户自己钉一条（国内镜像标出来）。
_UPDATE_LINES = (
    {"id": "auto", "label": "自动测速择优", "tag": "推荐", "cn": False, "prefix": "",
     "host": "各线路先测 2.5 秒再下"},
    {"id": "ghfast", "label": "ghfast.top", "tag": "国内镜像", "cn": True,
     "prefix": "https://ghfast.top/", "host": "ghfast.top"},
    {"id": "ghproxy", "label": "gh-proxy.com", "tag": "国内镜像", "cn": True,
     "prefix": "https://gh-proxy.com/", "host": "gh-proxy.com"},
    {"id": "ghproxynet", "label": "ghproxy.net", "tag": "国内镜像", "cn": True,
     "prefix": "https://ghproxy.net/", "host": "ghproxy.net"},
    {"id": "moeyy", "label": "github.moeyy.xyz", "tag": "国内镜像", "cn": True,
     "prefix": "https://github.moeyy.xyz/", "host": "github.moeyy.xyz"},
    {"id": "direct", "label": "GitHub 直连", "tag": "不套镜像", "cn": False, "prefix": "",
     "host": "github.com"},
)
_GH_MIRRORS = tuple(ln["prefix"] for ln in _UPDATE_LINES if ln["prefix"])


def _line_by_id(line_id: str) -> dict:
    for ln in _UPDATE_LINES:
        if ln["id"] == line_id:
            return ln
    return _UPDATE_LINES[0]


def _read_update_config() -> dict:
    """读 AI-Studio-Data\\update_source.json（读不到就当空配置，别抛）。"""
    try:
        with open(os.path.join(state_dir(), _UPDATE_CONFIG_FILE), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _write_update_config(patch: dict) -> dict:
    """合并写回（保住 version_url 等其它键），先写临时文件再 replace。"""
    cfg = _read_update_config()
    cfg.update(patch)
    path = os.path.join(state_dir(), _UPDATE_CONFIG_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return cfg


def update_line_get() -> dict:
    line = str(_read_update_config().get("line") or "auto").strip() or "auto"
    if line not in [ln["id"] for ln in _UPDATE_LINES]:
        line = "auto"
    # 顺带把"自动检索到的本机代理"报给设置页显示（不在这里同步探，只读预热线程的结果，
    # 免得设置页打开时被 20 秒的探针卡住）。万一预热线程还没跑（或还没跑完），这里补一脚，
    # 前端会隔几秒再问一次。
    if not _proxy_cache.get("tested") and _CURL_EXE:
        threading.Thread(target=_detect_proxy, daemon=True).start()
    return {"ok": True, "line": line, "label": _line_by_id(line)["label"],
            "lines": [dict(ln) for ln in _UPDATE_LINES],
            "proxy": str(_proxy_cache.get("proxy") or ""),
            "proxy_source": str(_proxy_cache.get("source") or ""),
            "proxy_tested": bool(_proxy_cache.get("tested"))}


def update_line_set(line: str) -> dict:
    if str(line) not in [ln["id"] for ln in _UPDATE_LINES]:
        return {"ok": False, "error": "未知的下载线路：%s" % line}
    _write_update_config({"line": str(line)})
    return update_line_get()


# —— 代理探测（只信"官方来源"，不再瞎扫端口：2026-09-29 见 _detect_proxy 的注释）——
_PROXY_CANDIDATE_PORTS = (7890, 7891, 7897, 7899, 10808, 10809, 1080, 20171, 20172, 2080, 8888, 8080)
_PROXY_PROBE_BUDGET = 20                  # 代理探测总预算（秒）；超了就用手上已找到的
_proxy_cache = {"tested": False, "proxy": "", "source": ""}

def set_update_proxy(proxy: str) -> None:
    """update_source.json 里显式配了 proxy 就用它（覆盖自动探测）。"""
    if proxy:
        _proxy_cache.update(tested=True, proxy=proxy, source="update_source.json 配置")


def _env_proxy_candidates() -> list:
    """环境变量里配的代理（有些代理软件会顺手设 HTTP_PROXY/ALL_PROXY）。"""
    out = []
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        v = str(os.environ.get(key) or "").strip()
        if not v:
            continue
        if "://" not in v:
            v = "http://" + v
        if v not in out:
            out.append(v)
    return out


def _system_proxy_candidates() -> list:
    """Windows 系统代理设置里的地址（用户开了"系统代理"时这里最准）。
    形如 `http=127.0.0.1:6382;socks=127.0.0.1:7891` —— socks 那条要换成 socks5h:// 才探得通。"""
    out = []
    if os.name != "nt":
        return out
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            enabled, _ = winreg.QueryValueEx(k, "ProxyEnable")
            server, _ = winreg.QueryValueEx(k, "ProxyServer")
        if enabled and server:
            for part in str(server).split(";"):
                part = part.strip()
                if not part:
                    continue
                scheme = "http"
                if "=" in part:
                    name, _, val = part.partition("=")
                    part = val.strip()
                    if "socks" in name.lower():
                        scheme = "socks5h"
                if not part:
                    continue
                out.append(part if "://" in part else "%s://%s" % (scheme, part))
    except Exception:
        pass
    return out


def _probe_one_proxy(cand: str, seconds: float = 5.0) -> bool:
    """一个候选代理能不能用：seconds 秒内过 gstatic 的 204 探针才算。"""
    try:
        rc = subprocess.run(
            [_CURL_EXE, "-sS", "-x", cand, "-m", "%.1f" % seconds, "-o", os.devnull,
             "-w", "%{http_code}", "https://www.gstatic.com/generate_204"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=seconds + 8, creationflags=_NO_WINDOW)
        return rc.returncode == 0 and "204" in (rc.stdout or "")
    except Exception:
        return False


def _race_probe(cands: list, timeout: float = None, probe_seconds: float = 5.0) -> str:
    """并发探针，谁先过 204 就用谁。**所有探针都回报了就收工**（不硬等满预算）。
    用 daemon 线程而不是 ThreadPoolExecutor：后者的 atexit 会 join 线程，退出应用时被拖几秒。"""
    if not cands:
        return ""
    import queue as _q
    res_q = _q.Queue()

    def _one(c):
        try:
            res_q.put(c if _probe_one_proxy(c, probe_seconds) else None)
        except Exception:
            res_q.put(None)

    for c in cands:
        threading.Thread(target=_one, args=(c,), daemon=True).start()
    deadline = time.time() + (timeout or _PROXY_PROBE_BUDGET)
    got = 0
    while got < len(cands):
        left = deadline - time.time()
        if left <= 0:
            break
        try:
            r = res_q.get(timeout=min(0.5, left))
        except Exception:
            continue
        got += 1
        if r:
            return r
    return ""


def _detect_proxy() -> str:
    """**自动检索**用户正在用的代理（用户不用手配）：只信"官方来源" ——
      ① 环境变量（HTTP(S)_PROXY / ALL_PROXY）
      ② Windows 系统代理（注册表，跟 Edge 读同一处）
      ③ 常见代理端口（7890/7891/10809…）
    ① ② 先探（这就是浏览器会用的那个），探不通再看 ③；都不行就**不走代理**（跟浏览器一样）。
    ⚠ 2026-09-29 教训：原来还会"扫 netstat 里所有本机在监听的端口、谁先应答用谁"，
    结果在用户没开系统代理时挑中了 **WorkBuddy 沙箱自己的代理进程**（sandbox-cli.exe 的
    13891/1750），既不是用户的代理、又会随沙箱退出而失效 ⇒ 已撤掉这个瞎猜的兜底。
    进程内缓存；start_helper 起了预热线程，用户点检查更新时通常已经探完。"""
    if _proxy_cache["tested"]:
        return _proxy_cache["proxy"]
    found, src = "", ""
    if _CURL_EXE:
        # ① 环境变量 ② 系统代理（注册表）—— 这两个是"用户明确配过"的，优先（并发探）
        explicit = []
        for c in _env_proxy_candidates():
            explicit.append(("环境变量", c))
        for c in _system_proxy_candidates():
            explicit.append(("Windows 系统代理", c))
        seen, uniq = set(), []
        for tag, c in explicit:
            if c not in seen:
                seen.add(c)
                uniq.append((tag, c))
        if uniq:
            win = _race_probe([c for _, c in uniq])
            if win:
                found = win
                src = next(t for t, c in uniq if c == win)
        # ③ 常见代理端口（只在前面都没有时猜一次；猜测轮用 3 秒短超时，别把预算耗光）
        if not found:
            win = _race_probe(["http://127.0.0.1:%d" % p for p in _PROXY_CANDIDATE_PORTS],
                              probe_seconds=3.0)
            if win:
                found, src = win, "常见代理端口"
        # ④ HTTP 代理都没有 → 常见端口再按 SOCKS5 试一轮
        if not found:
            win = _race_probe(["socks5h://127.0.0.1:%d" % p for p in _PROXY_CANDIDATE_PORTS],
                              probe_seconds=3.0)
            if win:
                found, src = win, "常见代理端口(SOCKS5)"
    _proxy_cache.update(tested=True, proxy=found, source=src)
    if found:
        log("更新下载走本机代理 %s（来源：%s）" % (found, src))
    return found

def _fmt_speed(bps: float) -> str:
    """给进度条用的速度文字。"""
    try:
        bps = float(bps or 0)
    except Exception:
        bps = 0.0
    if bps >= 1048576:
        return "%.1f MB/s" % (bps / 1048576.0)
    if bps >= 1024:
        return "%.0f KB/s" % (bps / 1024.0)
    return "%.0f B/s" % max(0.0, bps)


# —— 代理只对"不套镜像"的线路生效（用户 2026-09-29 明确要求）——
# 国内镜像站本身就在国内，套一层代理只会更慢甚至不通；只有直连 github.com 时才需要代理
# （这也是浏览器的行为：系统代理是给"直连"用的）。
_DIRECT_GITHUB_HOSTS = ("github.com", "api.github.com", "codeload.github.com",
                        "uploads.github.com", "objects.githubusercontent.com",
                        "raw.githubusercontent.com", "gist.githubusercontent.com")


def _url_is_direct_github(url: str) -> bool:
    """这个下载/探测地址是不是"直连 GitHub"（= 该走代理的那类）。镜像地址返回 False。"""
    try:
        host = url.split("//", 1)[1].split("/", 1)[0].split(":")[0].strip().lower()
    except Exception:
        return False
    return host in _DIRECT_GITHUB_HOSTS


def _probe_line_speed(url: str, seconds: float = 2.5) -> float:
    """短测一条线路：下 seconds 秒，返回实测字节/秒（0 = 不通/拿不到）。
    ⚠ 代理只给"直连 GitHub"那条加；镜像线路**不套代理**（用户 2026-09-29 定：
    选了镜像就走镜像）。否则测速和下载都被代理污染，比出来的速度也不是真实可比。
    ⚠ curl 超时退出的 rc 是 28，但 -w 的 speed_download 照样会打出来 ⇒ 只看 stdout，别判 rc。"""
    if not _CURL_EXE:
        return 0.0
    cmd = [_CURL_EXE, "-sS", "-L", "-m", "%.1f" % seconds, "-o", os.devnull,
           "-A", "AI-Studio-Desktop", "-w", "%{speed_download}", url]
    if _url_is_direct_github(url):
        proxy = _detect_proxy()
        if proxy:
            cmd += ["-x", proxy]
    try:
        rc = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            timeout=seconds + 15, creationflags=_NO_WINDOW)
        out = (rc.stdout or "").strip()
        return float(out.splitlines()[-1]) if out else 0.0
    except Exception:
        return 0.0


def _order_lines(candidates: list, seconds: float = 2.5) -> list:
    """按**实测速度**从快到慢排线路（并发测，整体 ≈ seconds 秒）。
    2026-09-29 实测：ghfast.top 59KB/s、gh-proxy.com 4.6MB/s、ghproxy.net 11KB/s、
    github.moeyy.xyz 不通；而原来"哪条通走哪条"恰好选中列表第一个 ghfast.top
    ⇒ 36MB 要等 10 分钟（用户原话「更新速度慢得离谱」）。"""
    if not candidates:
        return []
    if len(candidates) == 1:
        return list(candidates)
    scored = {}
    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=min(6, len(candidates))) as ex:
            futs = {ex.submit(_probe_line_speed, cu, seconds): cu for cu in candidates}
            for fut, cu in futs.items():
                try:
                    scored[cu] = fut.result()
                except Exception:
                    scored[cu] = 0.0
    except Exception:
        return list(candidates)
    for cu in candidates:
        try:
            log("线路测速 %s：%s" % (cu.split("/")[2], _fmt_speed(scored.get(cu, 0.0))))
        except Exception:
            pass
    return sorted(candidates, key=lambda cu: -scored.get(cu, 0.0))


_apply_state = {"running": False, "stage": "", "msg": "", "pct": 0,
                "done": False, "ok": None, "result": None}

def _set_apply(stage: str, msg: str = "", pct: int = 0,
               running=None, done=None, ok=None, result=None) -> None:
    """刷新 /apply-update/progress 的状态快照（apply_update 的后台线程写，前端轮询读）。"""
    _apply_state["stage"] = stage
    _apply_state["msg"] = msg
    _apply_state["pct"] = int(pct)
    if running is not None: _apply_state["running"] = running
    if done is not None: _apply_state["done"] = done
    if ok is not None: _apply_state["ok"] = ok
    if result is not None: _apply_state["result"] = result

def _curl_fetch(url: str, target: str, max_seconds: int, retries: int = 1) -> None:
    """用系统 curl 下载到 target。失败抛异常（带 stderr 尾行）。
    ⚠ 必须带 CREATE_NO_WINDOW：curl 是控制台程序，从无窗口的 pythonw 启动会弹黑色 CMD 窗
    （2026-09-29 用户反馈「不要弹 cmd 窗，就后台下载」）。
    ⚠ 代理规则同下载：只有直连 GitHub 的地址才走代理（镜像/CDN 不套），
    直连且没探到代理 = 真直连（用户 2026-09-29 定）。"""
    if not _CURL_EXE:
        raise RuntimeError("系统里没有 curl.exe")
    cmd = [_CURL_EXE, "-sS", "-L", "--retry", str(retries), "--retry-delay", "2",
           "--retry-all-errors", "-m", str(max_seconds), "-A", "AI-Studio-Desktop",
           "--max-filesize", str(_UPDATE_ZIP_MAX), "-o", target, url]
    if _url_is_direct_github(url):
        proxy = _detect_proxy()
        if proxy:
            cmd += ["-x", proxy]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max_seconds + 30,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
        raise RuntimeError("curl rc=%s %s" % (proc.returncode, tail[-1] if tail else ""))

def _curl_download_progress(url: str, target: str, max_seconds: int, total: int, on_pct) -> None:
    """带进度回调的下载：Popen 启动 curl（无窗口），轮询落盘字节数算百分比 + **实时速度**。
    total 来自 Release 资产元数据（字节）；on_pct(0~100, 字节/秒)。超时/非零退出抛异常。
    ⚠ 代理只给"直连 GitHub"那条加；镜像线路不套代理（用户 2026-09-29 定：选了镜像就走镜像）。"""
    if not _CURL_EXE:
        raise RuntimeError("系统里没有 curl.exe")
    cmd = [_CURL_EXE, "-sS", "-L", "--retry", "2", "--retry-delay", "2",
           "--retry-all-errors", "-m", str(max_seconds), "-A", "AI-Studio-Desktop",
           "--max-filesize", str(_UPDATE_ZIP_MAX), "-o", target, url]
    if _url_is_direct_github(url):
        proxy = _detect_proxy()
        if proxy:
            cmd += ["-x", proxy]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)
    deadline = time.time() + max_seconds
    speed = 0.0                 # 平滑后的字节/秒（用户 2026-09-29 要能看到下载速度）
    last_b, last_t = 0, time.time()
    try:
        while proc.poll() is None:
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("下载超时（%d 秒）" % max_seconds)
            try:
                done = os.path.getsize(target)
            except OSError:
                done = 0
            now = time.time()
            if now - last_t >= 0.8:                      # 每 ~0.8s 采一次瞬时速度
                inst = (done - last_b) / (now - last_t)
                speed = inst if speed <= 0 else (speed * 0.55 + inst * 0.45)
                last_b, last_t = done, now
            on_pct(min(99, int(done * 100 / total)) if total > 0 else 0, speed)
            time.sleep(0.4)
        if proc.returncode != 0:
            tail = ""
            try:
                lines = [ln for ln in (proc.stderr.read() or b"").decode("utf-8", "replace").splitlines() if ln.strip()]
                tail = lines[-1] if lines else ""
            except Exception:
                pass
            raise RuntimeError("curl rc=%s %s" % (proc.returncode, tail))
        on_pct(100, speed)
    finally:
        try:
            proc.stderr.close()
        except Exception:
            pass


# —— 更新成功后自动重启应用（用户 2026-09-29：别让用户自己去关窗重开）——
# 为什么不能只杀启动器：助手是启动器里的线程，而窗口还开着时新启动器会判"应用已在运行"、
# 拉起旧窗口后自己退出 ⇒ 助手永远起不来（2026-09-29 真踩过：7 个启动器堆着、助手一直是旧的）。
# 所以必须"关窗口 + 收启动器 + 重新拉起"三件一起做，而且这个收尾脚本得**脱离式**跑
# （它的父进程马上要被它自己杀掉）。
_AUTO_RESTART_SRC = '''# -*- coding: utf-8 -*-
# 自动重启（更新成功后由启动器生成并调用；跑完自删）
import os, subprocess, sys, time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = 0x00000008
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    try:
        with open(os.path.join(HERE, "desktop.log"), "a", encoding="utf-8") as fh:
            fh.write("[%s] 自动重启: %s\\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def main():
    pid = int(sys.argv[1])
    project = sys.argv[2]
    time.sleep(3)                      # 留 3 秒让页面把"正在自动重启"显示出来
    log("开始（旧启动器 PID=%d）" % pid)
    # ① 关掉本应用的 Edge 窗口（命令行带 AI-Studio-Data 的，不碰用户自己的 Edge）
    ps = ("Get-CimInstance Win32_Process -Filter \\"Name='msedge.exe'\\" | "
          "Where-Object { $_.CommandLine -like '*AI-Studio-Data*' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=NO_WINDOW, timeout=40)
        log("已关闭应用窗口")
    except Exception as exc:
        log("关窗口失败: %s" % exc)
    # ② 收掉旧启动器（助手线程在里面；后端是它的子进程，留着让新启动器复用）
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       creationflags=NO_WINDOW, timeout=20)
        log("已收掉旧启动器")
    except Exception as exc:
        log("收启动器失败: %s" % exc)
    time.sleep(2.5)
    # ③ 重新拉起启动器
    py = os.path.join(project, "python", "pythonw.exe")
    if not os.path.exists(py):
        py = sys.executable
    try:
        subprocess.Popen([py, os.path.join(project, "AI-Studio-Desktop.pyw")],
                         cwd=project, creationflags=DETACHED, close_fds=True)
        log("已重新拉起启动器")
    except Exception as exc:
        log("重新拉起失败: %s" % exc)
    time.sleep(1.5)
    try:
        os.remove(os.path.abspath(__file__))       # 自删，别在数据目录里留垃圾
    except Exception:
        pass


main()
'''


def _spawn_auto_restart() -> bool:
    """更新成功后自动重启：把收尾脚本写到 AI-Studio-Data 下，**脱离式**跑它。
    返回 False 表示没起来（调用方要退回"请手动关窗重开"的提示）。"""
    try:
        project = PROJECT_DIR
        script = os.path.join(state_dir(), "_auto_restart.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_AUTO_RESTART_SRC)
        py = os.path.join(project, "python", "pythonw.exe")
        if not os.path.exists(py):
            py = sys.executable
        subprocess.Popen([py, script, str(os.getpid()), project],
                         cwd=project, creationflags=0x00000008, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("已安排更新后自动重启（收尾脚本 %s）" % script)
        return True
    except Exception as exc:
        log("安排自动重启失败：%s" % exc)
        return False


def _update_repo_from_url(url: str):
    """从 VERSION 地址解析出 (仓库标识, zip候选源列表, (owner, repo, branch))；解析不出返回 ('', [], None)。
    支持两种写法（2026-09-28 实测本机网络：raw.githubusercontent.com 被墙连不上，
    jsDelivr / codeload / api.github.com 都通）：raw 的 VERSION 地址、jsDelivr 的 VERSION 地址。
    zip 候选源两个（codeload 失败换 api zipball），适配不同用户的网络。"""
    u = str(url or "").strip()
    m = re.match(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/VERSION/?$", u)
    if not m:
        m = re.match(r"^https?://cdn\.jsdelivr\.net/gh/([^/@]+)/([^/@]+)@([^/@]+)/VERSION/?$", u)
    if not m:
        return "", [], None
    owner, repo, branch = m.group(1), m.group(2), m.group(3)
    return ("%s/%s@%s" % (owner, repo, branch),
            ["https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (owner, repo, branch),
             "https://api.github.com/repos/%s/%s/zipball/%s" % (owner, repo, branch)],
            (owner, repo, branch))


def _update_file_allowed(rel: str) -> bool:
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    return rel in _UPDATE_ALLOWED_ROOT_FILES or rel.startswith("static/")


def apply_update(force: bool = False) -> dict:
    """POST /apply-update：同步做「拿 release 元数据 + 版本守卫」（秒级），重活丢后台线程；
    进度走 GET /apply-update/progress（2026-09-29 用户要下载进度条 + 不要弹 CMD 窗）。"""
    if _apply_state.get("running"):
        return {"ok": False, "error": "更新正在进行中，请看进度条"}
    cur = _read_project_version()
    url = ""
    try:
        with open(os.path.join(state_dir(), _UPDATE_CONFIG_FILE), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict):
            url = str(cfg.get("version_url") or "").strip()
            set_update_proxy(str(cfg.get("proxy") or "").strip())
    except Exception:
        pass
    if not url:
        return {"ok": False, "error": "还没配更新源（AI-Studio-Data\\update_source.json 的 version_url）"}
    repo_id, zip_urls, repo_tuple = _update_repo_from_url(url)
    if not repo_id:
        return {"ok": False, "error": "version_url 解析不出 GitHub 仓库（支持 raw / jsDelivr 的 VERSION 地址）"}
    owner, repo, branch = repo_tuple
    staging = os.path.join(state_dir(), "update_staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    # 元数据同步拉（秒级）：latest + 资产 URL + 资产大小（给进度条算百分比）
    meta_t = os.path.join(staging, "_meta.json")
    try:
        _curl_fetch("https://api.github.com/repos/%s/%s/releases/latest" % (owner, repo),
                    meta_t, 20, retries=2)
        with open(meta_t, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        os.remove(meta_t)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": "拉取 release 信息失败：%s" % exc}
    tag = str(meta.get("tag_name") or "").strip()
    # 资产选择（2026-09-29 起 Release 只发完整包）：优先 Infinite-Canvas-Desktop-*-full.zip，
    # 兼容旧的 update.zip。补丁本就是"当版全量白名单快照"而非差异，拉最新完整包不会漏内容。
    asset_url = ""
    asset_total = 0
    for a in (meta.get("assets") or []):
        name = str(a.get("name") or "")
        if name.startswith("Infinite-Canvas-Desktop-") and name.endswith("-full.zip"):
            asset_url = str(a.get("browser_download_url") or "")
            asset_total = int(a.get("size") or 0)
            break
        if name == "update.zip" and not asset_url:
            asset_url = str(a.get("browser_download_url") or "")
            asset_total = int(a.get("size") or 0)
    latest = tag.lstrip("vV").strip()
    if not asset_url:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": "release 缺可更新的包资产（*-full.zip）"}
    if not force and not _ver_newer(latest, cur):
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "current": cur, "latest": latest,
                "error": "已是最新（%s），无需更新" % (cur or "?")}
    _set_apply("download", "正在下载更新包…", 0, running=True, done=False, ok=None, result=None)
    threading.Thread(target=apply_update_worker, daemon=True, kwargs=dict(
        repo_id=repo_id, owner=owner, repo=repo, zip_urls=zip_urls,
        asset_url=asset_url, asset_total=asset_total, cur=cur, force=force,
        latest_from_tag=latest)).start()
    return {"ok": True, "started": True, "latest": latest}


def apply_update_worker(repo_id: str, owner: str, repo: str, zip_urls: list,
                        asset_url: str, asset_total: int, cur: str, force: bool,
                        latest_from_tag: str) -> None:
    """apply_update 的重活（后台线程）：下载（带进度）→ 解压校验 → 还原点 → 替换。
    任何出口都必须 _set_apply(..., done=True, ok=..., result=...)，前端轮询靠它收尾。"""
    import zipfile as _zf
    staging = os.path.join(state_dir(), "update_staging")

    def _reset_staging():
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

    latest = latest_from_tag
    via = ""
    staged = False
    release_err = ""
    try:
        # —— 路线 A（首选）：Release 资产，带下载进度条（总大小来自资产元数据）——
        try:
            zip_t = os.path.join(staging, "update.zip")
            # 线路：用户在 ComfyUI 设置页钉的那条优先；auto（或没配）就**并发测速择优**。
            # 2026-09-29 实测 ghfast.top 59KB/s vs gh-proxy.com 4.6MB/s —— 原来"哪条通走哪条"
            # 恰好选中列表第一个最慢的 ⇒ 36MB 要等 10 分钟（用户报「更新速度慢得离谱」）。
            all_lines = [m + asset_url for m in _GH_MIRRORS] + [asset_url]
            line_id = str(_read_update_config().get("line") or "auto")
            if line_id != "auto":
                pinned = _line_by_id(line_id)
                first = (pinned["prefix"] + asset_url) if pinned["prefix"] else asset_url
                candidates = [first] + [c for c in all_lines if c != first]
                _set_apply("download", "按设置走「%s」，正在下载更新包…" % pinned["label"], 0)
            else:
                _set_apply("download", "正在测速挑选最快线路…", 0)
                candidates = _order_lines(all_lines)
            last = None
            for cu in candidates:
                host = cu.split("/")[2]
                try:
                    _curl_download_progress(cu, zip_t, 150, asset_total,
                        lambda p, sp=0, h=host: _set_apply(
                            "download",
                            "正在下载更新包… %d%% · %s（%s）" % (p, _fmt_speed(sp), h), p))
                    last = None
                    break
                except Exception as exc:
                    last = "线路 %s：%s" % (host, exc)
                    _set_apply("download", "线路 %s 失败，自动换下一条…" % host, 0)
                    _reset_staging()
            if last:
                raise RuntimeError(last)
            _set_apply("extract", "解压校验更新包…", 100)
            with _zf.ZipFile(zip_t) as zf:
                names = zf.namelist()
                if not names or len(names) > _UPDATE_ENTRIES_MAX:
                    raise RuntimeError("更新包含 %d 个条目，超出保护上限" % len(names))
                for info in zf.infolist():
                    rel = info.filename.replace("\\", "/")
                    if not rel or rel.endswith("/"):
                        continue
                    if rel.lstrip("/").startswith("/") or ".." in rel.split("/"):
                        raise RuntimeError("更新包含可疑路径，已中止")
                    if not _update_file_allowed(rel):
                        continue
                    target = os.path.join(staging, *rel.split("/"))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, 256 * 1024)
            os.remove(zip_t)
            via = "Release资产"
            staged = True
        except Exception as exc:
            release_err = str(exc)
            _reset_staging()
        # —— 路线 B（兜底）：仓库 zip（codeload → api zipball，每次 45 秒墙钟预算）。
        #    无资产元数据 ⇒ 没有进度数字，只有阶段文字；codeload 归档有分钟级缓存，只是兜底。
        if not staged:
            last_err = ""
            zip_ok = False
            for zip_url in zip_urls:
                try:
                    zip_target = os.path.join(staging, "repo.zip")
                    _set_apply("download", "正在下载更新包（备用源）…", 0)
                    if _CURL_EXE:
                        _curl_fetch(zip_url, zip_target, 45, retries=1)   # 45s：慢滴直接放弃换路线
                    else:
                        import urllib.request as _ur
                        t0 = time.time()
                        req = _ur.Request(zip_url, headers={"User-Agent": "AI-Studio-Desktop"})
                        with _ur.urlopen(req, timeout=30) as resp, open(zip_target, "wb") as out:
                            total = 0
                            while True:
                                if time.time() - t0 > 45:
                                    raise RuntimeError("下载超 45 秒未完成（zip 源被限速），换下一条路线")
                                chunk = resp.read(256 * 1024)
                                if not chunk:
                                    break
                                total += len(chunk)
                                if total > _UPDATE_ZIP_MAX:
                                    raise RuntimeError("更新包超过 %d MB，已中止" % (_UPDATE_ZIP_MAX // 1024 // 1024))
                                out.write(chunk)
                except Exception as exc:
                    last_err = "zip 下载失败（%s）：%s" % (zip_url.split("/")[2], exc)
                    _reset_staging()
                    continue
                try:
                    _set_apply("extract", "解压校验更新包…", 0)
                    with _zf.ZipFile(os.path.join(staging, "repo.zip")) as zf:
                        names = zf.namelist()
                        if not names or len(names) > _UPDATE_ENTRIES_MAX:
                            raise RuntimeError("更新包含 %d 个条目，超出保护上限" % len(names))
                        top = names[0].split("/")[0]
                        for info in zf.infolist():
                            rel = info.filename.replace("\\", "/")
                            if rel.startswith(top + "/"):
                                rel = rel[len(top) + 1:]
                            if not rel or rel.endswith("/"):
                                continue
                            if rel.lstrip("/").startswith("/") or ".." in rel.split("/"):
                                raise RuntimeError("更新包含可疑路径，已中止")
                            if not _update_file_allowed(rel):
                                continue
                            target = os.path.join(staging, *rel.split("/"))
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(info) as src, open(target, "wb") as dst:
                                shutil.copyfileobj(src, dst, 256 * 1024)
                    zip_ok = True
                    via = "zip"
                    staged = True
                    break
                except Exception as exc:
                    last_err = "解压失败：%s" % exc
                    _reset_staging()
            if not staged:
                shutil.rmtree(staging, ignore_errors=True)
                _set_apply("failed", "更新失败", 0, running=False, done=True, ok=False,
                           result={"ok": False, "error": "Release 路线失败（%s）；%s" % (release_err or "无 release", last_err or "未知")})
                return
        # 校验 staging：VERSION 必须在、static/ 必须非空
        _set_apply("verify", "校验更新包…", 0)
        try:
            with open(os.path.join(staging, "VERSION"), "r", encoding="utf-8") as fh:
                latest = (fh.read().strip().splitlines() or [""])[0].strip()
        except Exception:
            latest = ""
        staged_static = os.path.join(staging, "static")
        staged_static_count = sum(len(fs) for _, _, fs in os.walk(staged_static)) if os.path.isdir(staged_static) else 0
        if not latest or not staged_static_count:
            shutil.rmtree(staging, ignore_errors=True)
            _set_apply("failed", "更新包不完整", 0, running=False, done=True, ok=False,
                       result={"ok": False, "error": "更新包不完整（缺 VERSION 或 static/ 为空），已取消"})
            return
        if not force and not _ver_newer(latest, cur):
            shutil.rmtree(staging, ignore_errors=True)
            _set_apply("failed", "无需更新", 0, running=False, done=True, ok=False,
                       result={"ok": False, "current": cur, "latest": latest,
                               "error": "已是最新（%s），无需更新" % (cur or "?")})
            return
        # 还原点：当前将被替换的文件先备份
        _set_apply("backup", "备份当前版本（生成还原点）…", 0)
        backups_root = os.path.join(state_dir(), "update_backups")
        os.makedirs(backups_root, exist_ok=True)
        backup_dir = os.path.join(backups_root, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(backup_dir, exist_ok=True)
        for name in _UPDATE_ALLOWED_ROOT_FILES:
            p = os.path.join(PROJECT_DIR, name)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(backup_dir, name))
        if os.path.isdir(os.path.join(PROJECT_DIR, "static")):
            shutil.copytree(os.path.join(PROJECT_DIR, "static"), os.path.join(backup_dir, "static"))
        olds = sorted(d for d in os.listdir(backups_root) if os.path.isdir(os.path.join(backups_root, d)))
        while len(olds) > _UPDATE_BACKUP_KEEP:
            shutil.rmtree(os.path.join(backups_root, olds.pop(0)), ignore_errors=True)
        # 应用：先根文件（逐个原子替换），再 static/ 整目录替换（上游删掉的本地也删）
        _set_apply("apply", "替换程序文件…", 0)
        updated = []
        try:
            for name in sorted(_UPDATE_ALLOWED_ROOT_FILES):
                src = os.path.join(staging, name)
                if not os.path.isfile(src):
                    continue
                dst = os.path.join(PROJECT_DIR, name)
                tmp = dst + ".update_tmp"
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                updated.append(name)
            shutil.rmtree(os.path.join(PROJECT_DIR, "static"))
            shutil.copytree(staged_static, os.path.join(PROJECT_DIR, "static"))
            updated.append("static/（%d 个文件）" % staged_static_count)
        except Exception as exc:
            # 失败即回滚：根文件用还原点覆盖回去，static/ 整目录从还原点恢复
            try:
                for name in updated:
                    if name.startswith("static/"):
                        continue
                    b = os.path.join(backup_dir, name)
                    if os.path.isfile(b):
                        shutil.copy2(b, os.path.join(PROJECT_DIR, name))
                b_static = os.path.join(backup_dir, "static")
                if os.path.isdir(b_static):
                    shutil.rmtree(os.path.join(PROJECT_DIR, "static"), ignore_errors=True)
                    shutil.copytree(b_static, os.path.join(PROJECT_DIR, "static"))
            except Exception:
                pass
            shutil.rmtree(staging, ignore_errors=True)
            _set_apply("failed", "已回滚到还原点", 0, running=False, done=True, ok=False,
                       result={"ok": False, "error": "应用更新失败，已回滚到还原点：%s" % exc})
            return
        shutil.rmtree(staging, ignore_errors=True)
        # 更新成功后**自动重启应用**（用户 2026-09-29：别让用户自己去关窗重开）。
        # 收尾脚本会关窗口 → 收掉本启动器 → 重新拉起；助手线程随本进程一起结束，
        # 新启动器会起一个"新代码"的助手。脚本起不来才退回手动提示。
        auto = _spawn_auto_restart()
        msg = ("已更新到 %s，正在自动重启应用…（窗口会关掉再自己打开）" % latest) if auto else \
              ("已更新到 %s。自动重启没起来，请把应用窗口**完全关掉**（不是最小化），"
               "等约 10 秒再重新打开，新功能才生效。" % latest)
        result = {"ok": True, "repo": repo_id, "current": cur, "latest": latest, "via": via,
                  "updated": updated, "backup": os.path.basename(backup_dir),
                  "restart_required": True, "auto_restart": bool(auto), "message": msg}
        _set_apply("done", msg, 100, running=False, done=True, ok=True, result=result)
    except Exception as exc:      # 兜底：worker 里任何未捕获异常也要落进状态，前端才能收尾
        shutil.rmtree(staging, ignore_errors=True)
        _set_apply("failed", "更新失败", 0, running=False, done=True, ok=False,
                   result={"ok": False, "error": str(exc)})


class _HelperHandler(BaseHTTPRequestHandler):
    server_version = "AIStudioHelper"

    def log_message(self, *a):        # 别把访问日志打到控制台
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", headers=None):
        """headers: 额外响应头。封面图要长缓存 + 支持 Range；普通 JSON 仍然 no-store。

        2026-09-25：原来这里对**所有**响应写死 no-store，连封面图也不许缓存 ——
        库里 1389 张封面（含 291 个 1.5 MB 的视频）每次打开都重新下一遍，
        合计几百 MB，用户反馈"打开 LoRA 库很慢"就是这个原因。
        """
        extra = dict(headers or {})
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body_bytes(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    def _body_json(self) -> dict:
        raw = self._body_bytes()
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def do_OPTIONS(self):
        # 跨源写请求要先过预检，否则浏览器的 POST 根本发不出去
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parts = urlsplit(self.path)
        path = parts.path
        q = {k: (v[0] if v else "") for k, v in parse_qs(parts.query).items()}
        try:
            if path == "/settings":
                cfg = save_comfy_config(self._body_json())
                return self._json({"ok": True, "config": cfg, "report": comfy_report()})
            if path == "/update-line":          # 更新下载线路（ComfyUI 设置页里选）
                return self._json(update_line_set(str((self._body_json() or {}).get("line") or "")))
            if path == "/comfyui/start":
                return self._json(comfy_start(force=bool(q.get("force"))))
            if path == "/input-file":
                data = self._body_bytes()
                if not data:
                    return self._json({"ok": False, "message": "没收到文件内容"}, 400)
                res = write_comfy_input(q.get("name", ""), data)
                return self._json(res, 200 if res.get("ok") else 409)
            if path == "/asset-favorites/toggle":
                return self._json(asset_favorites_toggle(self._body_json()))
            if path == "/model-check":
                body = self._body_json()
                if isinstance(body.get("workflow"), dict):
                    res = check_workflow_models(name=body.get("name") or "", workflow=body["workflow"])
                else:
                    res = check_workflow_models(name=body.get("name") or q.get("name", ""))
                return self._json(res, 200 if res.get("ok") else 400)
            if path == "/templates":
                return self._json(templates_action(self._body_json()))
            if path == "/template-categories":
                return self._json(template_cats_action(self._body_json()))
            if path == "/prompt-history":
                return self._json(prompt_history_action(self._body_json()))
            if path == "/history-delete":
                return self._json(app_history_delete((self._body_json() or {}).get("timestamp")))
            if path == "/ui-state":
                return self._json(save_ui_state(self._body_json()))
            if path == "/lora-triggers":
                return self._json(lora_triggers_save(self._body_json()))
            if path == "/lora-fetch/start":
                body = self._body_json() or {}
                return self._json(lora_fetch_start(str(body.get("id") or "") or None))
            if path == "/lora-fetch/stop":
                return self._json(lora_fetch_stop())
            if path == "/workflow-config":
                return self._json(workflow_config_save(self._body_json()))
            if path == "/model-overrides":
                return self._json(model_overrides_save(self._body_json()))
            if path == "/model-options":
                body = self._body_json()
                wf = body.get("workflow")
                if not isinstance(wf, dict):
                    wf = _load_workflow_dict(body.get("name") or q.get("name", ""))
                if not isinstance(wf, dict):
                    return self._json({"ok": False, "message": "拿不到工作流结构"}, 400)
                return self._json(model_options(wf))
            # Seedance 桥接：原版「文生图」页把平台地址填成本机助手时，请求落在这里
            if path == "/v1/images/generations":
                return self._json(seedance_bridge_generate(self._body_json(), self.headers.get("Authorization") or ""))
            if path == "/v1/images/edits":
                return self._json({"error": {"message": "桥接暂不支持图生图：原版会用 multipart 传本地图，而 seedance 只收公网图片 URL。先用文生图，或给桥接加一步图床上传。"}}, 501)
            if path == "/apply-update":
                body = self._body_json() or {}
                return self._json(apply_update(force=bool(body.get("force"))))
            return self._json({"error": "not found"}, 404)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        q = {k: (v[0] if v else "") for k, v in parse_qs(parts.query).items()}
        try:
            if path == "/health":
                return self._json({"ok": True, "service": "AI Studio helper", "version": 1,
                                   "stale": helper_code_stale()})
            if path == "/check-update":
                return self._json(check_update())
            if path == "/apply-update/progress":
                return self._json(dict(_apply_state))
            if path == "/asset-favorites":
                return self._json(asset_favorites_get())
            # Seedance 桥接：透传上游 /v1/models，让应用里的「测试连接」按钮能通
            if path == "/v1/models":
                return self._json(seedance_bridge_models(self.headers.get("Authorization") or ""))
            if path == "/loras":
                return self._json(scan_loras(force=bool(q.get("refresh"))))
            if path == "/settings":
                return self._json({"ok": True, "config": load_comfy_config(),
                                   "report": comfy_report()})
            if path == "/update-line":          # 更新下载线路（ComfyUI 设置页里选）
                return self._json(update_line_get())
            if path == "/lora-search":
                return self._json(lora_search(q.get("q", ""), q.get("site", "civitai")))
            if path == "/comfyui/ping":
                return self._json(comfy_ping())
            if path == "/comfyui/status":
                return self._json({"ok": True, "status": comfy_report(),
                                   "launched": dict(_comfy_launch)})
            if path == "/comfyui/progress":
                return self._json({"ok": True, "progress": comfy_progress()})
            if path == "/pick-folder":
                return self._json(pick_folder(q.get("initial", "")))
            if path == "/model-check":
                res = check_workflow_models(name=q.get("name", ""))
                return self._json(res, 200 if res.get("ok") else 400)
            if path == "/model-check-all":
                res = check_all_workflows()
                return self._json(res, 200 if res.get("ok") else 400)
            if path == "/cover":
                return self._cover(q.get("id", ""))
            if path == "/workflows":
                return self._json({"workflows": list_workflow_files()})
            if path == "/workflow":
                return self._workflow(q.get("name", ""))
            if path == "/templates":
                return self._json({"ok": True, "items": list_templates()})
            if path == "/template-categories":
                return self._json(template_cats_get())
            if path == "/prompt-history":
                return self._json({"ok": True, "items": list_prompt_history(q.get("limit"))})
            if path == "/app-history":
                return self._json({"ok": True, "items": app_history(q.get("type"))})
            if path == "/ui-state":
                return self._json({"ok": True, "state": list_ui_state()})
            if path == "/model-options":
                wf = _load_workflow_dict(q.get("name", ""))
                if not isinstance(wf, dict):
                    return self._json({"ok": False,
                                       "message": "找不到工作流：%s" % q.get("name", "")}, 404)
                return self._json(model_options(wf))
            if path == "/lora-triggers":
                return self._json(lora_triggers_get(q.get("id", ""),
                                                    force=bool(q.get("refresh"))))
            if path == "/lora-fetch/status":
                return self._json(lora_fetch_status())
            if path == "/model-overrides":
                return self._json(model_overrides_get(q.get("name", "")))
            return self._json({"error": "not found"}, 404)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    def _cover(self, ident: str):
        # id 形如 "<rootIndex>:<相对路径>"，只允许在已配置的目录里取文件
        if ":" not in ident:
            return self._json({"error": "bad id"}, 400)
        try:
            ri, rel = ident.split(":", 1)
            folders = lora_folders()
            folder = folders[int(ri)]
        except Exception:
            return self._json({"error": "bad id"}, 400)
        full = os.path.abspath(os.path.join(folder, rel.replace("/", os.sep)))
        if not full.startswith(os.path.abspath(folder)) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        cover = _cover_for(full)
        if not cover:
            return self._json({"error": "no cover"}, 404)
        ext = os.path.splitext(cover)[1].lower()
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
                 ".mp4": "video/mp4", ".webm": "video/webm"}.get(ext, "application/octet-stream")
        size = os.path.getsize(cover)
        headers = {
            # 封面是本地文件、几乎不变：让浏览器缓存一天，别每次开库重下
            "Cache-Control": "public, max-age=86400",
            "Last-Modified": time.strftime("%a, %d %b %Y %H:%M:%S GMT",
                                           time.gmtime(os.path.getmtime(cover))),
            "Accept-Ranges": "bytes",
        }
        # 支持 Range：<video preload=metadata> 只需要头部那几 KB，不用整段 1.5 MB
        m = re.match(r"bytes=(\d*)-(\d*)$", str(self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            start = int(m.group(1) or 0)
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            if start >= size or start > end:
                return self._send(416, b"", "text/plain",
                                  dict(headers, **{"Content-Range": "bytes */%d" % size}))
            with open(cover, "rb") as fh:
                fh.seek(start)
                body = fh.read(end - start + 1)
            headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
            return self._send(206, body, ctype, headers)
        with open(cover, "rb") as fh:
            return self._send(200, fh.read(), ctype, headers)

    def _workflow(self, name: str):
        if not name or ".." in name or os.path.isabs(name):
            return self._json({"error": "bad name"}, 400)
        wf_dir = os.path.join(PROJECT_DIR, "workflows")
        full = os.path.abspath(os.path.join(wf_dir, name.replace("/", os.sep)))
        if not full.startswith(os.path.abspath(wf_dir)) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        try:
            wf = json.load(open(full, encoding="utf-8"))
        except Exception as exc:
            return self._json({"error": "解析失败: %s" % exc}, 500)
        cfg = {}
        cfg_path = full[:-5] + ".config.json"
        if os.path.isfile(cfg_path):
            try:
                cfg = json.load(open(cfg_path, encoding="utf-8")) or {}
            except Exception:
                cfg = {}
        return self._json({"name": name, "workflow": wf, "config": cfg})


def helper_alive() -> bool:
    """8317 上是不是已经有本应用的助手服务在跑了。"""
    import urllib.request

    try:
        with urllib.request.urlopen("http://%s:%d/health" % (HELPER_HOST, HELPER_PORT),
                                    timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return bool(data.get("ok")) and data.get("service") == "AI Studio helper"
    except Exception:
        return False


def start_helper() -> None:
    """起本地助手服务（后台线程，随应用退出而结束）。"""
    # 已经有一个在跑就别再起。ThreadingHTTPServer 带 SO_REUSEADDR，同一端口能绑两次，
    # 于是两个助手服务同时收请求 —— 这种重复很隐蔽（netstat 才会看到两行）
    if helper_alive():
        log("助手服务已在运行，跳过（复用已有那个）。")
        return
    start_comfy_progress()      # 后台连 ComfyUI 的 WS，缓存进度给页面轮询
    try:
        srv = ThreadingHTTPServer((HELPER_HOST, HELPER_PORT), _HelperHandler)
    except OSError as exc:
        log("助手服务没起来（端口 %d 可能被占用）: %s" % (HELPER_PORT, exc))
        return
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=_detect_proxy, daemon=True).start()   # 预热代理探测（更新下载用）
    log("助手服务已启动: http://%s:%d/  (LoRA 扫描 / 封面 / 工作流列表)"
        % (HELPER_HOST, HELPER_PORT))


def apply_ui_changes(verbose: bool = False) -> list:
    """把界面上的定制改动补齐。返回这次实际做了什么。

    每一步都幂等：找不到特征串就跳过，已经改过也不会重复改。
    """
    path = os.path.join(PROJECT_DIR, "static", "index.html")
    if not os.path.isfile(path):
        return []

    try:
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
    except Exception as exc:
        log("读取 index.html 失败: %s" % exc)
        return []

    changed = []

    # 0) 先保证新增页面的文件在（应用更新会整个替换 static/）
    if _ensure_new_page(verbose):
        log("已恢复新增页面: static/%s" % NEW_PAGE)

    # 0b) 备用端口用的后端包装脚本（根目录文件，自更新按清单覆盖时可能被冲掉）
    if ensure_backend_script(verbose):
        log("已恢复 %s" % BACKEND_SCRIPT)

    # 1) PAGE_IDS 白名单：移除要隐藏的页，补上新增的页
    m = re.search(r"(PAGE_IDS\s*=\s*\[)([^\]]*)(\])", html)
    if m:
        ids = [t.strip().strip("'\"") for t in m.group(2).split(",") if t.strip()]
        keep = [t for t in ids if t not in FEATURE_HIDES]
        if NEW_PAGE_ID not in keep:
            at = keep.index("angle") + 1 if "angle" in keep else len(keep)
            keep.insert(at, NEW_PAGE_ID)
        if keep != ids:
            html = (html[:m.start()]
                    + m.group(1) + ",".join("'%s'" % t for t in keep) + m.group(3)
                    + html[m.end():])
            gone = [t for t in ids if t not in FEATURE_HIDES]
            added = [t for t in keep if t not in ids]
            if gone:
                changed.append("PAGE_IDS 移除 " + ",".join(gone))
            if added:
                changed.append("PAGE_IDS 新增 " + ",".join(added))

    # 2) 隐藏对应的侧栏项
    for page in FEATURE_HIDES:
        pat = re.compile(r'<div class="nav-item"([^>]*onclick="switchUI\(this,\s*\'%s\'\)")'
                         % re.escape(page))
        mm = pat.search(html)
        if mm and "display:none" not in mm.group(1):
            html = (html[:mm.start()]
                    + '<div class="nav-item" style="display:none"' + mm.group(1)
                    + html[mm.end():])
            changed.append("隐藏侧栏项 " + page)

    # 3) 删掉左下角作者组件（作者名 + 社交图标 + D X 字母）
    if AUTHOR_BOX_TAG in html:
        html = _strip_div(html, AUTHOR_BOX_TAG)
        changed.append("删除左下角作者组件")

    # 4) 展开「更多设置」：去掉折叠类、去掉高度上限
    if SETTINGS_GROUP_FIND in html:
        html = html.replace(SETTINGS_GROUP_FIND, SETTINGS_GROUP_OPEN, 1)
        changed.append("展开更多设置分组")
    # 4b) 隐掉折叠按钮
    if SETTINGS_TOGGLE_FIND in html and SETTINGS_TOGGLE_HIDE not in html:
        html = html.replace(SETTINGS_TOGGLE_FIND, SETTINGS_TOGGLE_HIDE, 1)
        changed.append("隐藏更多设置折叠按钮")
    # 4c) 启动时不再按 localStorage 折叠（否则下次启动又收起来）
    if SETTINGS_RESTORE_FIND in html:
        html = html.replace(SETTINGS_RESTORE_FIND, SETTINGS_RESTORE_SET, 1)
        changed.append("停止恢复折叠状态")

    # 5) 删掉项目主页 / 版本角标 / 中英文切换
    if PROJECT_BTN_TAG in html:
        html = _strip_button(html, PROJECT_BTN_TAG)
        changed.append("删除项目主页")
    if VERSION_BADGE_TAG in html:
        span = _div_span(html, VERSION_BADGE_TAG)
        if span:
            html = html[:span[0]] + html[span[1]:]
            changed.append("删除版本角标")
    if LANG_BTN_TAG in html:
        html = _strip_button(html, LANG_BTN_TAG)
        changed.append("删除中英文切换")

    # 6) 把「黑夜模式」挪到侧栏底部（2026-09-28 起尾部允许紧跟「检查更新」pill）
    if THEME_BTN_TAG in html:
        act = _div_span(html, SIDE_ACTIONS_MARKER)
        if act:
            close_at = act[1] - len("</div>")
            i = html.find(THEME_BTN_TAG)
            j = html.find("</button>", i)
            if i != -1 and j != -1 and j < close_at:
                tail = html[j + len("</button>"):close_at].strip()
                # 2026-09-28：「检查更新」pill 允许紧跟在黑夜模式按钮下面（用户当天要求挪过来）；
                # 尾部只有它就视为已就位 —— 否则每次启动/apply-hides 都会把黑夜模式挪到它下面
                already = (tail == ""
                           or ('id="pillUpdate"' in tail and 'checkAppUpdate' in tail))
                if not already:
                    block = html[i:j + len("</button>")].strip()
                    rest = html[:i] + html[j + len("</button>"):]
                    act2 = _div_span(rest, SIDE_ACTIONS_MARKER)
                    if act2:
                        c2 = act2[1] - len("</div>")
                        html = (rest[:c2].rstrip()
                                + "\n                " + block
                                + "\n            " + rest[c2:])
                        changed.append("黑夜模式移到底部")

    # 7) 插入「视频生成」导航项（作为「本地功能」分组的最后一个，即「角度控制」之后）
    if ("switchUI(this, '%s')" % NEW_PAGE_ID) not in html:
        html, ok = _insert_before_div_close(html, NAV_GROUP_MARKER, NAV_ITEM_HTML)
        if ok:
            changed.append("新增导航项 视频生成")

    # 8) 插入对应的 iframe（紧跟「角度控制」那个）
    if ('id="frame-%s"' % NEW_PAGE_ID) not in html:
        mm = re.search(r'<iframe id="%s"[^>]*></iframe>' % re.escape(IFRAME_AFTER_ID), html)
        if mm:
            tag = ('<iframe id="frame-%s" data-src="/static/%s" scrolling="yes"></iframe>'
                   % (NEW_PAGE_ID, NEW_PAGE))
            html = html[:mm.end()] + "\n            " + tag + html[mm.end():]
            changed.append("新增 iframe frame-%s" % NEW_PAGE_ID)

    # 9) LOCAL_PAGE_IDS：把新页也算作「本地功能」分组里的页面
    m = re.search(r"(LOCAL_PAGE_IDS\s*=\s*\[)([^\]]*)(\])", html)
    if m:
        ids = [t.strip().strip("'\"") for t in m.group(2).split(",") if t.strip()]
        if NEW_PAGE_ID not in ids:
            ids.append(NEW_PAGE_ID)
            html = (html[:m.start()] + m.group(1)
                    + ",".join("'%s'" % t for t in ids) + m.group(3) + html[m.end():])
            changed.append("LOCAL_PAGE_IDS 新增 " + NEW_PAGE_ID)

    # 10) 新增「ComfyUI 设置」导航项：放在侧栏底部「API 设置」上面
    if ("switchUI(this, '%s')" % SETTINGS_PAGE_ID) not in html:
        i = html.find(SETTINGS_NAV_ANCHOR)
        if i != -1:
            line_start = html.rfind("\n", 0, i) + 1
            indent = html[line_start:i]
            block = "".join(indent + ln + "\n" for ln in SETTINGS_NAV_HTML.split("\n"))
            html = html[:line_start] + block + html[line_start:]
            changed.append("新增导航项 ComfyUI 设置")

    # 11) 它的 iframe（跟在 API 设置那个后面）
    if ('id="frame-%s"' % SETTINGS_PAGE_ID) not in html:
        mm = re.search(r'<iframe id="%s"[^>]*></iframe>' % re.escape(SETTINGS_IFRAME_AFTER), html)
        if mm:
            tag = ('<iframe id="frame-%s" data-src="/static/%s" scrolling="yes"></iframe>'
                   % (SETTINGS_PAGE_ID, SETTINGS_PAGE))
            html = html[:mm.end()] + "\n            " + tag + html[mm.end():]
            changed.append("新增 iframe frame-%s" % SETTINGS_PAGE_ID)

    # 12) 页面白名单
    m = re.search(r"(PAGE_IDS\s*=\s*\[)([^\]]*)(\])", html)
    if m:
        ids = [t.strip().strip("'\"") for t in m.group(2).split(",") if t.strip()]
        if SETTINGS_PAGE_ID not in ids:
            at = ids.index("api-settings") if "api-settings" in ids else len(ids)
            ids.insert(at, SETTINGS_PAGE_ID)
            html = (html[:m.start()] + m.group(1)
                    + ",".join("'%s'" % t for t in ids) + m.group(3) + html[m.end():])
            changed.append("PAGE_IDS 新增 " + SETTINGS_PAGE_ID)

    # 13.5) 挂工作流「默认配置」组件：它自己会再塞进「工作流设置」那个 iframe
    #       （那页的状态变量是 let，父页面跨 iframe 读不到，必须跑在 iframe 自己的作用域里）
    if WORKFLOW_AUTOFIELDS_JS not in html:
        i = html.rfind("</body>")
        if i != -1:
            html = (html[:i] + '<script src="/static/%s"></script>\n' % WORKFLOW_AUTOFIELDS_JS
                    + html[i:])
            changed.append("注入 %s" % WORKFLOW_AUTOFIELDS_JS)

    # 13) 清理上一版方案：往「工作流设置」页注入了界面的那个脚本，连同文件一起撤掉
    if PATH_UI_FILE in html:
        html = re.sub(r'[ \t]*<script src="/static/%s[^"]*"[^>]*></script>\r?\n?'
                      % re.escape(PATH_UI_FILE), "", html)
        if PATH_UI_FILE not in html:
            changed.append("移除旧的注入脚本")
    for old in (os.path.join(PROJECT_DIR, "static", PATH_UI_FILE),
                os.path.join(state_dir(), PATH_UI_FILE)):
        try:
            if os.path.isfile(old):
                os.remove(old)
                changed.append("删除 %s" % os.path.basename(old))
        except OSError as exc:
            log("删除 %s 失败: %s" % (old, exc))

    if changed:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
        except Exception as exc:
            log("写入 index.html 失败: %s" % exc)
            return []
        log("界面改动已应用 -> %s" % "; ".join(changed))
    elif verbose:
        print("无需改动：界面已经是定制后的状态。")
    return changed


def python_exe() -> str:
    """优先用项目自带的嵌入式 Python，保证依赖齐全、开箱即用。"""
    bundled = os.path.join(PROJECT_DIR, "python", "python.exe")
    if os.path.isfile(bundled):
        return bundled
    return sys.executable


def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_our_app(host: str, port: int) -> bool:
    """确认端口上跑的确实是本项目，而不是碰巧占了 3000 的别的程序。"""
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen("http://%s:%d/api/app-info" % (host, port), timeout=4) as resp:
            if resp.status != 200:
                return False
            _json.loads(resp.read().decode("utf-8", "replace"))
            return True
    except Exception:
        return False


# ---------- 窗口检测 ----------
# 不能用窗口标题判断：这个应用在运行时会把 document.title 改成「文生图」
# 「角度控制」「无限画布」等（static 里有 17 处 document.title 赋值）。
# 改用：枚举顶层可见窗口，看是否有窗口属于「命令行带桌面版专用 profile 的 msedge 进程」。
#
# 判定「应用已退出」需要同时满足三件事，且要连续确认多次：
#   1. WMI 查询成功（查询失败只当"未知"，绝不能当成"窗口没了"）
#   2. 没有任何带可见窗口的 Edge 进程
#   3. 连 --app= 的浏览器主进程都不在了
# 之所以这么保守：重复点图标时 Edge 会把请求交接给已有实例并重建窗口，
# 中间有几秒钟真的没有窗口。只按 2 次探测就判定关闭会把还在用的应用误杀。

# 本次实际用的后端端口。默认就是 3000；只有发现 3000 被别的程序占了才回退到备用端口。
# 端口会变，所以窗口 URL 一律走 app_url()，不要再引用写死的常量。
_active_port = PORT


def set_active_port(port: int) -> None:
    """记下本次用的端口，并落到用户目录 —— 「窗口还开着」时靠它查回来。"""
    global _active_port
    _active_port = int(port)
    try:
        with open(os.path.join(state_dir(), LAST_PORT_FILE), "w", encoding="utf-8") as fh:
            fh.write(str(_active_port))
    except OSError as exc:
        log("写 %s 失败: %s" % (LAST_PORT_FILE, exc))


def remembered_port() -> int:
    """上次实际用的端口。窗口还开着时，后端必须还在这个端口上它才连得上。"""
    try:
        with open(os.path.join(state_dir(), LAST_PORT_FILE), encoding="utf-8") as fh:
            text = fh.read().strip()
        if text.isdigit() and 1 <= int(text) <= 65535:
            return int(text)
    except OSError:
        pass
    return PORT


def app_url() -> str:
    return "http://%s:%d/" % (HOST, _active_port)


# 判断「是不是本应用的 Edge 主进程」只比前缀：端口可能是回退端口，不能写死
APP_URL_PREFIX = "--app=http://%s:" % HOST


def profile_procs():
    """返回 (ok, [(pid, cmdline)])。ok=False 表示这次查询失败，结果不可信。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             "Where-Object { $_.CommandLine -like '*%s*' } | "
             "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }" % PROFILE_MARKER],
            capture_output=True, text=True, errors="replace", timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return False, []
    if r.returncode != 0:
        return False, []
    rows = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid, cmd = line.split("|", 1)
        if pid.strip().isdigit():
            rows.append((int(pid), cmd))
    return True, rows


def has_visible_window(pids: set) -> bool:
    """这些进程里有没有带可见顶层窗口的。"""
    if not pids:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hit = []

        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.GetWindowTextLengthW(hwnd) > 0:
                hit.append(hwnd)
                return False
            return True

        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_cb)
        user32.EnumWindows(proc, 0)
        return bool(hit)
    except Exception:
        return False


def app_window_present() -> bool:
    ok, rows = profile_procs()
    return ok and has_visible_window({pid for pid, _ in rows})


def wait_window_maximized(timeout: int) -> bool:
    """等窗口出现，出现的第一时间就最大化。

    「打开就全屏」的关键：不能等窗口稳定后再统一最大化（POLL=2.5 秒的轮询
    会让用户先看到小窗、过几秒才变大），这里把轮询缩到 0.2 秒，
    窗口冒头的瞬间就 SW_MAXIMIZE。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, rows = profile_procs()
        if ok and rows:
            pids = {pid for pid, _ in rows}
            hwnds = []

            def _cb(hwnd, _lparam):
                if user32.IsWindowVisible(hwnd):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value in pids and user32.GetWindowTextLengthW(hwnd) > 0:
                        hwnds.append(hwnd)
                        return False
                return True

            proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_cb)
            user32.EnumWindows(proc, 0)
            if hwnds:
                for h in hwnds:
                    user32.ShowWindow(h, 3)         # SW_MAXIMIZE
                return True
        time.sleep(0.2)
    return False


def app_fully_closed():
    """返回 True=确实全关了 / False=还在 / None=这次查不出来（未知）。"""
    ok, rows = profile_procs()
    if not ok:
        return None
    pids = {pid for pid, _ in rows}
    if has_visible_window(pids):
        return False
    if any(APP_URL_PREFIX in cmd for _, cmd in rows):
        return False          # 浏览器主进程还在，不算关闭
    return True


# ---------- 启动流程 ----------

def find_running_instance() -> int:
    """本应用的后端是不是已经跑在某个候选端口上了？返回那个端口，没有就返回 0。

    这一步必须有：上次启动可能走了备用端口（3001/3002…），这次再启动时 3000
    仍然被别的程序占着；如果只找「第一个空闲端口」，就会在 3002 上又起一套后端 ——
    变成同一个应用两个后端、两个窗口，很难查。
    """
    for port in (PORT,) + tuple(FALLBACK_PORTS):
        if port_open(HOST, port) and is_our_app(HOST, port):
            return port
    return 0


def _pick_fallback_port() -> int:
    """找一个空闲的备用端口；都不可用就返回 0。"""
    for port in FALLBACK_PORTS:
        if not port_open(HOST, port):
            return port
    return 0


def start_server(allow_fallback: bool = True) -> "subprocess.Popen | None":
    """起后端。端口规则：

      · 当前端口上已经有本应用在跑 → 直接复用，不再起（返回 None）。
      · 当前端口空着 → 就用它（等于 3000 时直接跑 main.py，和以前完全一样）。
      · 当前端口被别的程序占着 → 挑一个备用端口，用 AI-Studio-Backend.py 把同一个
        app 跑起来（main.py 的端口是写死的、不能改，所以只能走包装脚本）。

    allow_fallback=False 时不换端口、直接报错退出。用在「窗口还开着」的场景：
    窗口连的是固定端口，后端就算换了端口它也看不见，换了只会白起一个孤儿进程。
    """
    port = _active_port

    # 本应用已经跑在某个候选端口上了？直接复用，别再起一套
    live = find_running_instance()
    if live:
        if live != port:
            set_active_port(live)
        log("本应用已在端口 %d 上运行，直接复用。" % live)
        return None

    if port_open(HOST, port):
        if not allow_fallback:
            fatal(
                "应用窗口还开着，但它连接的后端端口 %d 被其他程序占用了，\n"
                "没法治好它。\n\n"
                "请先关掉应用窗口，再重新双击图标打开（会自动改用备用端口）。" % port
            )
            sys.exit(1)

        alt = _pick_fallback_port()
        if not alt:
            fatal(
                "端口 %d 被其他程序占用了，备用端口 %s 也全都不可用。\n\n"
                "请先腾出一个端口，再重新打开本应用。"
                % (PORT, "/".join(str(p) for p in FALLBACK_PORTS))
            )
            sys.exit(1)
        log("端口 %d 被其他程序占用，改用备用端口 %d。" % (PORT, alt))
        set_active_port(alt)
        port = alt

    py = python_exe()
    logfile = open(os.path.join(state_dir(), "server.log"), "a", encoding="utf-8", buffering=1)
    logfile.write("\n=== %s 启动服务（端口 %d）===\n"
                  % (time.strftime("%Y-%m-%d %H:%M:%S"), port))

    if port == PORT:
        log("启动服务: %s main.py" % py)
        args = [py, "main.py"]
    else:
        ensure_backend_script()
        if not os.path.isfile(os.path.join(PROJECT_DIR, BACKEND_SCRIPT)):
            fatal("找不到 %s，没法在备用端口 %d 上启动后端。" % (BACKEND_SCRIPT, port))
            sys.exit(1)
        log("启动服务: %s %s %d" % (py, BACKEND_SCRIPT, port))
        args = [py, BACKEND_SCRIPT, str(port)]

    return subprocess.Popen(
        args,
        cwd=PROJECT_DIR,
        stdout=logfile,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )


def wait_ready(proc) -> bool:
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if port_open(HOST, _active_port):
            log("服务已就绪: %s" % app_url())
            return True
        if proc is not None and proc.poll() is not None:
            log("服务进程提前退出，返回码 %s，请看 %s\\server.log" % (proc.returncode, state_dir()))
            return False
        time.sleep(0.5)
    log("等待超时（%ds），服务未就绪。" % STARTUP_TIMEOUT)
    return False


def find_browser() -> str:
    """找一个 Chromium 内核，用于 --app 无边框窗口模式。"""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    fatal("没找到 Microsoft Edge 或 Google Chrome，无法打开应用窗口。\n\n请先安装其中之一。")
    sys.exit(1)


def profile_dir() -> str:
    """给桌面版一个独立浏览器配置，避免和日常浏览器的标签页/设置互相干扰。"""
    path = os.path.join(state_dir(), "profile")
    os.makedirs(path, exist_ok=True)
    return path


def ensure_mini_menu_blocked() -> None:
    """把应用专用 Edge 配置文件里的「选择文字迷你菜单」(inline_cue_menu) 屏蔽掉。

    迷你菜单（选中文字后浮出的 复制/搜索 小条）是 Edge 自带 UI，页面层管不住，
    只能在配置文件里关。CONTENT_SETTING: 1=允许 2=屏蔽。
    必须趁 Edge 没跑时写，否则会被 Edge 退出时的回写覆盖。
    profile 是全新的（Preferences 还不存在）也要写：2026-09-25 起 profile 落在项目内的
    AI-Studio-Data\profile，首次启动就是空目录 —— 这里不写就等于没屏蔽。
    """
    pref = os.path.join(profile_dir(), "Default", "Preferences")
    try:
        os.makedirs(os.path.dirname(pref), exist_ok=True)
        d = {}
        if os.path.isfile(pref):
            try:
                d = json.load(open(pref, encoding="utf-8"))
            except Exception:
                d = {}          # 文件坏了就当空的，至少把开关写进去
        if not isinstance(d, dict):
            d = {}
        prof = d.setdefault("profile", {})
        cs = prof.setdefault("content_settings", {})
        cs.setdefault("defaults", {})["inline_cue_menu"] = 2
        dv = prof.setdefault("default_content_setting_values", {})
        dv["inline_cue_menu"] = 2
        ex = cs.setdefault("exceptions", {}).setdefault("inline_cue_menu", {})
        ex["http://127.0.0.1,*"] = {"setting": 2}      # 覆盖 3000-3004 所有端口
        ex["http://localhost,*"] = {"setting": 2}
        # 真正管这个菜单的偏好键（从 Edge 二进制里挖出来的）：选文字不再弹
        eqs = d.setdefault("edge_quick_search", {})
        eqs["show_mini_menu"] = False
        with open(pref, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        log("屏蔽迷你菜单失败: %s" % exc)


def clear_stale_edge() -> None:
    """清掉占着专用 profile 但没有窗口、也没有 --app 主进程的 Edge 残留。

    这类残留会让新启动的 msedge.exe 把请求交接给旧实例后立刻退出，
    表现为「点了图标没反应」，所以启动前先清干净。
    """
    ok, rows = profile_procs()
    if not ok or not rows:
        return
    pids = {pid for pid, _ in rows}
    if has_visible_window(pids):
        return                      # 有窗口在用，别动
    if any(APP_URL_PREFIX in cmd for _, cmd in rows):
        return                      # 主进程还在，交给它自己收尾
    log("清理残留的 Edge 进程: %s" % sorted(pids))
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       creationflags=CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def default_window_size() -> str:
    """按屏幕可用区域取窗口尺寸，而不是写死一个值。"""
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        # SPI_GETWORKAREA = 0x0030，避开任务栏
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            # 直接给满工作区大小：窗口一出来就是全屏尺寸，紧接着的
            # SW_MAXIMIZE 只负责收边框，肉眼看不到「先小后大」的过程
            return "%d,%d" % (max(900, w), max(640, h))
    except Exception:
        pass
    return WINDOW_SIZE


def open_window() -> None:
    browser = find_browser()
    args = [
        browser,
        "--app=" + app_url(),
        "--start-maximized",        # app 窗口多数版本不理这个，下面还有 ShowWindow 兜底
        "--window-size=" + default_window_size(),
        "--user-data-dir=" + profile_dir(),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,msEdgeSidebarV2,msEdgeMiniMenu",
        "--disable-extensions",
    ]
    log("打开应用窗口: %s" % os.path.basename(browser))
    subprocess.Popen(args, creationflags=DETACHED_PROCESS)


def wait_window_appear(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if app_window_present():
            return True
        time.sleep(POLL)
    return False


def wait_window_gone() -> None:
    """等应用真正全关。

    app_fully_closed() 返回 None 表示这次查询没成功 —— 只当"未知"跳过，
    绝不当成"窗口没了"，否则一次 WMI 抖动就会把还在用的应用误杀。
    """
    confirms = 0
    while True:
        time.sleep(POLL)
        state = app_fully_closed()
        if state is None:
            continue
        if state:
            confirms += 1
            if confirms >= CONFIRM_GONE:
                log("已确认应用关闭（连续 %d 次）。" % confirms)
                return
        else:
            confirms = 0


def main() -> int:
    log("项目目录: %s" % PROJECT_DIR)

    apply_ui_changes()
    ensure_backend_script()
    start_helper()

    already_open = app_window_present()
    if already_open:
        # 窗口连的是上次那个端口，后端必须还在同一个端口上它才看得见
        set_active_port(remembered_port())

    # 窗口还开着时不换端口：换了它也看不见，只会白起一个孤儿后端
    proc = start_server(allow_fallback=not already_open)
    if not wait_ready(proc):
        if proc is not None:
            terminate(proc)
        return 1

    # ComfyUI 按配置在后台拉起（不阻塞开窗口；它加载模型慢，早点开始）
    if load_comfy_config()["auto_start"]:
        threading.Thread(target=comfy_autostart, daemon=True).start()

    if already_open:
        # 应用已在运行：只是把已有窗口拉到前台，不接管它的生命周期
        log("应用已在运行，打开（激活）已有窗口后退出。")
        open_window()
        return 0

    # 清掉会让新实例「交接后立刻退出」的残留 Edge 进程
    clear_stale_edge()
    ensure_mini_menu_blocked()      # 趁 Edge 没跑，把迷你菜单写死为屏蔽

    open_window()
    if not wait_window_maximized(WINDOW_TIMEOUT):   # 窗口冒头即最大化，打开就是全屏
        fatal(
            "窗口没能打开（等了 %d 秒）。\n\n"
            "请查看日志：%s\\desktop.log\n"
            "后端日志：%s\\server.log" % (WINDOW_TIMEOUT, state_dir(), state_dir())
        )
        if proc is not None:
            terminate(proc)
        return 1

    log("窗口已打开。")
    wait_window_gone()
    log("窗口已关闭，正在停止服务…")
    if proc is not None:
        terminate(proc)
    log("已退出。")
    return 0


def terminate(proc) -> None:
    """结束服务进程及其子进程。"""
    if proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    # 单独跑这一个动作，不开窗口：用于应用自更新后手工补一次界面改动
    if "--apply-hides" in sys.argv:
        print("项目目录:", PROJECT_DIR)
        print("要隐藏的功能页:", ", ".join(FEATURE_HIDES) or "(无)")
        result = apply_ui_changes(verbose=True)
        print("本次改动:", result or "无")
        sys.exit(0)
    # 只起助手服务，方便单独调试 LoRA 扫描
    if "--helper" in sys.argv:
        print("项目目录:", PROJECT_DIR)
        start_helper()
        print("助手服务: http://%s:%d/   （Ctrl+C 结束）" % (HELPER_HOST, HELPER_PORT))
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        sys.exit(0)
    sys.exit(main())
