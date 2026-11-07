"""Explicit expression-only preparation and recoverable installation.

The Markdown is the only runtime authority. Candidates and source documents are
private audit material, never a fallback runtime store. Reuses the installation
journal's atomic IO/SQLite primitives, with a distinct write set: persona_version
only, no memories, seeding or maintenance watermarks.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import config as paths
from .persona_transaction import atomic_text, write_record, digest, current_text, _begin, _set_meta, _plain_stat

KIND = "expression_update_v2"
TOKEN = re.compile(r"exp_[0-9a-f]{32}\Z")
TERMINAL = {"completed", "aborted", "cancelled", "rolled_back"}
MAX_AUDIT_BYTES = 16 * 1024 * 1024
PREPARATION_POLICY = 12  # Shared bounded expressive interpretation and evidence-bound factual regression.
MARKER_FIELDS = ("kind", "token", "epoch", "before_sha256", "after_sha256", "before_version", "after_version")


def root():
    return paths.PERSONA_DIR / "expressions"


def location(token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError("表达操作身份无效")
    return root() / token


def read_record(path):
    if _plain_stat(path).st_size > MAX_AUDIT_BYTES:
        raise ValueError("表达记录超过读取上限")
    with path.open("rb") as stream:
        raw = stream.read(MAX_AUDIT_BYTES + 1)
    if len(raw) > MAX_AUDIT_BYTES:
        raise ValueError("表达记录超过读取上限")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("表达记录损坏")
    return value


def _binding(store, revision, epoch):
    if epoch != store.meta_get("session_epoch"):
        raise ValueError("会话已更换，请重新准备表达")
    if revision != digest(current_text()):
        raise ValueError("人格已变化，请重新打开当前人格；未覆盖后来的编辑")


def _rollback_proven(store, original, undo):
    """Prove the inverse transaction from both journals and committed markers.

    This proves a historical rollback, not that its result is still current.
    Later edits never prevent reconciliation and never get overwritten by it.
    """
    for record in (original, undo):
        location(record.get("token"))
        if record.get("kind") != KIND or record.get("phase") not in {"completed", "rolled_back"}:
            return False
        if type(record.get("after_version")) is not int:
            return False
        marker = store.meta_get("expression_update:" + record["token"])
        if not marker or json.loads(marker) != {key: record[key] for key in MARKER_FIELDS}:
            return False
    return (undo.get("mode") == "rollback" and undo.get("metadata", {}).get("rollback_of") == original["token"]
            and undo["token"] != original["token"] and undo["epoch"] == original["epoch"]
            and undo["before_sha256"] == original["after_sha256"]
            and undo["after_sha256"] == original["before_sha256"]
            and undo["before_version"] == str(original["after_version"])
            and undo["after_version"] == original["after_version"] + 1)


def _rollback_relations(store, records):
    relations = {}
    for undo in records.values():
        parent = undo.get("metadata", {}).get("rollback_of")
        if undo.get("mode") != "rollback" or not parent or undo.get("phase") not in {"completed", "rolled_back"}:
            continue
        original = records.get(parent)
        if original is None:
            original = read_record(location(parent) / "op.json")
        if not _rollback_proven(store, original, undo):
            raise ValueError("回退关系缺少同代际、相反文件摘要和连续版本的事务证据")
        if parent in relations and relations[parent]["token"] != undo["token"]:
            raise ValueError("同一表达操作存在冲突的回退记录，需人工核对")
        relations[parent] = undo
    return relations


def _project_operation(record, relations):
    """Pure read projection, including an un-reconciled proven inverse."""
    undo = relations.get(record.get("token"))
    if undo is not None:
        return {**record, "phase": "rolled_back", "rollback_token": undo["token"]}
    return record


def _read_relations(store, *, deadline=None):
    records, incomplete = {}, False
    for journal in root().glob("exp_*/op.json"):
        if (deadline is not None and time.monotonic() >= deadline) or len(records) >= 2000:
            incomplete = True
            break
        record = read_record(journal)
        if location(record.get("token")) != journal.parent:
            raise ValueError("表达操作记录身份不一致")
        records[record["token"]] = record
    return _rollback_relations(store, records), incomplete


def recover(store):
    """Finish a proven commit or undo only its own metadata; never replay text."""
    for journal in sorted(root().glob("exp_*/op.json")):
        rec = read_record(journal)
        token = rec.get("token")
        if location(token) != journal.parent or rec.get("kind") != KIND:
            raise ValueError("表达恢复记录身份不一致")
        if rec.get("phase") in TERMINAL:
            continue
        if rec.get("phase") not in {"prepared", "db_committed", "file_replaced"}:
            raise ValueError("表达恢复阶段不明，需人工核对")
        if rec.get("epoch") != store.meta_get("session_epoch"):
            raise ValueError("表达恢复代际不符，需人工核对")
        raw = store.meta_get("expression_update:" + token)
        marker = json.loads(raw) if raw else None
        if marker is not None and marker != {key: rec[key] for key in
                ("kind", "token", "epoch", "before_sha256", "after_sha256", "before_version", "after_version")}:
            raise ValueError("表达恢复文件与数据库版本不一致")
        now = digest(current_text())
        version = store.meta_get("persona_version")
        if marker and now == rec["after_sha256"] and version == str(rec["after_version"]):
            rec["phase"] = "completed"
        elif now == rec["before_sha256"]:
            if marker:
                if version != str(rec["after_version"]):
                    raise ValueError("表达恢复遇到后续元数据修改，需人工核对")
                _begin(store)
                try:
                    _set_meta(store.conn, "persona_version", rec["before_version"])
                    _set_meta(store.conn, "expression_update:" + token, None)
                    store.conn.commit()
                except BaseException:
                    store.conn.rollback()
                    raise
            rec["phase"] = "aborted"
        else:
            raise ValueError("表达恢复冲突：当前人格不是提交前后任一版本；保留现场与后续编辑")
        write_record(journal, rec)
    # A completed inverse may have committed just before the original journal's
    # rolled_back write. Reconcile that link without replaying any persona file.
    records = {}
    for journal in root().glob("exp_*/op.json"):
        rec = read_record(journal)
        records[rec["token"]] = rec
    for token, undo in _rollback_relations(store, records).items():
        rec = records[token]
        if rec.get("phase") != "rolled_back" or rec.get("rollback_token") != undo["token"]:
            rec.update(phase="rolled_back", rollback_token=undo["token"])
            write_record(location(token) / "op.json", rec)


def commit(store, before, candidate, *, token=None, mode="source_update", metadata=None):
    """Caller holds service control locks. No model calls and no other write set."""
    recover(store)
    token = token or "exp_" + uuid.uuid4().hex
    folder = location(token)
    journal = folder / "op.json"
    epoch = store.meta_get("session_epoch")
    if journal.exists():
        rec = read_record(journal)
        if (rec.get("phase") == "completed" and rec.get("epoch") == epoch
                and rec.get("after_sha256") == digest(current_text())
                and rec.get("after_sha256") == digest(candidate)
                and str(rec.get("after_version")) == store.meta_get("persona_version")):
            return token
        raise ValueError("该表达操作已使用或版本已变化，不能重放")
    _binding(store, digest(before), epoch)
    if not before or not epoch:
        raise ValueError("先安装人格，再更新表达")
    from .persona_growth import bodies, EXPRESSION
    old, new = bodies(before), bodies(candidate)
    if {k: v for k, v in old.items() if k not in EXPRESSION} != {
            k: v for k, v in new.items() if k not in EXPRESSION}:
        raise ValueError("表达更新不能改变认知、来历或生长小节")
    folder.mkdir(parents=True, exist_ok=True)
    atomic_text(folder / "previous.md", before)
    atomic_text(folder / "candidate.md", candidate)
    rec = {"kind": KIND, "token": token, "epoch": epoch, "phase": "prepared", "mode": mode,
           "before_sha256": digest(before), "after_sha256": digest(candidate),
           "before_version": store.meta_get("persona_version"),
           "after_version": int(store.meta_get("persona_version", "0") or 0) + 1,
           "created_at": time.time(), "metadata": metadata or {}}
    write_record(journal, rec)
    marker = {key: rec[key] for key in ("kind", "token", "epoch", "before_sha256", "after_sha256",
                                      "before_version", "after_version")}
    _begin(store)
    try:
        _set_meta(store.conn, "persona_version", rec["after_version"])
        _set_meta(store.conn, "expression_update:" + token, json.dumps(marker, ensure_ascii=False))
        store.conn.commit()
    except BaseException:
        store.conn.rollback()
        recover(store)
        raise
    try:
        rec["phase"] = "db_committed"
        write_record(journal, rec)
        if current_text() != before:
            raise ValueError("人格在提交期间被编辑，不能覆盖")
        atomic_text(paths.PERSONA_FILE, candidate)
        rec["phase"] = "completed"
        write_record(journal, rec)
    except BaseException:
        recover(store)
        if digest(current_text()) != digest(candidate):
            raise
    return token


def rollback(store, token, revision, epoch, *, cfg=None):
    recover(store)
    folder = location(token)
    rec = read_record(folder / "op.json")
    if rec.get("phase") == "rolled_back" and rec.get("epoch") == epoch == store.meta_get("session_epoch"):
        undo = read_record(location(rec.get("rollback_token")) / "op.json")
        if (undo.get("phase") == "completed" and digest(current_text()) == undo.get("after_sha256")
                and store.meta_get("persona_version") == str(undo.get("after_version"))):
            return {"status": "rolled_back", "token": token, "rollback_token": undo["token"],
                    "revision": undo["after_sha256"], "session_epoch": epoch}
        raise ValueError("该操作已回退且人格后来发生变化，不能重复覆盖")
    _binding(store, revision, epoch)
    if rec.get("phase") != "completed" or rec.get("epoch") != epoch or rec.get("after_sha256") != revision:
        raise ValueError("只能回退当前完整版本；后续修改存在时请在编辑器明确修订")
    if str(rec.get("after_version")) != store.meta_get("persona_version"):
        raise ValueError("人格修订号已变化，不能覆盖后续修改")
    before = (folder / "previous.md").read_text(encoding="utf-8")
    if digest(before) != rec["before_sha256"]:
        raise ValueError("回退快照损坏")
    from .persona_context import parse_snapshot, validate_expression
    validate_expression(parse_snapshot(before), cfg=cfg)
    if cfg is not None:
        validate_budget(before, cfg)
    undo = commit(store, current_text(), before, mode="rollback", metadata={"rollback_of": token})
    rec.update(phase="rolled_back", rollback_token=undo)
    try:
        write_record(folder / "op.json", rec)
    except OSError:
        pass  # The undo transaction itself already proves success.
    return {"status": "rolled_back", "token": token, "rollback_token": undo,
            "revision": digest(before), "session_epoch": epoch}


def public_status(store, limit=20):
    """Bounded metadata only. Reading never recovers or calls a model."""
    import heapq
    entries, incomplete = [], False
    deadline = time.monotonic() + 1.0
    try:
        for folder in root().iterdir():
            if time.monotonic() > deadline:
                incomplete = True
                break
            if TOKEN.fullmatch(folder.name):
                entries.append((_plain_stat(folder).st_mtime_ns, folder))
    except FileNotFoundError:
        pass
    selected = heapq.nlargest(limit, entries)
    try:
        relations, relationship_incomplete = _read_relations(store, deadline=deadline)
        incomplete |= relationship_incomplete
    except (OSError, ValueError, TypeError, KeyError):
        relations, incomplete = {}, True
    items = []
    for _, folder in selected:
        try:
            path = folder / "op.json"
            rec = read_record(path if path.exists() else folder / "plan.json")
            rec = _project_operation(rec, relations)
            item = {key: rec.get(key) for key in ("token", "phase", "status", "source_hash", "created_at",
                "before_sha256", "after_sha256", "before_version", "after_version", "projection_chars", "rollback_token")}
            item["rollback_of"] = rec.get("metadata", {}).get("rollback_of")
            item["epoch_matches"] = rec.get("epoch") == store.meta_get("session_epoch")
            item["format"] = "expression-v2"
            item["metadata"] = rec.get("metadata", {})
            item["status"] = rec.get("status") or {"completed": "applied", "aborted": "prepare_failed"}.get(rec.get("phase"), rec.get("phase"))
            if item["status"] == "preparing":
                item["status"] = "prepare_failed"
            if incomplete and rec.get("phase") == "completed" and rec.get("token") not in relations:
                item["status"] = "state_unconfirmed"
            items.append(item)
        except (OSError, ValueError, TypeError):
            items.append({"token": folder.name, "status": "unreadable"})
    return {"items": items, "scan_incomplete": incomplete, "truncated": incomplete or len(entries) > limit}


def _source(payload, cfg):
    from .expression import limits_for
    maximum = limits_for(cfg)["max_source_chars"]
    if "source_op_id" in payload and "source_text" in payload:
        raise ValueError("请明确选择一份原稿：上传/粘贴或审计来源不能同时提交")
    if "source_op_id" in payload:
        token = payload["source_op_id"]
        if isinstance(token, str) and TOKEN.fullmatch(token):
            folder = location(token)
        else:
            from .persona_transaction import validate_op_id
            folder = paths.PERSONA_DIR / "imports" / validate_op_id(token)
        _plain_stat(folder)
        file = folder / "source.md"
        if _plain_stat(file).st_size > maximum * 4:
            raise ValueError("所选审计原稿超过准备上限")
        text = file.read_bytes().decode("utf-8")
        name = "已选择的导入源文 " + token
    else:
        text, name = payload.get("source_text"), payload.get("source_name", "用户选择的原稿")
    if not isinstance(text, str) or not text.strip() or len(text) > maximum:
        raise ValueError("原稿为空或超过准备上限；未截断原稿")
    if not isinstance(name, str) or len(name) > 160:
        raise ValueError("原稿名称无效")
    return text, name


def list_sources(store):
    sources = []
    deadline = time.monotonic() + 1.0
    incomplete = False
    for base, pattern in ((root(), "exp_*"), (paths.PERSONA_DIR / "imports", "imp_*")):
        for folder in base.glob(pattern):
            if len(sources) >= 100 or time.monotonic() > deadline:
                incomplete = True
                break
            try:
                _plain_stat(folder)
                file = folder / "source.md"
                info = _plain_stat(file)
                if info.st_size > 4 * 1024 * 1024:
                    incomplete = True
                    continue
                sources.append({"op_id": folder.name, "source_name": folder.name,
                                "source_hash": digest(file.read_bytes().decode("utf-8"))})
            except (OSError, ValueError):
                continue
    return {"sources": sources, "sources_incomplete": incomplete,
            "operations": public_status(store)["items"]}


LEGACY_POLICIES = {
    "preserve": "保留当前：保护本节全文；新源必需项未合入时保持缺口，不视为删除授权。",
    "replace": "采用原稿：用户明确选择以本次原稿的完整新条目替换当前本节，仍须重新双审。",
    "merge": "保留两者：保留原稿新条目，并将当前本节全文追加为 GUI 私有配置；不猜测去重，冲突仍须核对。",
}


def legacy_user_baseline(store, before, choices=None):
    """Missing provenance never proves the absence of user edits.

    Default to preserving every current v1 section, including empty sections.
    Explicit per-section choices are frozen with this baseline for review;
    no legacy log or source mapping is required or fabricated.
    """
    from .persona_growth import bodies, EXPRESSION
    sections = bodies(before)
    if choices is None:
        choices = {}
    if not isinstance(choices, dict) or set(choices) - set(EXPRESSION):
        raise ValueError("旧格式迁移选择只能包含三个表达小节")
    if any(not isinstance(value, str) or value not in LEGACY_POLICIES for value in choices.values()):
        raise ValueError("旧格式迁移选择无效；未改变当前人格")
    choices = {title: choices.get(title, "preserve") for title in EXPRESSION}
    return {"legacy_overrides": {title: sections.get(title, "") for title in EXPRESSION},
            "legacy_mode": "conservative_preserve", "legacy_choices": choices}


def _preflight_user_expression(old, cfg):
    """Reject immutable user text that cannot fit before paying for generation.

    This only checks material the real merge must preserve. It does not build a
    candidate, certify coverage, or estimate the final projection's capacity.
    """
    from . import expression as expr
    cap = expr.limits_for(cfg)

    def check(group, item, label):
        try:
            for key in (*expr._FIELDS[group], "scope"):
                expr._string(item.get(key), key, cap["max_item_chars"],
                             empty=key in {"exceptions", "user_input"})
            fields = {key: item[key] for key in expr._FIELDS[group]}
            if len(json.dumps(fields, ensure_ascii=False)) > cap["max_item_chars"]:
                raise ValueError("完整表达单元超限：" + item["id"])
        except ValueError as exc:
            raise ValueError("保留的用户表达不能按当前容量合入（" + label + "）：" + str(exc)
                             + "；尚未调用模型，请先调整该表达或资源上限") from exc

    legacy = old.get("legacy_overrides")
    if legacy is not None:
        choices = old.get("legacy_choices", {})
        for title, body in legacy.items():
            body = expr._string(body, title, cap["max_projection_chars"], empty=True)
            choice = choices.get(title, "preserve")
            if not body or choice == "replace":
                continue
            group, item = expr._free_text_item(title, body)
            if choice == "merge":
                item["scope"] = "用户明确合并保留的旧格式表达（以正文条件为准）"
            elif old.get("legacy_mode") == "conservative_preserve":
                item["scope"] = "旧格式当前表达的保守保护；并非用户声明删除新源条目"
            check(group, item, title)
        return
    if old.get("user_overrides") and not old.get("user_revisions"):
        raise ValueError("旧修订只保存了整节正文，无法证明逐项修改或删除；请明确选择整节保留模式后再更新原稿")
    state = old.get("user_revisions")
    if not state or (not state["edits"] and not state["sections"]):
        return
    expr._validate_revisions(state, cap)
    if any(record["mode"] == "legacy_unknown" for record in state["sections"].values()):
        raise ValueError("旧整节修订粒度未知；请明确选择整节保留模式，不能冒充逐项合并")
    protected_groups = {group for title in state["sections"]
                        for group in expr.SECTION_GROUPS[expr.TITLES.index(title)]}
    for edit in state["edits"]:
        if edit["item"] and edit["group"] not in protected_groups:
            check(edit["group"], edit["item"], edit["item"]["id"])
    for title, record in state["sections"].items():
        for pair in record["items"]:
            check(pair["group"], pair["item"], title)


def validate_budget(candidate, cfg):
    """Admission lower bound; real rounds additionally account for history/tools."""
    from .persona_context import parse_snapshot, validate_expression, render_full
    from .context import messages_tokens
    from .retrieval import build_system_prompt
    snapshot = parse_snapshot(candidate)
    validate_expression(snapshot, cfg=cfg)
    # The exact static request includes constitution/persona; optional memory
    # and current user input are checked by the existing per-request guard.
    rendered = render_full(snapshot, cfg=cfg)
    static = build_system_prompt(None, cfg, rendered, memory_area="")
    total = cfg.context.total_budget_tokens
    if total and messages_tokens([{"role": "system", "content": static}]) + cfg.context.reply_reserve_tokens >= total:
        raise ValueError("完整人格与必要宿主规则已超出总 token 预算；候选未激活")


def package_metadata(package, registry=None):
    """Body-free observability; raw scopes/reasons stay in the private GUI audit."""
    from .expression import GROUPS, SCENARIOS
    from .persona_context import expression_coverage_metadata
    return {"format": package["format"], "source_hash": package["source_hash"],
            "projection_strategy": "complete_per_purpose", "source_count": len(package["source_ids"]),
            "type_counts": {g: len(package[g]) for g in GROUPS},
            "replacement_count": package.get("replacement_count", 0),
            "review_status": package.get("reviews", {}),
            "gap_count": len(package.get("gaps", [])),
            **expression_coverage_metadata(package),
            "source_units": [{k: u[k] for k in ("source_id", "line_start", "line_end", "kind")}
                             for u in (registry or {}).get("units", [])],
            "destinations": [{k: d[k] for k in ("source_id", "status", "targets", "required")}
                             for d in package["destinations"]],
            "scopes": [{"id": e["id"], "scope_sha256": digest(e["scope"]), "scope_chars": len(e["scope"]),
                        "required": e["required"], "purposes": e["purposes"]} for g in GROUPS for e in package[g]],
            "coverage": [{"scenario": c["scenario"] if c["scenario"] in SCENARIOS else "custom_" + digest(c["scenario"])[:16],
                          "status": c["status"], "example_ids": c["example_ids"],
                          **({"rule_ids": c["rule_ids"]} if "rule_ids" in c else {})} for c in package["coverage"]]}


def prepare(store, client, cfg, payload):
    from . import expression as expr
    from .expression_prepare import prepare_expression
    from .persona_growth import bodies, replace_bodies, EXPRESSION
    from .persona_context import normalize_section_headings
    recover(store)
    _binding(store, payload.get("revision"), payload.get("session_epoch"))
    before = current_text()
    if not before:
        raise ValueError("请先安装人格，再从原稿更新表达")
    doc, name = _source(payload, cfg)
    if "source_hash" in payload and payload["source_hash"] != digest(doc):
        raise ValueError("所选原稿版本已变化，请重新查看原稿并选择迁移范围")
    raw_sections = bodies(before)
    old = expr.decode_sections(raw_sections)
    baseline_sections = expr.editor_sections(old) if old else {title: raw_sections.get(title, "") for title in EXPRESSION}
    if old is not None and "legacy_choices" in payload:
        raise ValueError("当前已是新版表达；旧格式迁移选择不能覆盖现有修订，请刷新人格面板")
    old = old or legacy_user_baseline(store, before, payload.get("legacy_choices"))
    legacy_choices = old.get("legacy_choices")
    baseline_policy = ("旧格式默认保护当前三节全文（包括空节）；逐节采用本次明确选择。选择绑定当前人格及原稿，"
                       "保留两者不自动消除矛盾，事实、失真或硬约束未满足仍阻止应用。"
                       if old.get("legacy_mode") == "conservative_preserve" else
                       "按已记录的条目修改、明确删除和新增合入；整节自由文本范围单独保留，身份不明确时停止激活。")
    baseline_policy += ("合入用户保留内容后完整进行事实与保真初审；如有修复，按原问题验收及实际改动影响回归，"
                        "未受影响且初审通过的结论沿用，未解决的事实或失真否决仍阻止应用；"
                        "原稿支持范围内的普通遗漏保留提示，可安装部分表达，不为原稿未提供的内容制造缺口。")
    token = "exp_" + uuid.uuid4().hex
    folder = location(token)
    folder.mkdir(parents=True, exist_ok=False)
    atomic_text(folder / "source.md", doc)
    plan = {"kind": KIND, "token": token, "epoch": payload["session_epoch"],
            "revision": payload["revision"], "before_version": store.meta_get("persona_version"),
            "source_hash": digest(doc), "source_name": name, "status": "preparing",
            "created_at": time.time(), "limits": expr.limits_for(cfg),
            "preparation_policy": PREPARATION_POLICY, "baseline_policy": baseline_policy,
            "baseline_sections": baseline_sections, "legacy_choices": legacy_choices}
    atomic_text(folder / "previous.md", before)
    write_record(folder / "plan.json", plan)
    try:
        _preflight_user_expression(old, cfg)
        # Existing explicit revisions are part of the review input and must
        # survive the exact frozen candidate, not be reinserted after review.
        result = prepare_expression(client, cfg, doc, store=store, op_id=token,
                                    audit_dir=folder, user_sections=old)
        plan["review_policy"] = result.get("review_policy", "initial_plus_delta_v1")
        write_record(folder / "result.json", result)
        package = result.get("package")
        if package:
            from .persona_context import expression_coverage_view
            # Bind displayed coverage to the same package embedded in candidate.md.
            plan.update(expression_coverage_view(package))
        candidate = replace_bodies(normalize_section_headings(before), expr.encode_sections(package), allowed=EXPRESSION) if package else None
        if package:
            plan["current_diff"] = [{"section": title, "before": plan["baseline_sections"][title],
                                      "after": expr.editor_sections(package)[title],
                                      "policy": LEGACY_POLICIES[legacy_choices[title]] if legacy_choices else baseline_policy}
                                     for title in EXPRESSION]
        if result.get("ready"):
            validate_budget(candidate, cfg)
            _binding(store, plan["revision"], plan["epoch"])
        projection_chars = None
        if package:
            try:
                projection_chars = len(expr.project(package, "chat", cfg=cfg))
            except ValueError:
                if result.get("ready"):
                    raise
        plan.update({"status": "candidate_inactive" if result.get("ready") else "uncovered",
                     "ready": bool(result.get("ready")), "gaps": result.get("gaps", []),
                     "reviews": result.get("reviews", {}), "candidate_hash": digest(candidate),
                     "projection_chars": projection_chars,
                     "user_revision_note": baseline_policy})
        if package:
            plan["metadata"] = {**package_metadata(package, result.get("registry")),
                                "review_policy": plan["review_policy"]}
        if candidate:
            atomic_text(folder / "candidate.md", candidate)
        atomic_text(folder / "previous.md", before)
        write_record(folder / "result.json", result)
    except Exception as exc:
        # No provider content or raw response in the GUI error. Audits keep
        # successful steps; retry always requires a new explicit preparation.
        plan.update(status="prepare_failed", ready=False,
                    gaps=[str(exc) if isinstance(exc, ValueError) else
                          "准备未完成：请核对原稿结构、预算和本次模型调用记录；当前人格未改变。"])
        write_record(folder / "plan.json", plan)
        return view_plan(folder, plan)
    finally:
        try:
            from .metrics import flush_calls
            flush_calls(store)
        except Exception:
            pass
    write_record(folder / "plan.json", plan)
    return view_plan(folder, plan)


def view_plan(folder, plan, *, store=None):
    if (plan.get("status") == "candidate_inactive"
            and plan.get("preparation_policy") != PREPARATION_POLICY and not (folder / "op.json").exists()):
        plan = {**plan, "status": "prepare_failed", "ready": False,
                "gaps": ["此候选使用旧迁移保护或核对策略，请重新准备；当前人格未改变。"]}
    if plan.get("status") == "preparing":
        plan = {**plan, "status": "prepare_failed", "ready": False,
                "gaps": ["上次准备未产生完整候选；当前人格未改变，可查看已有审计后重新准备。"]}
    if (folder / "op.json").exists():
        operation = read_record(folder / "op.json")
        incomplete = False
        if store is not None:
            relations, incomplete = _read_relations(store, deadline=time.monotonic() + 1.0)
            operation = _project_operation(operation, relations)
        if incomplete and operation.get("phase") == "completed":
            plan = {**plan, "status": "state_unconfirmed", "ready": False}
        elif operation.get("phase") == "completed":
            plan = {**plan, "status": "applied", "revision": operation["after_sha256"]}
        elif operation.get("phase") == "rolled_back":
            plan = {**plan, "status": "rolled_back", "rollback_token": operation.get("rollback_token")}
        elif operation.get("phase") == "aborted":
            plan = {**plan, "status": "prepare_failed", "ready": False,
                    "gaps": ["此次应用在文件替换前中止并已恢复元数据；候选不能重放，请重新准备。"]}
        plan = {**plan, "rollback_of": operation.get("metadata", {}).get("rollback_of")}
    result = read_record(folder / "result.json") if (folder / "result.json").exists() else {}
    if not result and (folder / "expression_source.json").exists():
        result = {"registry": read_record(folder / "expression_source.json")}
    registry = result.get("registry", {})
    units = registry.get("units", []) if isinstance(registry, dict) else registry
    package = result.get("package") or {}
    dispositions = {item.get("source_id"): item for item in package.get("destinations", [])}
    from .expression import GROUPS, entry_text
    by_id = {item["id"]: entry_text(item, group) for group in GROUPS for item in package.get(group, [])}
    diff = [{"source_id": item.get("source_id"), "original": item.get("text", item.get("raw", "")),
             "adapted": "\n\n".join(by_id[target] for target in dispositions.get(item.get("source_id"), {}).get("targets", []) if target in by_id),
             "disposition": dispositions.get(item.get("source_id"), {}).get("status", "未决"),
             "reason": dispositions.get(item.get("source_id"), {}).get("reason", "")}
            for item in units]
    return {**{k: plan.get(k, result.get(k)) for k in ("token", "status", "ready", "revision", "source_hash",
                "gaps", "reviews", "review_policy", "coverage_policy", "coverage_report", "warnings", "review_notices",
                "projection_chars", "user_revision_note", "baseline_policy", "current_diff",
                "rollback_token", "rollback_of", "legacy_choices")},
            "session_epoch": plan["epoch"], "diff": diff}


def detail(store, token):
    folder = location(token)
    file = folder / "plan.json"
    plan = read_record(file if file.exists() else folder / "op.json")
    if plan.get("epoch") != store.meta_get("session_epoch"):
        raise ValueError("表达候选不属于当前会话")
    return view_plan(folder, plan, store=store)


def apply(store, cfg, payload):
    from . import expression as expr
    from .persona_context import parse_snapshot, validate_expression
    recover(store)
    folder = location(payload.get("token"))
    plan = read_record(folder / "plan.json")
    if plan.get("epoch") != payload.get("session_epoch") or plan.get("epoch") != store.meta_get("session_epoch"):
        raise ValueError("候选代际已变化")
    if (folder / "op.json").exists():
        rec = read_record(folder / "op.json")
        if (rec.get("phase") == "completed" and digest(current_text()) == rec["after_sha256"]
                and str(rec["after_version"]) == store.meta_get("persona_version")):
            return {"status": "applied", "token": payload["token"], "revision": rec["after_sha256"],
                    "session_epoch": plan["epoch"]}
        raise ValueError("候选操作已结束或人格后来发生变化，不能重放；请查看更新记录")
    _binding(store, payload.get("revision"), payload.get("session_epoch"))
    if plan.get("preparation_policy") != PREPARATION_POLICY:
        raise ValueError("此候选使用旧迁移保护或核对策略，请重新准备；当前人格未改变")
    if (plan.get("status") != "candidate_inactive" or not plan.get("ready")
            or plan["revision"] != payload["revision"]
            or plan["before_version"] != store.meta_get("persona_version")
            or plan["limits"] != expr.limits_for(cfg)):
        raise ValueError("候选未就绪、已取消或预算/版本已变化，请重新准备")
    if digest((folder / "source.md").read_bytes().decode("utf-8")) != plan["source_hash"]:
        raise ValueError("原稿版本已变化")
    candidate = (folder / "candidate.md").read_text(encoding="utf-8")
    if digest(candidate) != plan["candidate_hash"]:
        raise ValueError("候选正文与审议版本不一致")
    snapshot = parse_snapshot(candidate)
    expr.validate_package(snapshot["expression_package"], activation=True)
    validate_budget(candidate, cfg)
    commit(store, current_text(), candidate, token=payload["token"], metadata=plan.get("metadata", {}))
    plan["status"] = "applied"
    write_record(folder / "plan.json", plan)
    return {"status": "applied", "token": payload["token"], "revision": digest(candidate), "session_epoch": plan["epoch"]}


def cancel(store, payload):
    folder = location(payload.get("token"))
    plan = read_record(folder / "plan.json")
    if plan.get("epoch") != payload.get("session_epoch") or plan.get("epoch") != store.meta_get("session_epoch"):
        raise ValueError("候选代际已变化")
    if (folder / "op.json").exists() or plan.get("status") == "applied":
        raise ValueError("候选已经应用；需要使用当前版本回退")
    plan.update(status="cancelled", ready=False)
    write_record(folder / "plan.json", plan)
    return {"status": "cancelled", "token": payload["token"]}


# ---- 驻留分层准备/应用（人格注册表设计 §3.1：沿用本模块的准备/查看/应用与事务原语） ----

RESIDENCY_STAGED = "residency-staged.json"
RESIDENCY_TOKEN = re.compile(r"res_[0-9a-f]{32}\Z")


def _expression_limits(cfg) -> dict:
    return {"max_core_chars": cfg.expression.max_core_chars,
            "max_registry_chars": cfg.expression.max_registry_chars,
            "max_registry_bundles": cfg.expression.max_registry_bundles,
            "max_scene_expanded_chars": cfg.expression.max_scene_expanded_chars}


def _residency_staged_path() -> Path:
    return root() / RESIDENCY_STAGED


def _residency_preview(contract, package, limits) -> dict:
    """§3.1 准备结果：各条目归属、核心/目录大小、场景闭包与缺口。"""
    from . import expression
    from . import persona_residency as residency
    by_id = {}
    for group in expression.GROUPS:
        for item in package[group]:
            by_id[item["id"]] = {"group": group, "item": item}
    where = {entry_id: [] for entry_id in by_id}
    for entry_id in contract["core_ids"]:
        where[entry_id].append("core")
    bundles = []
    catalogue_chars = 0
    for bundle in contract["bundles"]:
        parts = residency.bundle_parts(contract, package, bundle)
        closure = parts["closure"] if parts else set(bundle["entry_ids"])
        for entry_id in closure:
            where.setdefault(entry_id, []).append("bundle:" + bundle["id"])
        text = residency._ordered_text(package, closure) if parts else ""
        catalogue_chars += len(bundle["label"]) + len(bundle["when"]) + 24
        bundles.append({"id": bundle["id"], "label": bundle["label"], "when": bundle["when"],
                        "purposes": list(bundle["purposes"]), "chars": len(text),
                        "closure_ids": sorted(closure)})
    core_text = residency.core_text(contract, package)
    return {"per_entry": [{"id": entry_id, "group": info["group"], "where": where[entry_id]}
                          for entry_id, info in sorted(by_id.items())],
            "core_chars": len(core_text), "catalogue_chars": catalogue_chars,
            "bundles": bundles, "core_entry_ids": sorted(set(contract["core_ids"]))}


def residency_status(store, cfg) -> dict:
    """当前分层状态：模式、assembly 有无与有效性、预览、暂存候选。只读。"""
    from .persona_context import load_persona_snapshot
    from . import persona_residency as residency
    result = {"mode": cfg.persona.expression_mode, "has_v2": False, "has_assembly": False,
              "issues": [], "preview": None, "registry_ready": False, "staged": None}
    snapshot = load_persona_snapshot()
    if snapshot is None or snapshot.get("expression_package") is None:
        result["reason"] = "no_v2_package"
        return result
    result["has_v2"] = True
    result["expression_digest"] = residency.expression_digest(snapshot["expression_package"])
    package = snapshot["expression_package"]
    assembly = snapshot.get("assembly")
    if assembly:
        result["has_assembly"] = True
        contract = residency.parse_contract(assembly)
        if contract is None:
            result["issues"] = [{"code": "invalid_shape"}]
        else:
            result["issues"] = residency.validate_contract(contract, package, limits=_expression_limits(cfg))
            result["preview"] = _residency_preview(contract, package, _expression_limits(cfg))
    result["registry_ready"] = bool(assembly) and not result["issues"]
    staged_path = _residency_staged_path()
    try:
        staged = read_record(staged_path)
        result["staged"] = {"token": staged.get("token"),
                            "matches_current": staged.get("expression_digest") == result.get("expression_digest"),
                            "created_at": staged.get("created_at")}
    except (OSError, ValueError, json.JSONDecodeError):
        result["staged"] = None
    return result


def residency_prepare(store, client, cfg) -> dict:
    """模型辅助分类当前表达包 → 校验通过才暂存；原表达未改时不重新收养认知。"""
    from .persona_context import load_persona_snapshot
    from . import persona_residency as residency
    snapshot = load_persona_snapshot()
    if snapshot is None or snapshot.get("expression_package") is None:
        raise ValueError("当前人格没有 expression-v2 表达包，无法分层")
    package = snapshot["expression_package"]
    contract, issues = residency.prepare_classification(package, client, cfg)
    if contract is None:
        return {"staged": False, "issues": issues,
                "notice": "分类候选未通过结构校验；可重新准备，原表达保持不变。"}
    preview = _residency_preview(contract, package, _expression_limits(cfg))
    record = {"token": "res_" + uuid.uuid4().hex,
              "expression_digest": contract["expression_digest"],
              "persona_revision": digest(current_text()),
              "classification_revision": contract["classification_revision"],
              "assembly": contract, "created_at": time.time()}
    write_record(root() / RESIDENCY_STAGED, record)
    return {"staged": True, "token": record["token"], "preview": preview,
            "notice": "已暂存候选；查看归属与大小后可应用或取消。应用会用当前人格与摘要复核。"}


def residency_apply(store, cfg, payload) -> dict:
    """用当前表达版本与分类版本核对后，一次保存回同一权威 Markdown。"""
    from . import expression
    from .persona_context import load_persona_snapshot
    from . import persona_residency as residency
    from .persona_growth import bodies as split_bodies, replace_bodies, EXPRESSION
    staged_path = _residency_staged_path()
    record = read_record(staged_path)
    if not RESIDENCY_TOKEN.fullmatch(str(record.get("token") or "")):
        raise ValueError("暂存候选身份无效")
    current = current_text()
    if record.get("persona_revision") and digest(current) != record["persona_revision"]:
        raise ValueError("人格在准备后已变化；请重新准备驻留分层")
    package, _ = expression.decode_sections_with_assembly(split_bodies(current))
    if package is None:
        raise ValueError("当前人格没有 expression-v2 表达包")
    if residency.expression_digest(package) != record.get("expression_digest"):
        raise ValueError("表达包在准备后已变化；请重新准备驻留分层")
    contract = residency.parse_contract(record.get("assembly"))
    if contract is None:
        raise ValueError("暂存分类结构无效；请重新准备")
    issues = residency.validate_contract(contract, package, limits=_expression_limits(cfg))
    if issues:
        raise ValueError("驻留分类与当前表达不匹配：" + "；".join(i["code"] for i in issues))
    candidate = replace_bodies(current, expression.encode_sections(package, assembly=contract),
                               allowed=EXPRESSION)
    validate_budget(candidate, cfg)
    token = commit(store, current, candidate, mode="residency_layering",
                   metadata={"expression_digest": record["expression_digest"],
                             "classification_revision": contract["classification_revision"]})
    staged_path.unlink(missing_ok=True)
    return {"applied": True, "token": token,
            "preview": _residency_preview(contract, package, _expression_limits(cfg)),
            "notice": "分层已保存进人格；registry 模式下次请求生效（expression_mode=registry 时）。"}


def residency_cancel() -> dict:
    staged_path = _residency_staged_path()
    existed = staged_path.exists()
    staged_path.unlink(missing_ok=True)
    return {"cancelled": bool(existed)}
