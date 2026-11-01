"""Standard-library host for a registered local connector's JSON-line CLI.

The server supplies a fixed command at composition time. Browsers can neither
select a program/path nor submit credentials. The connector remains an external
process using the public channel HTTP API; this module imports no addon code.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from .channels import ChannelError
from .connector_setup import (Environment, InstallGuard, SetupFailure, inspect_environment,
                              install_dependencies)


@dataclass(frozen=True)
class ConnectorSpec:
    command: tuple[str, ...]
    cwd: Path
    unavailable_reason: str = ""
    setup_root: Path | None = None


_STATES = {"idle", "scanning", "awaiting_confirmation", "pairing", "configured",
           "starting", "connected", "stopping", "stopped", "error"}
_MESSAGES = {
    "idle": "尚未连接。可以扫码，或使用本机已保存的连接。",
    "scanning": "请用手机 QQ 扫码；二维码只在当前页面显示。",
    "awaiting_confirmation": "请确认这是你本人；确认后保存身份，尚不会开始聊天。",
    "pairing": "请从你本人的 QQ 私聊机器人发送页面上的配对文字。",
    "configured": "身份已保存。点击“开始聊天”后启用 QQ 连接。",
    "starting": "正在准备连接，请稍候。",
    "connected": "QQ 已连接，只接收已确认所有者的私聊文字。",
    "stopping": "正在停止连接，当前已交给 Alice 的回复会继续完成。",
    "stopped": "连接已停止，已保存的身份仍保留在本机。",
    "error": "连接未完成，请重试或查看接入说明。",
    "unavailable": "本机连接组件尚未准备好，请按接入说明安装所需依赖。",
}
_ERROR_MESSAGES = {
    "owner_pair_required": "扫码已完成，还需从你本人 QQ 发送一次配对文字来确认身份。",
    "binding_rejected": "本机配置未能保存。请核对当前会话、已有绑定及配置文件是否被修改。",
    "connector_unavailable": "连接组件不可用，请检查 Node.js 与 QQ 组件是否已安装。",
    "connector_exited": "连接组件已退出；可重新连接，发送结果未知的消息不会自动重发。",
    "connector_protocol": "连接组件返回了不兼容的状态，请检查本机组件版本。",
    "operation_expired": "本次操作已过期，请重新扫码或配对。",
    "already_configured": "本机已有 QQ 连接资料。请使用已保存连接，或重新确认本人身份。",
    "binding_conflict": "已有另一份 QQ 身份绑定，本次未覆盖；请先停止原连接并按说明处理。",
    "not_configured": "还没有完整的 QQ 连接资料，请先扫码并确认本人身份。",
    "external_bridge_or_stale_lock": "另一个 QQ 连接仍在运行或状态无法确认；请先停止原连接。",
    "qr_expired": "当前二维码已过期，正在等待官方刷新；本次操作总时限不延长。",
    "qq_reconnecting": "QQ 连接暂时中断，正在重新连接。",
    "operation_failed": "操作未完成，请核对组件、文件权限与网络后重试。",
    "scan_failed": "扫码未完成，请重新扫码并核对官方账号权限。",
    "pair_failed": "配对连接未完成，请核对机器人权限后重试。",
    "binding_failed": "连接资料未能保存到 Alice，请核对已有绑定或配置文件变更。",
    "config_invalid": "本机 QQ 配置无效，请按接入说明检查配置。",
    "token_invalid": "本机连接凭据无效，请按接入说明检查配置。",
    "alice_not_ready": "Alice 尚未准备好，请先完成初始化并核对连接配置。",
    "qq_start_failed": "QQ 连接未能建立，请核对机器人权限与网络。",
    "bridge_failed": "QQ 连接未能运行，请核对本机配置、权限与网络。",
    "bridge_storage_failed": "连接记录无法保存，已停止接收新消息，请检查本机磁盘与目录权限。",
    "busy": "原连接还未完全停止，请稍后再试。",
    "stopped_forced": "连接组件已停止。中途发送的结果可能未知，请先在 QQ 核对；不会自动重发。",
    "stop_unconfirmed": "停止尚未确认，请检查本机进程；不要同时启动第二个连接。",
    "unsupported_node": "需要 Node.js 22.19 或更新版本，请安装后点击重新检测。",
    "installation_busy": "QQ 组件正在安装或占用状态尚未确认，请稍后重新检测。",
    "qr_invalid": "官方返回的二维码不符合接入要求，请稍后重试。",
}


class LocalConnectorManager:
    """One owned child, one confirmed operation; reads never start anything."""

    def __init__(self, spec: ConnectorSpec, service):
        self.spec = spec
        self.service = service
        self._lock = threading.RLock()
        self._process = None
        self._reader = None
        self._closed = False
        self._pending_action = None
        self._epoch = None
        self._setup_thread = None
        self._setup_cancel = threading.Event()
        self._environment = inspect_environment(spec.setup_root) if spec.setup_root else None
        self._setup = self._environment.public() if self._environment else None
        if self._environment:
            self._apply_environment(self._environment)
            spec = self.spec
        self._state = {"state": "unavailable" if spec.unavailable_reason or not spec.command else "idle",
                       "operation_id": uuid.uuid4().hex}

    def status(self) -> dict:
        activity = self.service.chat_activity()
        with self._lock:
            result = copy.deepcopy(self._state)
            setup = copy.deepcopy(self._setup)
            if setup and (self._closed or result["state"] in {
                    "scanning", "pairing", "awaiting_confirmation", "starting", "connected", "stopping"}
                    or (result["state"] == "error" and self._process is not None and self._process.poll() is None)):
                setup["can_install"] = setup["can_recheck"] = False
        result["message"] = _ERROR_MESSAGES.get(result.get("error_code"), _MESSAGES[result["state"]])
        if result["state"] == "unavailable" and self.spec.unavailable_reason:
            result["message"] = self.spec.unavailable_reason
        if setup is not None:
            result["setup"] = setup
            if result["state"] == "unavailable":
                result["message"] = setup["message"]
        result["channel_enabled"] = activity["channels_enabled"]
        return result

    def _apply_environment(self, environment: Environment) -> None:
        self._environment = environment
        command = ((environment.node or "node", *self.spec.command[1:]) if self.spec.command else ())
        self.spec = replace(self.spec, command=command,
                            unavailable_reason="" if environment.state == "ready" else environment.public()["message"])
        self._setup = environment.public()

    @staticmethod
    def _close_idle_helper(child) -> None:
        if child is None or child.poll() is not None:
            return
        if child.stdin:
            try:
                child.stdin.close()
            except OSError:
                pass
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)

    def _setup_worker(self, action: str, op: str, child) -> None:
        def report(message):
            with self._lock:
                if not self._closed and op == self._state["operation_id"]:
                    self._setup["message"] = message
        stage = "stop"
        try:
            self._close_idle_helper(child)
            if self._setup_cancel.is_set():
                raise SetupFailure("cancelled")
            stage = "inspect"
            if action == "recheck":
                # The same gate excludes terminal bridges during explicit detection.
                if not self.spec.setup_root.is_dir():
                    environment = Environment("component_missing")
                else:
                    with InstallGuard(self.spec.setup_root):
                        environment = inspect_environment(self.spec.setup_root)
            else:
                environment = inspect_environment(self.spec.setup_root)
                stage = "prepare"
                environment = install_dependencies(self.spec.setup_root, environment,
                                                   self._setup_cancel, report)
            with self._lock:
                if self._closed or op != self._state["operation_id"]:
                    return
                self._apply_environment(environment)
                self._state = {"state": "idle" if environment.state == "ready" else "unavailable",
                               "operation_id": uuid.uuid4().hex}
        except Exception as exc:
            failure = exc if isinstance(exc, SetupFailure) else SetupFailure(stage=stage)
            message = str(failure)
            with self._lock:
                if self._closed or op != self._state["operation_id"]:
                    return
                self._setup = {"state": "failed", "message": message,
                               "diagnostic": failure.diagnostic(stage),
                               "can_install": bool(self._environment and self._environment.npm
                                                   and self._environment.state == "dependencies_missing"),
                               "can_recheck": True}
                self._state = {"state": "unavailable", "operation_id": uuid.uuid4().hex}
                if child is not None and child.poll() is None:
                    # Preserve control of an owned helper whose exit was not
                    # confirmed; the user can still explicitly stop it.
                    self._process = child
                    self._state.update(state="error", error_code="stop_unconfirmed")

    def _begin_setup_locked(self, action: str) -> None:
        if not self.spec.setup_root:
            raise ChannelError(503, "connector_unavailable", _ERROR_MESSAGES["connector_unavailable"])
        if self._setup_thread is not None and self._setup_thread.is_alive():
            raise ChannelError(409, "connector_busy", "组件正在安装或检测，请稍候")
        if self._state["state"] not in {"idle", "configured", "stopped", "error", "unavailable"}:
            raise ChannelError(409, "connector_busy", "请先停止或取消当前 QQ 连接，再安装或检测")
        child = self._process
        if (self._state["state"] == "error" and child is not None and child.poll() is None):
            raise ChannelError(409, "connector_busy", "请先点击停止连接，确认结束后再安装或检测")
        if action == "install" and not self._setup.get("can_install"):
            raise ChannelError(409, "setup_unavailable", "请先按页面提示准备 Node.js 并重新检测")
        # Detach the stopped/idle helper before closing it: late events cannot
        # overwrite installation state or revive a retired operation.
        self._process = None
        self._pending_action = None
        op = uuid.uuid4().hex
        self._state = {"state": "unavailable", "operation_id": op}
        self._setup = {"state": "installing", "message": "正在检查本机组件，请稍候。" if action == "recheck"
                       else "正在下载并安装 QQ 组件，请稍候。", "can_install": False, "can_recheck": False}
        self._setup_thread = threading.Thread(target=self._setup_worker, args=(action, op, child),
                                              name="alice-qq-setup", daemon=True)
        self._setup_thread.start()

    def _send_locked(self, action: str, operation_id: str) -> None:
        if self._process is None or self._process.poll() is not None or self._process.stdin is None:
            raise ChannelError(503, "connector_exited", _ERROR_MESSAGES["connector_exited"])
        try:
            line = json.dumps({"action": action, "operation_id": operation_id}) + "\n"
            self._process.stdin.write(line.encode("utf-8"))
            self._process.stdin.flush()
        except (OSError, ValueError):
            raise ChannelError(503, "connector_exited", _ERROR_MESSAGES["connector_exited"]) from None

    def _start_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if self.spec.unavailable_reason or not self.spec.command:
            raise ChannelError(503, "connector_unavailable", _ERROR_MESSAGES["connector_unavailable"])
        try:
            child = subprocess.Popen(self.spec.command, cwd=self.spec.cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self._state.update(state="unavailable", error_code="connector_unavailable")
            if self._setup is not None:
                self._setup = {"state": "failed", "message": "连接组件未能启动，请点击重新检测。",
                               "can_install": False, "can_recheck": True}
            raise ChannelError(503, "connector_unavailable", _ERROR_MESSAGES["connector_unavailable"]) from None
        self._process = child
        self._reader = threading.Thread(target=self._read, args=(child,), name="alice-connector", daemon=True)
        self._reader.start()

    def _request_stop_locked(self, op: str) -> None:
        self._pending_action = None
        child = self._process
        if child is None or child.poll() is not None:
            self._state = {"state": "stopped", "operation_id": uuid.uuid4().hex}
            return
        self._state = {"state": "stopping", "operation_id": op}
        try:
            self._send_locked("stop", op)
        except ChannelError:
            pass  # The bounded waiter still checks/terminates this owned child.
        threading.Thread(target=self._finish_stop, args=(child, op),
                         name="alice-connector-stop", daemon=True).start()

    def _finish_stop(self, child, op: str) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with self._lock:
                if (self._closed or child is not self._process or op != self._state["operation_id"]
                        or self._state["state"] == "stopped"):
                    return
            if child.poll() is not None:
                break
            time.sleep(0.1)
        # A graceful stop may arrive exactly as the deadline expires. Recheck
        # while holding the operation lock before signalling: the same helper
        # may already have been reused by a newly requested connection.
        with self._lock:
            if (self._closed or child is not self._process or op != self._state["operation_id"]
                    or self._state["state"] != "stopping"):
                return
            forced = child.poll() is None
            if forced:
                try:
                    child.terminate()
                except OSError:
                    pass
        if forced:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with self._lock:
                    if (self._closed or child is not self._process or op != self._state["operation_id"]
                            or self._state["state"] != "stopping"):
                        return
                    try:
                        child.kill()
                    except OSError:
                        pass
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        with self._lock:
            if not self._closed and child is self._process and op == self._state["operation_id"]:
                if child.poll() is None:
                    self._state = {"state": "error", "operation_id": op, "error_code": "stop_unconfirmed"}
                else:
                    self._state = {"state": "stopped", "operation_id": uuid.uuid4().hex,
                                   **({"error_code": "stopped_forced"} if forced else {})}

    def action(self, payload: dict) -> dict:
        if (not isinstance(payload, dict) or set(payload) - {"action", "operation_id"}
                or payload.get("action") not in {"scan", "pair", "confirm", "run", "cancel", "stop", "install", "recheck"}):
            raise ChannelError(400, "invalid_request", "连接操作无效")
        action = payload["action"]
        activity = self.service.chat_activity()
        with self._lock:
            if self._closed or activity["status"] == "draining":
                raise ChannelError(503, "draining", "Alice 正在关闭，请稍后重新打开")
            op = self._state["operation_id"]
            supplied = payload.get("operation_id")
            if supplied != op and not (supplied is None and action == "scan" and self._state["state"] == "idle"):
                raise ChannelError(409, "operation_changed", "连接状态已变化，请刷新面板后操作")
            state = self._state["state"]
            if action in {"install", "recheck"}:
                self._begin_setup_locked(action)
            elif action in {"scan", "pair", "run"}:
                allowed = {"idle", "configured", "stopped", "error"}
                if action == "pair":
                    allowed.add("awaiting_confirmation")
                if state not in allowed:
                    raise ChannelError(409, "connector_busy", "请先完成或取消当前连接操作")
                if action == "run" and activity["status"] == "not_ready":
                    raise ChannelError(409, "not_ready", "请先完成 Alice 人格初始化，再开始 QQ 聊天")
                self._start_locked()
                self._epoch = activity["session_epoch"]
                self._pending_action = action
                op = uuid.uuid4().hex
                self._state = {"state": {"scan": "scanning", "pair": "pairing", "run": "starting"}[action],
                               "operation_id": op}
                self._send_locked(action, op)
            elif action == "confirm":
                if state != "awaiting_confirmation" or not self._state.get("owner_openid"):
                    raise ChannelError(409, "confirmation_unavailable", "请先扫码并取得本人身份，必要时完成配对")
                if activity["session_epoch"] != self._epoch:
                    raise ChannelError(409, "session_changed", "会话已重置，请重新开始连接操作")
                expires = self._state.get("expires_at")
                if expires is not None and expires <= time.time() * 1000:
                    raise ChannelError(409, "operation_expired", _ERROR_MESSAGES["operation_expired"])
                self._pending_action = "confirm"
                self._state = {"state": "starting", "operation_id": op}
                self._send_locked(action, op)
            elif action == "cancel":
                if state not in {"scanning", "pairing", "awaiting_confirmation", "starting"}:
                    raise ChannelError(409, "operation_changed", "当前没有可取消的连接操作")
                stopping_bridge = state == "starting" and self._pending_action != "confirm"
                if stopping_bridge:
                    self._request_stop_locked(op)
                else:
                    self._pending_action = None
                    self._send_locked(action, op)
                    self._state = {"state": "stopped", "operation_id": uuid.uuid4().hex}
            else:
                if state not in {"connected", "starting", "stopping", "error"}:
                    raise ChannelError(409, "operation_changed", "当前没有由本页面启动的连接")
                if state != "stopping":
                    self._request_stop_locked(op)
        return self.status()

    @staticmethod
    def _public_state(event: dict) -> dict:
        state = event.get("state")
        if state not in _STATES:
            raise ValueError("state")
        result = {"state": state, "operation_id": event["operation_id"]}
        for name in ("app_id", "owner_openid"):
            value = event.get(name)
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                    raise ValueError("identity")
                result[name] = value
        code = event.get("error_code")
        if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code):
            # Arbitrary helper diagnostics are never displayed as text.
            result["error_code"] = code if code in _ERROR_MESSAGES else "connection_failed"
        expires = event.get("expires_at")
        if expires is not None:
            if type(expires) not in (int, float) or not 0 < expires <= (time.time() + 360) * 1000:
                raise ValueError("expiry")
            result["expires_at"] = expires
        pair_code = event.get("pair_code")
        if pair_code is not None:
            if not isinstance(pair_code, str) or not re.fullmatch(r"(?:/pair )?[0-9a-f]{20}", pair_code):
                raise ValueError("pair_code")
            result["pair_code"] = pair_code
        matrix = event.get("qr_matrix")
        if matrix is not None:
            if (state != "scanning" or not isinstance(matrix, list) or not 21 <= len(matrix) <= 177
                    or any(not isinstance(row, str) or len(row) != len(matrix)
                           or not re.fullmatch(r"[01]+", row) for row in matrix)):
                raise ValueError("qr_matrix")
            result["qr_matrix"] = matrix
        return result

    def _event(self, child, event: dict) -> None:
        with self._lock:
            op = event.get("operation_id")
            if self._closed or child is not self._process or op != self._state["operation_id"]:
                return
            if event.get("type") == "state":
                if self._state["state"] == "stopping" and event.get("state") != "stopped":
                    return  # A late reconnect/error cannot reopen the stopped operation.
                self._state = self._public_state(event)
                if self._state["state"] in {"configured", "connected", "stopped", "error"}:
                    self._pending_action = None
                return
            if event.get("type") != "binding" or self._pending_action not in {"confirm", "run"}:
                raise ValueError("unexpected_binding")
            action = self._pending_action
            epoch = self._epoch
            binding = {key: event.get(key) for key in ("app_id", "sender_id", "token_sha256")}

        def current():
            with self._lock:
                return (not self._closed and child is self._process and op == self._state["operation_id"]
                        and self._pending_action == action)

        @contextmanager
        def commit_guard():
            with self._lock:
                if not current():
                    raise ValueError("连接操作已取消或失效")
                yield

        try:
            self.service.configure_local_channel(binding, epoch, enabled=action == "run", guard=commit_guard)
        except (ValueError, OSError):
            with self._lock:
                if current():
                    self._pending_action = None
                    self._state = {"state": "error", "operation_id": op, "error_code": "binding_rejected"}
                    self._send_locked("binding_failed", op)
            return
        with self._lock:
            if current():
                self._send_locked("configured", op)

    def _read(self, child) -> None:
        try:
            while True:
                line = child.stdout.readline(65537)
                if not line:
                    break
                if len(line) > 65536 or not line.endswith(b"\n"):
                    raise ValueError("line")
                event = json.loads(line.decode("utf-8"))
                if not isinstance(event, dict):
                    raise ValueError("event")
                self._event(child, event)
        except Exception:
            with self._lock:
                if child is self._process and not self._closed and self._state["state"] != "stopping":
                    self._pending_action = None
                    self._state = {"state": "error", "operation_id": self._state["operation_id"],
                                   "error_code": "connector_protocol"}
            try:
                child.terminate()
            except OSError:
                pass
        finally:
            if child.stdout:
                child.stdout.close()
            with self._lock:
                if child is self._process and not self._closed and self._state["state"] not in {"stopped", "stopping", "error"}:
                    self._pending_action = None
                    self._state = {"state": "error", "operation_id": self._state["operation_id"],
                                   "error_code": "connector_exited"}

    def close(self) -> None:
        """Stop only our own helper; bounded shutdown never kills other bridges."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending_action = None
            self._setup_cancel.set()
            setup_thread = self._setup_thread
            child = self._process
            if child is not None:
                try:
                    self._send_locked("stop", self._state["operation_id"])
                except ChannelError:
                    pass
                if child.stdin:
                    try:
                        child.stdin.close()
                    except OSError:
                        pass
        if setup_thread is not None:
            setup_thread.join(timeout=10)
        if child is None:
            return
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                child.terminate()
                child.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    child.kill()
                except OSError:
                    pass
