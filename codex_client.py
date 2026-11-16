"""Read-only JSON-RPC client for the user's local Codex installation."""
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path


def find_codex():
    override = os.environ.get('BANK_RESET_CODEX_BIN')
    if override:
        return Path(override)
    executable = shutil.which('codex.exe')
    if executable:
        return Path(executable)
    wrapper = shutil.which('codex')
    if wrapper:
        package = Path(wrapper).parent / 'node_modules' / '@openai' / 'codex'
        for candidate in package.rglob('codex.exe'):
            return candidate
    raise FileNotFoundError('找不到 Codex CLI；请安装并登录 Codex，或设置 BANK_RESET_CODEX_BIN。')


class CodexClient:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.process = None
        self.messages = queue.Queue()
        self.serial = 0
        self.lock = threading.RLock()
        self.stderr = None

    def _start(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.stderr = (self.runtime / 'codex-rpc.log').open('ab')
        self.process = subprocess.Popen(
            [str(find_codex()), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self.messages = queue.Queue()
        process, inbox = self.process, self.messages

        def read():
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if 'id' in message:
                    inbox.put(message)
            inbox.put({'error': {'message': 'Codex app-server 已退出。'}})

        threading.Thread(target=read, daemon=True).start()
        self._request('initialize', {'clientInfo': {'name': 'bank_reset_calculator',
            'title': 'Bank Reset Calculator', 'version': '0.2.0'}})
        self.process.stdin.write(json.dumps({'method': 'initialized'})+'\n')
        self.process.stdin.flush()

    def _request(self, method, params=None):
        if method not in ('initialize', 'account/read', 'account/rateLimits/read'):
            raise ValueError('This integration only supports reading account usage.')
        self.serial += 1
        request_id = self.serial
        self.process.stdin.write(json.dumps({'id': request_id, 'method': method,
            'params': params or {}})+'\n')
        self.process.stdin.flush()
        while True:
            reply = self.messages.get(timeout=25)
            if reply.get('id') not in (None, request_id):
                continue
            if 'error' in reply:
                raise RuntimeError(reply['error'].get('message', 'Codex RPC error'))
            return reply['result']

    def read_limits(self):
        with self.lock:
            try:
                if self.process is None or self.process.poll() is not None:
                    self._start()
                return self._request('account/rateLimits/read')
            except Exception:
                self.close()
                raise

    def close(self):
        with self.lock:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.stdin.close()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.terminate()
                        self.process.wait(timeout=3)
                self.process = None
            if self.stderr is not None:
                self.stderr.close()
                self.stderr = None


if __name__ == '__main__':
    client = CodexClient(Path(__file__).parent/'runtime')
    try:
        result = client.read_limits()
        bucket = result.get('rateLimitsByLimitId', {}).get('codex') or result.get('rateLimits')
        resets = result.get('rateLimitResetCredits')
        print(json.dumps({'rateLimits': bucket, 'resets': resets}, ensure_ascii=False, indent=2))
    finally:
        client.close()
