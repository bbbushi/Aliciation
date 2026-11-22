"""One explicitly armed evaluation at a time; all persistence uses the service lock.

The worker owns its client, never a database connection. Network waits stay outside
the chat lock. Only structured, reference-based results reach EvaluationStore.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading

from . import evaluation
from .llm import make_client


class EvaluationCoordinator:
    def __init__(self, service):
        self.service = service
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.epoch = 0
        self.bind_store()

    def bind_store(self):
        # Called only during construction or while holding the original service lock.
        self.repo = evaluation.EvaluationStore(self.service.store.conn)

    def before_reset(self):
        self.epoch += 1
        self.repo.cancel_pending("reset")

    def begin_shutdown(self):
        # Lock-free admission signal: a long chat must not delay stopping new dispatches.
        self.stopping.set()

    def shutdown(self):
        self.begin_shutdown()
        with self.service._lock:
            self.repo.cancel_pending("shutdown")
            worker = self.thread
        # Do not close Store or client underneath an in-flight provider request.
        if worker is not None and worker is not threading.current_thread():
            worker.join()

    def access(self, action, *, dataset_id=None, **args):
        if not self.service._lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            identity = self.repo.dataset_id
            if action not in ("settings", "list", "calibrations") and dataset_id is None:
                raise ValueError("请先读取本次会话身份，再操作P1核查")
            if dataset_id is not None and dataset_id != identity:
                return {"status": "reset", "dataset_id": identity}
            if self.stopping.is_set():
                return {"status": "error", "dataset_id": identity, "error": "服务正在收尾，P1核查已停止接单"}
            if action == "settings":
                result = self.repo.settings()
                result["model"] = self.service.cfg.llm.cheap_model
            elif action == "set_limit":
                limit = args.get("daily_limit")
                if type(limit) is not int or not 0 <= limit <= 100:
                    raise ValueError("日请求额度须为0到100的整数")
                if limit and args.get("allow_paid") is not True:
                    raise ValueError("启用费用前须明确同意评审请求可能产生费用")
                result = self.repo.set_limit(limit)
                result["model"] = self.service.cfg.llm.cheap_model
            elif action == "arm":
                if self.thread is not None and self.thread.is_alive():
                    raise ValueError("上一项核查仍在收尾，请等待完成")
                result = {"run": self.repo.arm(args.get("action_id"), self.service.cfg.llm.cheap_model,
                                               config_revision=self._model_stamp())}
            elif action == "list":
                result = self.repo.list_runs(before=args.get("before"), status_filter=args.get("filter", "all"))
            elif action == "detail":
                result = self.repo.detail(args.get("id"), reveal=args.get("reveal", False))
            elif action == "cancel":
                self.repo.cancel(args.get("id"))
                result = {"cancelled": True}
            elif action == "delete":
                result = self.repo.delete_run(args.get("id"))
            elif action == "label":
                result = self.repo.label(args.get("id"), args.get("ordinal"), args.get("verdict"),
                                         args.get("revealed", False))
            elif action == "calibrate":
                result = self.repo.calibrate(args.get("run_ids"), args.get("minimum_agreement"),
                                             args.get("attest", False))
            elif action == "calibrations":
                result = self.repo.calibrations()
            else:
                raise ValueError("未知的P1核查操作")
            return {**result, "status": "ok", "dataset_id": identity}
        except ValueError as exc:
            # Repository validation errors are fixed messages, never provider exceptions.
            return {"status": "error", "dataset_id": self.repo.dataset_id, "error": str(exc)}
        except Exception:
            return {"status": "error", "error": "P1操作未完成，请刷新核查记录；不要自动重复付费请求"}
        finally:
            self.service._lock.release()

    def capture(self, run_id):
        if self.stopping.is_set():
            raise ValueError("服务正在退出，无法核查新轮次")
        record = self.repo.capture(run_id)
        if (record.get("model") != self.service.cfg.llm.cheap_model
                or record.get("config_revision") != self._model_stamp()):
            self.repo.fail(record["id"], "cancelled", "configuration_changed")
            raise ValueError("核查模型配置已变化，请重新授权下一轮")
        cfg = copy.deepcopy(self.service.cfg)
        cfg.llm.api_key = cfg.llm.resolved_key()  # Freeze environment-sourced credentials too.
        return {"id": record["id"], "dataset_id": self.repo.dataset_id, "epoch": self.epoch,
                "cfg": cfg}

    def _model_stamp(self):
        values = [self.service.cfg.llm.base_url, self.service.cfg.llm.cheap_model,
                  self.service._config_revision]
        return hashlib.sha256(json.dumps(values).encode("utf-8")).hexdigest()

    def bind_turn(self, job, turn_id):
        return self.repo.bind_turn(job["id"], turn_id)

    def abort_chat(self, job):
        try:
            self.repo.fail(job["id"], "failed", "chat_failed")
        except Exception:
            pass  # Evaluation failure must never mask the real chat outcome.

    def enqueue(self, job) -> bool:
        with self.service._lock:
            if not self._current(job):
                if job["epoch"] == self.epoch and job["dataset_id"] == self.repo.dataset_id:
                    self.repo.fail(job["id"], "cancelled", "shutdown")
                return False
            if self.thread is not None and self.thread.is_alive():
                self.repo.fail(job["id"], "failed", "worker_busy")
                return False
            self.thread = threading.Thread(target=self._work, args=(job,), name="AliceP1", daemon=False)
            self.thread.start()
            return True

    def _current(self, job):
        return (not self.stopping.is_set() and job["epoch"] == self.epoch
                and job["dataset_id"] == self.repo.dataset_id)

    def _locked(self, job, method, *args):
        with self.service._lock:
            if not self._current(job):
                raise ValueError("核查已取消或所属会话已变化")
            return getattr(self.repo, method)(*args)

    def _call(self, job, client, stage, messages):
        attempt = self._locked(job, "reserve_attempt", job["id"], stage)
        try:
            # Reservation is the dispatch boundary; cancellation cannot revoke an
            # already reserved provider attempt or promise that it is uncharged.
            self._locked(job, "get_work", job["id"])
            response = client.chat.completions.create(
                model=job["cfg"].llm.cheap_model, messages=messages,
                temperature=0, response_format={"type": "json_object"},
                max_tokens=2048, timeout=45.0, stream=False,
            )
            usage = getattr(response, "usage", None)
            measured = None if usage is None else {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
            }
            with self.service._lock:
                if job["epoch"] == self.epoch and job["dataset_id"] == self.repo.dataset_id:
                    self.repo.finish_attempt(attempt, measured, "received")
            raw = response.choices[0].message.content
            if not isinstance(raw, str) or len(raw) > 32000:
                raise ValueError("评审响应格式无效")
            return raw
        except BaseException:
            with self.service._lock:
                if job["epoch"] == self.epoch and job["dataset_id"] == self.repo.dataset_id:
                    try:
                        self.repo.finish_attempt(attempt, None, "unknown")
                    except Exception:
                        pass  # Keep the original failure private, even if accounting fails.
            raise

    def _work(self, job):
        client = None
        try:
            source = self._locked(job, "get_work", job["id"])
            # make_client has max_retries=0; never call llm.chat's retrying wrapper here.
            client = make_client(job["cfg"])
            extracted = self._call(job, client, "extract", evaluation.extraction_messages(source["reply"]))
            claims = evaluation.parse_claims(extracted, source["reply"])
            self._locked(job, "save_claims", job["id"], claims)
            if not claims:
                return
            material = self._locked(job, "material", job["id"])
            if not material["claims"]:
                return
            judged = self._call(job, client, "judge", evaluation.verdict_messages(material))
            verdicts = evaluation.parse_verdicts(judged, material)
            self._locked(job, "finish_verdicts", job["id"], verdicts)
        except BaseException as exc:
            # No prompt, evidence, free reasoning or provider exception is logged/persisted.
            status, reason = "failed", "evaluation_failed"
            if isinstance(exc, evaluation.EvidenceUnavailable):
                status, reason = "evidence_unavailable", "evidence_changed"
            elif isinstance(exc, evaluation.EvaluationIncomplete):
                status, reason = "incomplete", "input_limit"
            try:
                with self.service._lock:
                    if job["epoch"] == self.epoch and job["dataset_id"] == self.repo.dataset_id:
                        self.repo.fail(job["id"], status, reason)
            except Exception:
                # A second persistence failure must not print a chained provider traceback.
                pass
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            job.clear()  # Drop captured configuration and credentials after this attempt.
