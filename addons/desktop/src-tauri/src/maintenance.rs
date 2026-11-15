//! Trusted bundle-only maintenance UI. Loopback chat pages cannot invoke it.
use super::{start, Backend};
use serde_json::{json, Value};
use std::{io::Write, process::Stdio, sync::{atomic::Ordering, mpsc, Arc}, time::Duration};
use tauri::{Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub fn open(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("maintenance") {
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let _ = WebviewWindowBuilder::new(app, "maintenance", WebviewUrl::App("maintenance.html".into()))
        .title("Alice — 备份与恢复")
        .inner_size(640.0, 650.0).min_inner_size(540.0, 480.0)
        .on_navigation(|url| url.path() == "/maintenance.html" &&
            ((url.scheme() == "tauri" && url.host_str() == Some("localhost")) ||
            (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))))
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
        .build();
}

pub fn notify(app: &tauri::AppHandle, frame: &Value) {
    if let Some(window) = app.get_webview_window("maintenance") {
        let _ = window.eval(&format!("window.updateMaintenanceState && window.updateMaintenanceState({})", frame));
    }
}

fn alive(state: &Backend) -> Result<bool, String> {
    let mut child = state.child.lock().unwrap();
    match child.as_mut() {
        Some(child) => child.try_wait().map(|status| status.is_none()).map_err(|_| "无法确认后端是否退出".into()),
        None => Ok(false),
    }
}

fn request(state: &Backend, operation: &str, reason: Option<&str>) -> Result<Value, String> {
    let identity = state.identity.lock().unwrap().clone().ok_or("后端尚未就绪，请稍候")?;
    let request_id = uuid::Uuid::new_v4().simple().to_string();
    let mut frame = json!({"protocol":1,"instance_id":identity.0,"nonce":identity.1,"request_id":request_id,"op":operation});
    if let Some(reason) = reason { frame["reason"] = json!(reason); }
    let (sender, receiver) = mpsc::channel();
    state.replies.lock().unwrap().insert(request_id.clone(), sender);
    let result = (|| {
        {
            let mut input = state.input.lock().unwrap();
            let input = input.as_mut().ok_or("控制管道已关闭")?;
            writeln!(input, "{}", frame).and_then(|_| input.flush()).map_err(|_| "控制请求未能送达")?;
        }
        receiver.recv_timeout(Duration::from_secs(30)).map_err(|_| "控制请求尚未确认，请等待当前收尾完成".to_string())
    })();
    state.replies.lock().unwrap().remove(&request_id);
    result
}

fn helper(app: &tauri::AppHandle, action: &str, backup: Option<&str>, nonce: Option<&str>) -> Result<Value, String> {
    let root = app.path().resolve("Alice", tauri::path::BaseDirectory::LocalData).map_err(|_| "数据目录不可用")?;
    let mut command = super::backend_command(app).map_err(|_| "包内维护程序不可用")?;
    command.arg("--user-dir").arg(&root).arg("maintenance").arg(action);
    if let Some(id) = backup { command.arg("--backup-id").arg(id); }
    if let Some(nonce) = nonce { command.arg("--launch-nonce").arg(nonce); }
    if matches!(action, "prepare" | "restore" | "rollback" | "authorize") { command.arg("--confirm"); }
    let log = std::fs::OpenOptions::new().create(true).append(true).open(root.join("logs/desktop.log"))
        .map_err(|_| "无法打开本机维护日志")?;
    // This fixed CLI only returns bounded metadata, never profile contents.
    let mut child = command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::from(log))
        .spawn().map_err(|_| "维护程序未能启动")?;
    use std::io::Read;
    let mut bytes = Vec::new();
    child.stdout.take().ok_or("维护输出不可用")?.take(65537).read_to_end(&mut bytes)
        .map_err(|_| "无法读取维护结果")?;
    let result = child.wait().map_err(|_| "无法确认维护进程是否结束")?;
    if bytes.len() > 65536 { return Err("维护响应超出限制，请检查日志".into()); }
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| "维护结果无法核对，请检查日志")?;
    if !result.success() { return Err(value["error"].as_str().unwrap_or("维护未完成，请检查日志").to_string()); }
    Ok(value)
}

