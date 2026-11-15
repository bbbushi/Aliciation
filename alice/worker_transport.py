"""Fixed loopback plugin transport. Credentials exist only in process memory."""
from __future__ import annotations

import json
import http.client
import os
import re
import subprocess

from .channels import ChannelError

PROTOCOL = 'alice-dsh-plugin-bridge-v1'


class BridgeError(ChannelError):
    def __init__(self, code='bridge_unavailable', *, uncertain=True):
        super().__init__(503 if uncertain else 409, code,
                         'DSH 连接或请求结果暂不可确认；未自动重发。' if uncertain else
                         'DSH 拒绝本次操作，请检查连接、权限及任务状态。')
        self.uncertain = uncertain


class DshBridge:
    """No proxy, redirects, arbitrary URL, personal credentials or auto retry."""
    def __init__(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
            raise ChannelError(400, 'invalid_token', '请输入有效的独立桥接令牌')
        self._token = token
        self.instance_id = None
        self.hello = None

    def _request(self, path, payload=None, *, timeout=5):
        connection = http.client.HTTPConnection('127.0.0.1', 18765, timeout=timeout)
        try:
            headers = {'Authorization': 'Bearer ' + self._token, 'Connection': 'close'}
            body = None
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
                if len(body) > 32768:
                    raise BridgeError('request_too_large', uncertain=False)
                headers['Content-Type'] = 'application/json'
            connection.request('GET' if payload is None else 'POST', '/alice-bridge/v1/' + path,
                               body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(1048577)
            if len(raw) > 1048576 or response.getheader('Content-Type', '').split(';')[0] != 'application/json':
                raise BridgeError('invalid_bridge_response')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise BridgeError('invalid_bridge_response')
            if response.status != 200:
                # Only protocol error codes, never remote exception/body text.
                code = value.get('error')
                allowed = {'instance_changed', 'task_not_found', 'task_busy', 'execution_disabled',
                           'request_conflict', 'permission_unavailable', 'authentication_required',
                           'slot_busy', 'invalid_request', 'capacity_exceeded', 'bridge_busy', 'task_unknown',
                           'task_expired', 'events_expired', 'cursor_ahead', 'task_closed', 'default_model_unavailable',
                           'agent_preset_unavailable',
                           'permission_changed', 'workspace_changed', 'session_capacity', 'task_capacity',
                           'task_identity_capacity', 'receipt_capacity', 'native_permission_consent_required',
                           'permission_mode_unsupported', 'dsh_delete_unsupported',
                           'maintenance_unavailable', 'session_identity_mismatch',
                           'session_not_found', 'session_busy', 'maintenance_busy',
                           'bridge_closing', 'session_active', 'dispose_unconfirmed',
                           'maintenance_unconfirmed', 'session_identity_unavailable'}
                raise BridgeError(code if code in allowed else 'bridge_rejected',
                                  uncertain=response.status >= 500 and code != 'dsh_delete_unsupported')
            return value
        except BridgeError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise BridgeError() from None
        finally:
            connection.close()

    def connect(self, *, timeout=5):
        result = self._request('hello', timeout=timeout)
        profiles, capabilities = result.get('permission_profiles'), result.get('capabilities')
        if (result.get('protocol') != PROTOCOL or not isinstance(result.get('instance_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', result['instance_id'])
                or result.get('execution_enabled') is not True
                or result.get('boundary_enforced') is not False
                or not isinstance(profiles, list) or 'dsh_native_v1' not in profiles
                or not isinstance(capabilities, dict) or not capabilities
                or any(type(value) is not bool or not value for value in capabilities.values())):
            raise BridgeError('bridge_not_ready', uncertain=False)
        self.instance_id, self.hello = result['instance_id'], result
        return result

    def call(self, operation, payload):
        if operation not in {'create', 'send', 'status', 'events', 'cancel', 'close', 'maintain'} or not self.instance_id:
            raise BridgeError('invalid_operation', uncertain=False)
        result = self._request('tasks/' + operation, {'instance_id': self.instance_id, **payload},
                               timeout=12 if operation in {'create', 'maintain'} else 5)
        if result.get('instance_id') != self.instance_id or result.get('task_id') != payload.get('task_id'):
            raise BridgeError('instance_changed')
        return result


def inspect_dsh_installation():
    """Run our bounded metadata reader, never DSH, its CLI or personal config.

    Startup only, outside the Store lock. Browser/model input cannot select any
    command, installation path, environment, endpoint or authentication token.
    """
    from .runtime_paths import RESOURCE_ROOT
    try:
        from .runtime_process import addon_command
        result = subprocess.run(addon_command('dsh-agent', '--describe'),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=5, check=False, cwd=str(RESOURCE_ROOT),
                                env={key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ},
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode != 0 or len(result.stdout) > 8192:
            return 'metadata_unavailable'
        value = json.loads(result.stdout)
        if (not isinstance(value, dict) or value.get('protocol') != 'alice-dsh-adapter-v1'
                or value.get('name') != 'dsh-agent'):
            return 'metadata_unavailable'
        if (value.get('installation_verified') is True
                and value.get('desktop_name') == 'dsh-desktop-next'
                and value.get('desktop_version') == '2.0.21-next'
                and value.get('runtime_version') == '0.2.1-rc.4'
                and value.get('cordis_version') == '4.0.5'):
            return 'reviewed_installation'
        if value.get('reason') == 'installation_version_changed':
            return 'version_changed'
        return 'installation_unverified'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 'metadata_unavailable'
