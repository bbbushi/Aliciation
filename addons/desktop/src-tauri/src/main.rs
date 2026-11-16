#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
mod maintenance;
mod updates;
use std::{
    collections::HashMap,
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

struct Backend {
    child: Mutex<Option<Child>>,
    input: Mutex<Option<ChildStdin>>,
    identity: Mutex<Option<(String, String)>>,
    origin: Mutex<Option<String>>,
    closing: AtomicBool,
    allow_exit: AtomicBool,
    launch_done: AtomicBool,
    reader_done: AtomicBool,
    suspended: AtomicBool,
    maintenance_pending: AtomicBool,
    maintenance_busy: AtomicBool,
    update_pending: AtomicBool,
    backup_completed: AtomicBool,
    replies: Mutex<HashMap<String, mpsc::Sender<Value>>>,
}

impl Backend {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
            input: Mutex::new(None),
            identity: Mutex::new(None),
            origin: Mutex::new(None),
            closing: AtomicBool::new(false),
            allow_exit: AtomicBool::new(false),
            launch_done: AtomicBool::new(false),
            reader_done: AtomicBool::new(true),
            suspended: AtomicBool::new(false),
            maintenance_pending: AtomicBool::new(false),
            maintenance_busy: AtomicBool::new(false),
            update_pending: AtomicBool::new(false),
            backup_completed: AtomicBool::new(false),
            replies: Mutex::new(HashMap::new()),
        }
    }

    fn quit(&self, app: &tauri::AppHandle) {
        if self.maintenance_pending.load(Ordering::SeqCst) || self.maintenance_busy.load(Ordering::SeqCst) {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_title("Alice — 正在处理本机数据，请等待完成后退出…");
            }
            return;
        }
        if let Some(child) = self.child.lock().unwrap().as_mut() {
            if child.try_wait().ok().flatten().is_some() {
                self.allow_exit.store(true, Ordering::SeqCst);
                app.exit(0);
                return;
            }
        } else if self.launch_done.load(Ordering::SeqCst) {
            self.allow_exit.store(true, Ordering::SeqCst);
            app.exit(0);
            return;
        }
        let identity = self.identity.lock().unwrap().clone();
        if identity.is_none() {
            // Before ready, EOF asks the backend to drain without model extraction.
            self.input.lock().unwrap().take();
        } else if !self.closing.swap(true, Ordering::SeqCst) {
            let (instance, nonce) = identity.unwrap();
            let frame = json!({"protocol":1,"instance_id":instance,"nonce":nonce,
                "request_id":uuid::Uuid::new_v4().simple().to_string(),"op":"shutdown","reason":"quit"});
            if let Some(input) = self.input.lock().unwrap().as_mut() {
                let _ = writeln!(input, "{}", frame);
                let _ = input.flush();
            }
        }
        self.closing.store(true, Ordering::SeqCst);
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.set_title("Alice — 正在保存并退出，请稍候…");
        }
    }
}

fn failure(app: &tauri::AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_title(message);
        // Fixed function + JSON string; never insert backend text as HTML or script.
        let _ = window.eval(&format!(
            "window.showState && window.showState({})",
            json!(message)
        ));
    }
}

fn backend_command(app: &tauri::AppHandle) -> Result<Command, Box<dyn std::error::Error>> {
    let mut command;
    if cfg!(debug_assertions) {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize()?;
        let python = if cfg!(windows) { repo.join(".venv/Scripts/python.exe") } else { repo.join(".venv/bin/python") };
        command = Command::new(python);
        command.current_dir(repo).args(["-m", "alice"]);
    } else {
        let suffix = if cfg!(windows) { ".exe" } else { "" };
        command = Command::new(app.path().resource_dir()?.join(format!("backend/alice-backend{suffix}")));
    }
    command.env("PYTHONIOENCODING", "utf-8");
    Ok(command)
}

