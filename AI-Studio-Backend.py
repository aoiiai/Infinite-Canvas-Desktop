# -*- coding: utf-8 -*-
"""AI-Studio-Backend — 在指定端口上跑本项目后端的包装脚本。

为什么需要它
------------
`main.py` 里 `uvicorn.run(..., port=3000)` 的端口是**写死**的，而 `main.py` 不能改：
应用自更新会按清单把它覆盖回去，用户也明确要求「后端用他原来的」。

但本机 3000 端口有可能被别的程序占着（例如开发机上的工具链会在 127.0.0.1:3000
起一个本地代理）。桌面版遇到这种情况时不能就罢工，需要把后端挪到备用端口上跑。

这个脚本 `import main` 拿到它的 `app` 对象，用**和 main.py 第 19032-19038 行完全
一致**的 uvicorn 参数在指定端口上跑起来，`main.py` 一行都不用动。

用法
----
    python AI-Studio-Backend.py <端口>

由 `AI-Studio-Desktop.pyw` 在「3000 被别的程序占用」时自动调用；正常情况下
（3000 空闲）桌面版仍然直接跑 `main.py`，不会用到这个文件。
"""

import os
import sys

# 保证 import main 能成功：脚本可能被从别的 cwd 调起
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def serve(port):
    import uvicorn

    # main.py 的 uvicorn.run 在 `if __name__ == "__main__":` 守卫里，
    # 所以导入它只会执行模块级代码、不会自己把服务起起来。
    import main as backend

    # 参数与 main.py 末尾保持一致：关闭服务端协议级 WebSocket ping，
    # 否则 PS UXP 面板那类不会自动回 pong 的客户端会每隔一会儿被踢掉、频繁断连。
    uvicorn.run(backend.app, host="0.0.0.0", port=port,
                ws_ping_interval=None, ws_ping_timeout=None)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip().isdigit():
        sys.stderr.write("用法: python AI-Studio-Backend.py <端口>\n")
        sys.exit(2)
    sys.exit(serve(int(sys.argv[1])))
