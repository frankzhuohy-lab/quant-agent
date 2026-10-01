#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS 通知中心即时提醒（launchd 自驱, 不依赖任何会话/AI）
用法: python3 notify.py "标题" "内容"
可被 maket.py run_monitor 在每条信号后自动调用。
"""
import sys, subprocess


def notify(title, body, sound="Glass"):
    body = body.replace('"', "'")
    title = title.replace('"', "'")
    script = ('display notification "%s" with title "%s" sound name "%s"'
              % (body, title, sound))
    try:
        subprocess.run(["osascript", "-e", script], timeout=10,
                       capture_output=True)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "做T信号"
    body = sys.argv[2] if len(sys.argv) > 2 else ""
    ok = notify(title, body)
    print("notified" if ok else "notify failed")


def speak(text):
    """语音播报关键信号（TSP模式借鉴）; 失败静默"""
    text = text.replace('"', "'").replace("🔔 ", "").replace("📤 ", "")
    try:
        subprocess.run(["say", "-v", "Tingting", text], timeout=15,
                       capture_output=True)
        return True
    except Exception:
        return False