fn start(app: tauri::AppHandle, state: Arc<Backend>, launch_nonce: Option<String>) -> Result<(), Box<dyn std::error::Error>> {
    let root = app
        .path()
        .resolve("Alice", tauri::path::BaseDirectory::LocalData)?;
    fs::create_dir_all(root.join("logs"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;
        fs::set_permissions(root.join("logs"), fs::Permissions::from_mode(0o700))?;
    }
    let log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(root.join("logs/desktop.log"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        log.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    let nonce = launch_nonce.unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
    let mut command = backend_command(&app)?;
    command
        .arg("--user-dir")
        .arg(&root)
        .arg("desktop-host")
        .env("ALICE_DESKTOP_NONCE", &nonce)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log));
    let mut child = command.spawn()?;
    let output = child.stdout.take().ok_or("missing backend pipe")?;
    *state.input.lock().unwrap() = child.stdin.take();
    *state.child.lock().unwrap() = Some(child);
    state.reader_done.store(false, Ordering::SeqCst);
    if state.closing.load(Ordering::SeqCst) {
        state.input.lock().unwrap().take();
        state.replies.lock().unwrap().clear();
    }
    thread::spawn(move || {
        let mut reader = BufReader::new(output);
        loop {
            // Bound memory before decoding, including a malicious/accidental long line.
            let mut raw = Vec::new();
            let count = reader.by_ref().take(16385).read_until(b'\n', &mut raw);
            if !matches!(count, Ok(n) if n > 0 && n <= 16384) || raw.last() != Some(&b'\n') {
                break;
            }
            let Ok(frame) = serde_json::from_slice::<Value>(&raw) else {
                break;
            };
            if frame["protocol"] != 1 || frame["nonce"] != nonce {
                break;
            }
            if let Some(id) = frame["request_id"].as_str() {
                if let Some(sender) = state.replies.lock().unwrap().remove(id) {
                    let _ = sender.send(frame.clone());
                }
            }
            match frame["event"].as_str() {
                Some("ready") => {
                    let Some(instance) = frame["instance_id"].as_str() else {
                        break;
                    };
                    let Some(url) = frame["url"]
                        .as_str()
                        .and_then(|s| tauri::Url::parse(s).ok())
                    else {
                        break;
                    };
                    if url.scheme() != "http"
                        || url.host_str() != Some("127.0.0.1")
                        || url.port().is_none()
                        || !url.username().is_empty()
                        || url.password().is_some()
                        || url.query().is_some()
                        || url.fragment().is_some()
                        || url.path() != "/"
                    {
                        break;
                    }
                    *state.identity.lock().unwrap() = Some((instance.to_owned(), nonce.clone()));
                    *state.origin.lock().unwrap() = Some(url.origin().ascii_serialization());
                    if !state.closing.load(Ordering::SeqCst) {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.navigate(url);
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    maintenance::notify(&app, &frame);
                }
                Some("maintenance_required") => {
                    state.suspended.store(true, Ordering::SeqCst);
                    failure(&app, "Alice 等待确认更新或恢复，请打开备份与恢复窗口");
                    maintenance::open(&app);
                }
                Some("failed") => {
                    state.suspended.store(true, Ordering::SeqCst);
                    failure(&app, "Alice 未能启动，可在备份与恢复窗口核对恢复点");
                    maintenance::open(&app);
                }
                Some("backup_started" | "backup_complete" | "backup_failed") => {
                    if frame["event"] == "backup_complete" { state.backup_completed.store(true,Ordering::SeqCst); }
                    if frame["event"] == "backup_failed" {
                        state.suspended.store(true, Ordering::SeqCst);
                        maintenance::open(&app);
                    }
                    maintenance::notify(&app, &frame);
                }
                Some("closed") => {
                    // Release the backend's reader before Python finalization.
                    // Keep reading output and wait for the actual process status.
                    state.input.lock().unwrap().take();
                    let clean = frame["clean"] == true;
                    if frame["reason"] == "recovery" {
                        state.suspended.store(true, Ordering::SeqCst);
                    }
                    state.closing.store(clean && !state.suspended.load(Ordering::SeqCst), Ordering::SeqCst);
                    if !clean {
                        failure(&app, "Alice 收尾有警告，请核对本机日志；可关闭窗口退出");
                    }
                }
                _ => {}
            }
        }
        // EOF is not itself proof of process exit. Never kill an unverified PID.
        state.input.lock().unwrap().take();
        let exit_status = loop {
            let result = state.child.lock().unwrap().as_mut().map(|p| p.try_wait());
            match result {
                Some(Ok(Some(status))) => break Some(status),
                Some(Ok(None)) => thread::sleep(std::time::Duration::from_millis(100)),
                _ => break None,
            }
        };
        if !state.update_pending.load(Ordering::SeqCst) { state.maintenance_pending.store(false, Ordering::SeqCst); }
        if state.update_pending.load(Ordering::SeqCst) {
            state.reader_done.store(true,Ordering::SeqCst);
            let clean=exit_status.is_some_and(|status| status.success()) && state.backup_completed.load(Ordering::SeqCst)
                && state.closing.load(Ordering::SeqCst) && !state.suspended.load(Ordering::SeqCst);
            updates::finish(app.clone(),state.clone(),clean);
            return;
        }
        if state.closing.load(Ordering::SeqCst) && exit_status.is_some_and(|status| status.success()) {
            state.allow_exit.store(true, Ordering::SeqCst);
            app.exit(0);
        } else if state.suspended.load(Ordering::SeqCst) {
            *state.origin.lock().unwrap() = None;
            if let Some(window) = app.get_webview_window("main") {
                let bootstrap = if cfg!(windows) { "http://tauri.localhost/index.html?maintenance=1" }
                    else { "tauri://localhost/index.html?maintenance=1" };
                if let Ok(url) = tauri::Url::parse(bootstrap) { let _ = window.navigate(url); }
            }
            maintenance::open(&app);
            maintenance::notify(&app, &json!({"event":"maintenance_stopped"}));
        } else {
            failure(&app, "Alice 后端未能正常退出，请关闭窗口并检查本机日志");
            maintenance::open(&app);
        }
        state.reader_done.store(true, Ordering::SeqCst);
    });
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let initial_nonce = args.windows(2).find(|pair| pair[0]=="--alice-update-nonce")
        .map(|pair|pair[1].clone()).filter(|value| value.len()==32 && value.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)));
    let state = Arc::new(Backend::new());
    let setup_state = state.clone();
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![maintenance::maintenance_action,updates::update_action])
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Arc::new(updates::Updates::default()))
        .menu(|app| {
            use tauri::menu::{Menu, MenuItem, Submenu};
            let menu = Menu::default(app)?;
            let item = MenuItem::with_id(app, "alice_maintenance", "备份与恢复…", true, None::<&str>)?;
            let check = MenuItem::with_id(app, "alice_updates", "检查更新…", true, None::<&str>)?;
            menu.append(&Submenu::with_items(app, "数据与更新", true, &[&check,&item])?)?;
            Ok(menu)
        })
        .on_menu_event(|app, event| {
            if event.id().as_ref() == "alice_maintenance" { maintenance::open(app); }
            if event.id().as_ref() == "alice_updates" { updates::open(app); }
        })
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(state)
        .setup(move |app| {
            let navigation = setup_state.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Alice")
                .inner_size(1100.0, 780.0)
                .min_inner_size(760.0, 560.0)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .on_navigation(move |url| {
                    let origin = navigation.origin.lock().unwrap();
                    match origin.as_ref() {
                        Some(expected) => url.origin().ascii_serialization() == *expected,
                        None => {
                            url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
                        }
                    }
                })
                .build()?;
            let handle = app.handle().clone();
            let backend = setup_state.clone();
            thread::spawn(move || {
                if start(handle.clone(), backend.clone(), initial_nonce).is_err() {
                    failure(&handle, "Alice 启动失败，请检查运行环境与日志后关闭窗口");
                }
                backend.launch_done.store(true, Ordering::SeqCst);
                if backend.closing.load(Ordering::SeqCst) {
                    backend.quit(&handle);
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" { return; }
            if let WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<Arc<Backend>>();
                if !state.allow_exit.load(Ordering::SeqCst) {
                    api.prevent_close();
                    state.quit(window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Alice desktop initialization failed");
    app.run(|app, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            let state = app.state::<Arc<Backend>>();
            if !state.allow_exit.load(Ordering::SeqCst) {
                api.prevent_exit();
                state.quit(app);
            }
        }
    });
}
