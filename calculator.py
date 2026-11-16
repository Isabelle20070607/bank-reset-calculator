"""Run the existing optimizer as a hidden background calculation."""
import json
import shutil
import subprocess
from app_paths import FROZEN, ROOT


class Calculator:
    def __init__(self):
        self.node = str(ROOT/'node'/'node.exe') if FROZEN else shutil.which('node')
        if not self.node:
            raise FileNotFoundError('找不到 Node.js 计算运行时。')
        self.runner = ROOT/'run-calculation.cjs'

    def optimize(self, model, days=28, strength=1):
        result = subprocess.run([self.node, str(self.runner)],
            input=json.dumps({'model': model, 'options': {'days': days, 'strength': strength}}),
            capture_output=True, text=True, encoding='utf-8', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or '计算进程异常退出。')
        return json.loads(result.stdout)
