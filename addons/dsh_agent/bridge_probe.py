"""Optional human-operated hello client; never creates or sends a task."""
from __future__ import annotations

import argparse
import getpass
import http.client
import json
import re
import sys
import uuid

PROTOCOL = 'alice-dsh-plugin-bridge-v1'
MAX_RESPONSE = 16 * 1024


def probe(port: int, token: str) -> dict:
    if type(port) is not int or port != 18765:
        raise ValueError('invalid_port')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('invalid_probe_token')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', '/alice-bridge/v1/hello', headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/json',
            'Connection': 'close',
        })
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE:
            raise ValueError('handshake_refused_or_oversize')
        if response.getheader('Content-Type', '').split(';', 1)[0].lower() != 'application/json':
            raise ValueError('unexpected_response_type')
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get('protocol') != PROTOCOL
                or data.get('status') not in {'ready', 'execution_disabled'}
                or type(data.get('execution_enabled')) is not bool
                or data.get('boundary_enforced') is not False):
            raise ValueError('unexpected_bridge_contract')
        instance = str(uuid.UUID(data['instance_id']))
        capabilities = data.get('capabilities')
        if (not isinstance(capabilities, dict) or len(capabilities) > 32
                or any(not isinstance(k, str) or not re.fullmatch(r'[a-z_]{1,64}', k)
                       or type(v) is not bool for k, v in capabilities.items())):
            raise ValueError('invalid_capability_shape')
        # Only these public fields are returned; no raw body or auth is logged.
        return {'protocol': PROTOCOL, 'instance_id': instance,
                'status': data['status'], 'capabilities': capabilities,
                'execution_enabled': data['execution_enabled'], 'boundary_enforced': False}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error('仅供真实终端人工握手；不从命令行参数或管道读取令牌。')
    print('仅连接已由你加载的 DSH 插件，不安装、不启动、不派工。')
    token = getpass.getpass('输入本次插件专用配对令牌（不会显示）：')
    try:
        result = probe(args.port, token)
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        print('未取得符合约定的插件握手；未打印原始错误或响应，也不自动重试。')
        return 1
    finally:
        token = ''
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('仅确认插件握手响应；Alice 派工、权限隔离和业务执行仍未验收。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