struct ActionGuard(Arc<Backend>);
impl Drop for ActionGuard {
    fn drop(&mut self) { self.0.maintenance_busy.store(false, Ordering::SeqCst); }
}

#[tauri::command]
pub async fn maintenance_action(window: WebviewWindow, app: tauri::AppHandle,
    state: tauri::State<'_, Arc<Backend>>, action: String, backup_id: Option<String>, confirmed: bool) -> Result<Value, String> {
    if window.label() != "maintenance" { return Err("此入口仅供应用内备份与恢复窗口使用".into()); }
    if !matches!(action.as_str(), "status" | "verify" | "prepare" | "pause" | "restore" | "rollback" | "resume") {
        return Err("不支持的维护操作".into());
    }
    if matches!(action.as_str(), "verify" | "restore") && !backup_id.as_ref().is_some_and(|id|
        id.len() == 32 && id.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))) {
        return Err("请选择有效恢复点".into());
    }
    if !matches!(action.as_str(), "status" | "verify") && !confirmed { return Err("此操作需要明确确认".into()); }
    let state = state.inner().clone();
    if state.maintenance_busy.swap(true, Ordering::SeqCst) { return Err("维护操作正在进行，请稍候".into()); }
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = ActionGuard(state.clone());
        if state.maintenance_pending.load(Ordering::SeqCst) {
            if action == "status" { return Ok(json!({"busy":true,"phase":"quiescing","backups":[]})); }
            return Err("正在等待服务收尾，请不要重复操作".into());
        }
        let running = alive(&state)?;
        if action == "status" && running {
            if state.suspended.load(Ordering::SeqCst) { return Err("正在进入恢复模式，请稍候".into()); }
            let result = request(&state, "maintenance_status", None)?;
            let mut value = result.get("maintenance").cloned().ok_or("无法读取恢复点列表")?;
            value["running"] = json!(true);
            return Ok(value);
        }
        if running {
            if !matches!(action.as_str(), "prepare" | "pause") { return Err("请先暂停 Alice，进入恢复模式后再操作".into()); }
            state.maintenance_pending.store(true, Ordering::SeqCst);
            let was_suspended = state.suspended.swap(action == "pause", Ordering::SeqCst);
            let reason = if action == "prepare" { "update" } else { "recovery" };
            match request(&state, "shutdown", Some(reason)) {
                Ok(value) if value["status"] == "quiescing" => return Ok(json!({"busy":true,"phase":"quiescing"})),
                _ => {
                    state.suspended.store(was_suspended, Ordering::SeqCst);
                    state.maintenance_pending.store(false, Ordering::SeqCst);
                    return Err("未确认停止请求，请检查本机日志".into());
                }
            }
        }
        if !state.reader_done.load(Ordering::SeqCst) { return Err("正在等待后端进程退出，请稍候".into()); }
        match action.as_str() {
            "status" => { let mut value = helper(&app, "status", None, None)?; value["running"] = json!(false); Ok(value) }
            "verify" => helper(&app, "verify", backup_id.as_deref(), None),
            "restore" => helper(&app, "restore", backup_id.as_deref(), None),
            "rollback" => helper(&app, "rollback", None, None),
            "pause" => Ok(json!({"status":"stopped"})),
            "prepare" => {
                let value = helper(&app, "prepare", None, None)?;
                state.allow_exit.store(true, Ordering::SeqCst);
                app.exit(0);
                Ok(value)
            }
            "resume" => {
                let nonce = uuid::Uuid::new_v4().simple().to_string();
                helper(&app, "authorize", None, Some(&nonce))?;
                state.launch_done.store(false, Ordering::SeqCst);
                state.suspended.store(false, Ordering::SeqCst);
                state.closing.store(false, Ordering::SeqCst);
                *state.origin.lock().unwrap() = None;
                *state.identity.lock().unwrap() = None;
                let result = start(app.clone(), state.clone(), Some(nonce)).map_err(|_| "Alice 未能重新启动，请保留恢复点".to_string());
                state.launch_done.store(true, Ordering::SeqCst);
                result?;
                Ok(json!({"status":"starting"}))
            }
            _ => Err("维护操作不可用".into()),
        }
    }).await.map_err(|_| "维护操作中断，请检查本机日志".to_string())?
}
