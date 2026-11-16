//! Scheduled checks and manually confirmed, signed macOS updates. Only the bundle-local update window may act.
use super::{maintenance, Backend};
use flate2::read::GzDecoder;
use reqwest::{redirect::Policy, Url};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, io::{Cursor, Read, Write}, path::{Component, Path},
    sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex}, time::{Duration, Instant}};
use tauri::{Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_updater::{Update, UpdaterExt};

const PUBLIC_KEY: &str = include_str!("../../packaging/updater.pub");
const MAX_DOWNLOAD: usize = 512 * 1024 * 1024;
const MAX_EXPANDED: u64 = 2 * 1024 * 1024 * 1024;

#[derive(Default)]
struct Session { phase: String, error: String, update: Option<Update>, bytes: Option<Arc<Vec<u8>>>, release: Option<Value>, token: Option<String> }
struct Preferences { enabled: bool, announced: Option<String> }
impl Default for Preferences {
    fn default() -> Self { Self { enabled:true, announced:None } }
}
#[derive(Default)]
pub struct Updates { session: Mutex<Session>, busy: AtomicBool, preferences: Mutex<Preferences> }

pub fn open(app: &tauri::AppHandle) { let _=open_window(app,true); }

fn open_window(app: &tauri::AppHandle, focus: bool) -> bool {
    if let Some(window) = app.get_webview_window("updates") {
        let shown=window.show().is_ok(); if focus { let _ = window.set_focus(); } return shown;
    }
    WebviewWindowBuilder::new(app, "updates", WebviewUrl::App("updates.html".into()))
        .title("Alice — 检查更新").focused(focus).inner_size(580.0, 570.0).min_inner_size(500.0, 440.0)
        .on_navigation(|url| url.path() == "/updates.html" &&
            ((url.scheme() == "tauri" && url.host_str() == Some("localhost")) ||
             (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))))
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny).build().is_ok()
}

fn notify(app: &tauri::AppHandle, value: &Value) {
    if let Some(window) = app.get_webview_window("updates") {
        let _ = window.eval(&format!("window.updateProgress && window.updateProgress({})", value));
    }
}

fn state(app: &tauri::AppHandle, updates: &Updates) -> Value {
    let session = updates.session.lock().unwrap();
    json!({"current":app.package_info().version.to_string(),"phase":session.phase,
        "error":session.error,"busy":updates.busy.load(Ordering::SeqCst),
        "version":session.update.as_ref().map(|u| &u.version),
        "notes":session.update.as_ref().and_then(|u| u.body.as_ref()).map(|s| s.chars().take(8000).collect::<String>()),
        "automatic":updates.preferences.lock().unwrap().enabled,"verified":session.bytes.is_some(),"has_token":session.token.is_some(),"source":"GitHub · bbbushi/Aliciation Releases"})
}

fn phase(app: &tauri::AppHandle, updates: &Updates, name: &str) {
    updates.session.lock().unwrap().phase = name.into();
    notify(app, &state(app, updates));
}

fn allowed_transport(url: &Url) -> bool {
    url.scheme() == "https" && url.username().is_empty() && url.password().is_none()
        && url.port_or_known_default() == Some(443) && url.host_str().is_some_and(|host|
            host == "api.github.com" || host == "github.com" || host.ends_with(".githubusercontent.com"))
}

fn github_headers(token: Option<&str>, accept: &'static str) -> Result<reqwest::header::HeaderMap,String> {
    use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION};
    let mut headers=HeaderMap::new();
    if let Some(token)=token {
        let mut authorization=HeaderValue::try_from(format!("Bearer {token}")).map_err(|_| "GitHub 令牌格式无效")?;
        authorization.set_sensitive(true);
        headers.insert(AUTHORIZATION,authorization);
    }
    headers.insert(ACCEPT,HeaderValue::from_static(accept));
    Ok(headers)
}

async fn release_asset(token: Option<&str>, tag: &str, name: &str, limit: u64) -> Result<Url,String> {
    let client=secure_client(reqwest::Client::builder()).user_agent("Alice-Updater")
        .timeout(Duration::from_secs(30)).build().map_err(|_| "GitHub 请求配置不可用")?;
    let url=format!("https://api.github.com/repos/bbbushi/Aliciation/releases/tags/{tag}");
    let mut response=client.get(url).headers(github_headers(token,"application/vnd.github+json")?).send().await
        .map_err(|_| "无法连接 GitHub 更新源")?;
    if matches!(response.status().as_u16(),401|403|429) {
        return Err(if token.is_some() { "GitHub 授权无效、已过期、权限不足或请求受限；公开源可清除授权后重试" }
            else { "GitHub 请求受限，请稍后重试，或使用可选的只读授权" }.into());
    }
    if response.status().as_u16()==404 { return Err("开发更新尚未发布，或令牌未获此仓库的读取权限".into()); }
    if !response.status().is_success() { return Err("GitHub 更新源暂不可用".into()); }
    let mut bytes=Vec::new();
    while let Some(chunk)=response.chunk().await.map_err(|_| "读取 GitHub 发布信息中断")? {
        if bytes.len()+chunk.len()>1024*1024 { return Err("GitHub 发布信息过大".into()); } bytes.extend_from_slice(&chunk);
    }
    let value:Value=serde_json::from_slice(&bytes).map_err(|_| "GitHub 发布信息无效")?;
    if value["draft"]==true || value["tag_name"]!=tag { return Err("更新发布尚未完成".into()); }
    let assets=value["assets"].as_array().ok_or("发布尚无更新附件")?;
    let asset=assets.iter().find(|asset|asset["name"]==name && asset["state"]=="uploaded").ok_or("更新附件尚未上传")?;
    let size=asset["size"].as_u64().ok_or("更新附件大小无效")?;
    if size==0 || size>limit { return Err("更新附件大小超出限制".into()); }
    let id=asset["id"].as_u64().ok_or("更新附件标识无效")?;
    Url::parse(&format!("https://api.github.com/repos/bbbushi/Aliciation/releases/assets/{id}")).map_err(|_| "更新附件地址无效".into())
}

fn secure_client(client: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
    client.redirect(Policy::custom(|attempt| {
        if attempt.previous().len() >= 8 || !allowed_transport(attempt.url()) {
            attempt.error("update redirect rejected")
        } else { attempt.follow() }
    }))
}

fn safe_archive_path(path: &Path) -> bool {
    let mut parts = path.components();
    matches!(parts.next(), Some(Component::Normal(name)) if name == "Alice.app")
        && parts.all(|part| matches!(part, Component::Normal(_)))
}

fn internal_link(path: &Path, target: &Path) -> bool {
    if target.is_absolute() { return false; }
    let mut depth = path.parent().map(|p| p.components().count()).unwrap_or(0);
    for part in target.components() {
        match part {
            Component::Normal(_) => depth += 1,
            Component::CurDir => (),
            Component::ParentDir if depth > 1 => depth -= 1,
            _ => return false,
        }
    }
    depth >= 1
}

fn inspect_package(bytes: &[u8], version: &str) -> Result<Value, String> {
    let mut archive = tar::Archive::new(GzDecoder::new(Cursor::new(bytes)));
    let mut expanded = 0u64;
    let mut release = None;
    let mut backend = None;
    let mut seen = std::collections::HashSet::new();
    for entry in archive.entries().map_err(|_| "更新包格式无效")? {
        let mut entry = entry.map_err(|_| "更新包目录无法读取")?;
        let path = entry.path().map_err(|_| "更新包路径无效")?.into_owned();
        if !safe_archive_path(&path) || !seen.insert(path.clone()) { return Err("更新包含越界或重复路径".into()); }
        expanded = expanded.checked_add(entry.size()).ok_or("更新包大小异常")?;
        if expanded > MAX_EXPANDED { return Err("更新包展开大小超出限制".into()); }
        let kind = entry.header().entry_type();
        if kind.is_symlink() {
            let link = entry.link_name().map_err(|_| "更新包链接无效")?.ok_or("更新包链接缺失")?;
            if !internal_link(&path, &link) { return Err("更新包链接越界".into()); }
        } else if !kind.is_file() && !kind.is_dir() { return Err("更新包含不支持的文件类型".into()); }
        let name = path.to_string_lossy();
        if name == "Alice.app/Contents/Resources/release.json" || name == "Alice.app/Contents/Resources/backend/_internal/build-info.json" {
            if !kind.is_file() || entry.size() > 2 * 1024 * 1024 { return Err("兼容声明格式无效".into()); }
            let mut text = String::new(); entry.read_to_string(&mut text).map_err(|_| "兼容声明无法读取")?;
            let info: Value = serde_json::from_str(&text).map_err(|_| "兼容声明无法解析")?;
            if name.ends_with("/release.json") { release = Some(info); } else { backend = Some(info); }
        }
    }
    let info = release.ok_or("更新包缺少签名范围内的兼容声明")?;
    let core = backend.ok_or("更新包缺少后端版本声明")?;
    if !info["build_id"].as_str().is_some_and(|id| !id.is_empty() && id.len()<=160)
        || info["version"] != version || core["version"] != version || info["build_id"] != core["build_id"]
        || info["target"] != "darwin-aarch64" || info["application_id"] != "app.aliceharness.desktop"
        || info["update_channel"] != "desktop-dev"
        || info["maintenance_protocol"] != 1 || info["data_format"]["write"] != 1
        || !info["data_format"]["read"].as_array().is_some_and(|v| v.contains(&json!(1)))
        || info["updater_pubkey_sha256"] != Sha256::digest(PUBLIC_KEY.trim().as_bytes()).iter().map(|b|format!("{b:02x}")).collect::<String>() {
        return Err("程序版本、架构、数据格式或更新公钥不兼容".into());
    }
    for key in ["target","application_id","maintenance_protocol","data_format","updater_pubkey_sha256","minimum_macos","update_channel"] {
        if core[key]!=info[key] { return Err("宿主与后端的兼容声明不一致".into()); }
    }
    if !info["data_format"]["migration_from"].as_array().is_some_and(|items|items.is_empty()) {
        return Err("此批更新器尚不接受数据格式迁移".into());
    }
    let os = std::process::Command::new("/usr/bin/sw_vers").arg("-productVersion").output().map_err(|_| "无法核对系统版本")?;
    let current = String::from_utf8(os.stdout).map_err(|_| "系统版本格式无效")?;
    let normalize = |s: &str| { let mut p: Vec<_> = s.trim().split('.').collect(); while p.len()<3 { p.push("0"); } p.join(".") };
    let minimum = info["minimum_macos"].as_str().ok_or("更新包缺少系统下限")?;
    if semver::Version::parse(&normalize(&current)).map_err(|_| "系统版本格式无效")?
        < semver::Version::parse(&normalize(minimum)).map_err(|_| "更新包系统下限无效")? {
        return Err("此更新需要更高版本的 macOS".into());
    }
    Ok(info)
}

async fn check_release(app: &tauri::AppHandle, token: Option<&str>) -> Result<Option<Update>,String> {
    let endpoint=if token.is_some() {
        release_asset(token,"desktop-dev","latest.json",64*1024).await?
    } else {
        Url::parse("https://github.com/bbbushi/Aliciation/releases/download/desktop-dev/latest.json")
            .map_err(|_| "公开更新源配置无效")?
    };
    let updater=app.updater_builder().timeout(Duration::from_secs(30)).configure_client(secure_client)
        .endpoints(vec![endpoint]).map_err(|_| "更新附件配置不可用")?
        .headers(github_headers(token,"application/octet-stream")?)
        .build().map_err(|_| "更新配置不可用")?;
    let found=updater.check().await.map_err(|_| "更新清单不可访问或格式无效，请检查网络及发布文件")?;
    if let Some(update)=found.as_ref() {
        let filename=format!("Alice-{}-darwin-aarch64.app.tar.gz",update.version);
        if !allowed_transport(&update.download_url) || update.download_url.host_str()!=Some("github.com")
            || !update.download_url.path().starts_with("/bbbushi/Aliciation/releases/download/")
            || update.download_url.path().rsplit('/').next()!=Some(filename.as_str()) {
            return Err("更新包来源或名称不符合当前发布配置".into());
        }
    }
    Ok(found)
}

fn preference_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf,String> {
    app.path().app_config_dir().map(|root|root.join("updater.json")).map_err(|_| "更新偏好目录不可用".into())
}

fn save_preferences(app: &tauri::AppHandle, value: &Preferences) -> Result<(),String> {
    let path=preference_path(app)?;
    let directory=path.parent().ok_or("更新偏好目录不可用")?;
    private_directory(directory)?;
    if path.is_symlink() { return Err("更新偏好不能是符号链接".into()); }
    let temporary=directory.join(format!(".updater-{}.tmp",uuid::Uuid::new_v4().simple()));
    let result:Result<(),String>=(|| {
        let mut options=fs::OpenOptions::new(); options.write(true).create_new(true);
        #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
        let mut file=options.open(&temporary).map_err(|_| "更新偏好无法保存")?;
        file.write_all(json!({"enabled":value.enabled,"announced":value.announced}).to_string().as_bytes())
            .and_then(|_|file.sync_all()).map_err(|_| "更新偏好无法落盘")?;
        fs::rename(&temporary,&path).map_err(|_| "更新偏好无法替换".to_string())
    })();
    if result.is_err() { let _=fs::remove_file(temporary); }
    result
}

fn load_preferences(app: &tauri::AppHandle) -> Preferences {
    let disabled=||Preferences { enabled:false,announced:None };
    let Ok(path)=preference_path(app) else { return disabled(); };
    if path.is_symlink() { return disabled(); }
    let metadata=match fs::metadata(&path) {
        Ok(value)=>value,
        Err(error) if error.kind()==std::io::ErrorKind::NotFound=>return Preferences::default(),
        Err(_)=>return disabled(),
    };
    if !metadata.is_file() || metadata.len()>4096 { return disabled(); }
    let Ok(bytes)=fs::read(&path) else { return disabled(); };
    let Ok(value)=serde_json::from_slice::<Value>(&bytes) else { return disabled(); };
    Preferences { enabled:value["enabled"].as_bool().unwrap_or(false),
        announced:value["announced"].as_str().filter(|v|v.len()<160).map(str::to_owned) }
}

// Only fetches version metadata. Never downloads or installs from this timer.
pub fn start_scheduler(app: tauri::AppHandle, backend: Arc<Backend>) {
    let updates=app.state::<Arc<Updates>>().inner().clone();
    *updates.preferences.lock().unwrap()=load_preferences(&app);
    if cfg!(debug_assertions) || !cfg!(target_os="macos") || !cfg!(target_arch="aarch64") { return; }
    std::thread::spawn(move|| {
        std::thread::sleep(Duration::from_secs(30));
        let mut due=Instant::now();
        loop {
            if backend.allow_exit.load(Ordering::SeqCst) { return; }
            if Instant::now()>=due && updates.preferences.lock().unwrap().enabled
                && backend.launch_done.load(Ordering::SeqCst) && !backend.reader_done.load(Ordering::SeqCst)
                && !backend.closing.load(Ordering::SeqCst) && !backend.suspended.load(Ordering::SeqCst)
                && !backend.maintenance_pending.load(Ordering::SeqCst) && !backend.maintenance_busy.load(Ordering::SeqCst)
                && !updates.busy.swap(true,Ordering::SeqCst) {
                let _guard=BusyGuard { updates:updates.clone(),backend:backend.clone() };
                let token={ let session=updates.session.lock().unwrap();
                    if session.bytes.is_some() { due=Instant::now()+Duration::from_secs(86400); None }
                    else { Some(session.token.clone()) }
                };
                if let Some(token)=token {
                    let found=tauri::async_runtime::block_on(check_release(&app,token.as_deref()));
                    due=Instant::now()+Duration::from_secs(if found.is_ok() {86400} else {3600});
                    // Disabling checks or beginning shutdown during the request suppresses its result.
                    if updates.preferences.lock().unwrap().enabled && !backend.closing.load(Ordering::SeqCst)
                        && !backend.maintenance_pending.load(Ordering::SeqCst) && !backend.maintenance_busy.load(Ordering::SeqCst) {
                        if let Ok(Some(update))=found {
                            let version=update.version.clone();
                            { let mut session=updates.session.lock().unwrap(); session.phase="available".into();
                                session.error.clear(); session.update=Some(update); }
                            let handle=app.clone(); let pending=updates.clone(); let control=backend.clone();
                            let _=app.run_on_main_thread(move|| {
                                if pending.session.lock().unwrap().update.as_ref().map(|u| &u.version)!=Some(&version) { return; }
                                let mut preferences=pending.preferences.lock().unwrap();
                                if !preferences.enabled || preferences.announced.as_ref()==Some(&version)
                                    || control.closing.load(Ordering::SeqCst) || control.maintenance_pending.load(Ordering::SeqCst)
                                    || control.suspended.load(Ordering::SeqCst) || control.reader_done.load(Ordering::SeqCst) { return; }
                                if open_window(&handle,false) {
                                    preferences.announced=Some(version); let _=save_preferences(&handle,&preferences);
                                }
                                drop(preferences);
                                notify(&handle,&state(&handle,&pending));
                            });
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_secs(60));
        }
    });
}

struct BusyGuard { updates: Arc<Updates>, backend: Arc<Backend> }
impl Drop for BusyGuard {
    fn drop(&mut self) {
        if !self.backend.update_pending.load(Ordering::SeqCst) { self.updates.busy.store(false, Ordering::SeqCst); }
    }
}

#[tauri::command]
pub async fn update_action(window: WebviewWindow, app: tauri::AppHandle, updates: tauri::State<'_, Arc<Updates>>,
    backend: tauri::State<'_, Arc<Backend>>, action: String, confirmed: bool, token: Option<String>) -> Result<Value, String> {
    if window.label() != "updates" { return Err("此入口仅供应用内更新窗口使用".into()); }
    if action == "state" { return Ok(state(&app, &updates)); }
    if !matches!(action.as_str(), "check" | "download" | "install" | "forget" | "help" | "auto_on" | "auto_off") { return Err("未知更新操作".into()); }
    if !confirmed { return Err("更新操作需要明确确认".into()); }
    if action=="auto_on" || action=="auto_off" {
        let result={ let mut preferences=updates.preferences.lock().unwrap();
          let changed=Preferences { enabled:action=="auto_on",announced:preferences.announced.clone() };
          save_preferences(&app,&changed).map(|_| { *preferences=changed; }) };
        if let Err(error)=result {
            updates.session.lock().unwrap().error=error.clone(); return Err(error);
        }
        updates.session.lock().unwrap().error.clear();
        return Ok(state(&app,&updates));
    }
    if action=="help" {
        std::process::Command::new("/usr/bin/open").arg("https://github.com/settings/personal-access-tokens/new?name=Alice%20Updater&target_name=bbbushi&contents=read&expires_in=30")
            .status().map_err(|_| "无法打开 GitHub 授权页面")?;
        return Ok(state(&app,&updates));
    }
    if !cfg!(target_os="macos") || !cfg!(target_arch="aarch64") { return Err("此批仅支持 Apple Silicon macOS 更新".into()); }
    let updates = updates.inner().clone(); let backend = backend.inner().clone();
    if backend.maintenance_pending.load(Ordering::SeqCst) || backend.maintenance_busy.load(Ordering::SeqCst)
        || updates.busy.swap(true, Ordering::SeqCst) { return Err("正在处理其他维护或更新操作".into()); }
    let _guard = BusyGuard { updates:updates.clone(), backend:backend.clone() };
    updates.session.lock().unwrap().error.clear();
    let result: Result<(),String> = async {
        match action.as_str() {
            "forget" => { *updates.session.lock().unwrap()=Session {phase:"unauthorized".into(),..Session::default()}; }
            "check" => {
                if let Some(value)=token.filter(|value| !value.trim().is_empty()) {
                    let value=value.trim().to_string();
                    if !value.starts_with("github_pat_") || value.len()>4096 || !value.is_ascii()
                        || value.bytes().any(|b|b.is_ascii_whitespace() || b.is_ascii_control()) {
                        return Err("请使用 GitHub fine-grained 只读令牌（github_pat_ 开头），不要填写模型 API Key".into());
                    }
                    updates.session.lock().unwrap().token=Some(value);
                }
                let token=updates.session.lock().unwrap().token.clone();
                { let mut session=updates.session.lock().unwrap(); session.update=None; session.bytes=None; session.release=None; }
                phase(&app,&updates,"checking");
                let found=check_release(&app,token.as_deref()).await?;
                if let Some(update)=found {
                    updates.session.lock().unwrap().update=Some(update); phase(&app,&updates,"available");
                } else { phase(&app,&updates,"current"); }
            }
            "download" => {
                let update=updates.session.lock().unwrap().update.clone().ok_or("请先检查更新")?;
                let token=updates.session.lock().unwrap().token.clone();
                let filename=format!("Alice-{}-darwin-aarch64.app.tar.gz",update.version);
                if update.download_url.path().rsplit('/').next()!=Some(filename.as_str()) { return Err("更新附件名称与版本不一致".into()); }
                let endpoint=if token.is_some() {
                    release_asset(token.as_deref(),&format!("v{}",update.version),&filename,MAX_DOWNLOAD as u64).await?
                } else { update.download_url.clone() };
                phase(&app,&updates,"downloading");
                let client=secure_client(reqwest::Client::builder()).user_agent(format!("Alice-Updater/{}",app.package_info().version))
                    .timeout(Duration::from_secs(600)).build().map_err(|_| "下载配置不可用")?;
                let mut response=client.get(endpoint).headers(github_headers(token.as_deref(),"application/octet-stream")?)
                    .send().await.map_err(|_| "更新下载失败，可稍后重试")?;
                if !response.status().is_success() {
                    return Err(format!("更新下载失败（HTTP {}），请检查版本标签、附件与访问权限",response.status().as_u16()));
                }
                if response.content_length().is_some_and(|n| n>MAX_DOWNLOAD as u64) {
                    return Err("更新文件超过 512 MiB 下载限制".into());
                }
                let total=response.content_length(); let mut bytes=Vec::new(); let mut last=Instant::now();
                while let Some(chunk)=response.chunk().await.map_err(|_| "下载中断，当前版本仍可继续使用")? {
                    if bytes.len()+chunk.len()>MAX_DOWNLOAD { return Err("更新下载超出大小限制".into()); }
                    bytes.extend_from_slice(&chunk);
                    if last.elapsed()>Duration::from_millis(250) { notify(&app,&json!({"phase":"downloading","received":bytes.len(),"total":total})); last=Instant::now(); }
                }
                phase(&app,&updates,"verifying");
                update.verify(&bytes).map_err(|_| "更新包签名或签名版本不匹配，已拒绝安装")?;
                let release=inspect_package(&bytes,&update.version)?;
                { let mut session=updates.session.lock().unwrap(); session.bytes=Some(Arc::new(bytes)); session.release=Some(release); }
                phase(&app,&updates,"verified");
            }
            "install" => {
                if cfg!(debug_assertions) { return Err("源码窗口仅供检查与下载，请使用安装版执行更新")?; }
                { let session=updates.session.lock().unwrap(); if session.bytes.is_none() || session.release.is_none() { return Err("请先下载并通过验签")?; } }
                if !maintenance::alive(&backend)? { return Err("请先从备份与恢复窗口启动 Alice，再准备更新")?; }
                backend.update_pending.store(true,Ordering::SeqCst);
                backend.backup_completed.store(false,Ordering::SeqCst);
                backend.maintenance_pending.store(true,Ordering::SeqCst);
                phase(&app,&updates,"preparing");
                let state=backend.clone();
                let reply=tauri::async_runtime::spawn_blocking(move|| maintenance::request(&state,"shutdown",Some("update")))
                    .await.map_err(|_| "更新准备请求中断")??;
                if reply["status"]!="quiescing" { return Err("服务正在退出或未确认更新准备")?; }
            }
            _ => unreachable!(),
        }
        Ok(())
    }.await;
    if let Err(error)=result {
        backend.update_pending.store(false,Ordering::SeqCst);
        if action=="install" { backend.maintenance_pending.store(false,Ordering::SeqCst); }
        let mut session=updates.session.lock().unwrap(); session.phase="error".into();session.error=error.clone();
        return Err(error);
    }
    Ok(state(&app,&updates))
}

pub(super) fn private_directory(path: &Path) -> Result<(),String> {
    if path.ancestors().any(|p| p.is_symlink()) { return Err("更新维护目录不能是符号链接".into()); }
    fs::create_dir_all(path).map_err(|_| "无法建立更新维护目录")?;
    #[cfg(unix)] { use std::os::unix::fs::PermissionsExt; fs::set_permissions(path,fs::Permissions::from_mode(0o700)).map_err(|_| "无法保护更新维护目录")?; }
    Ok(())
}

fn install_after_backup(app: &tauri::AppHandle, updates: &Updates) -> Result<(),String> {
    let (update,bytes)={ let s=updates.session.lock().unwrap(); (s.update.clone().ok_or("更新信息已丢失")?,s.bytes.clone().ok_or("更新包已丢失")?) };
    update.verify(bytes.as_ref()).map_err(|_| "安装前再次验签失败")?;
    let release=inspect_package(bytes.as_ref(),&update.version)?;
    let root=app.path().resolve("Alice",tauri::path::BaseDirectory::LocalData).map_err(|_| "数据目录不可用")?;
    let program_backup=root.join(".maintenance/updates").join(uuid::Uuid::new_v4().simple().to_string());
    private_directory(&program_backup)?;
    let executable=std::env::current_exe().map_err(|_| "无法定位当前程序")?;
    let current_app=executable.parent().and_then(Path::parent).and_then(Path::parent).ok_or("当前程序不在应用包中")?;
    if current_app.extension().and_then(|s|s.to_str())!=Some("app") { return Err("请从已安装的 Alice.app 执行更新".into()); }
    let disk:Value=serde_json::from_slice(&fs::read(current_app.join("Contents/Resources/release.json")).map_err(|_| "无法核对当前应用版本")?)
        .map_err(|_| "当前应用版本声明无效")?;
    if disk["version"]!=app.package_info().version.to_string() { return Err("磁盘上的应用已被替换，请重新打开 Alice 再检查更新".into()); }
    let copied=std::process::Command::new("/usr/bin/ditto").arg(current_app).arg(program_backup.join("Alice.app"))
        .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status().map_err(|_| "无法保留旧版程序")?;
    if !copied.success() { return Err("旧版程序保留失败，更新已停止")?; }
    let verified=std::process::Command::new("/usr/bin/codesign").args(["--verify","--deep","--strict"])
        .arg(program_backup.join("Alice.app")).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
        .status().map_err(|_| "无法核验旧版程序副本")?;
    if !verified.success() { return Err("旧版程序副本核验失败，更新已停止")?; }
    let mut journal=fs::OpenOptions::new().write(true).create_new(true).open(program_backup.join("update.json")).map_err(|_| "更新记录无法写入")?;
    journal.write_all(json!({"source_version":app.package_info().version.to_string(),"target":release,"stage":"backed_up"}).to_string().as_bytes())
        .and_then(|_|journal.sync_all()).map_err(|_| "更新记录无法落盘")?;
    phase(app,updates,"installing");
    update.install(bytes.as_ref()).map_err(|_| "应用替换失败，数据恢复点与旧版程序副本已保留")?;
    let nonce=uuid::Uuid::new_v4().simple().to_string();
    maintenance::helper(app,"authorize",None,Some(&nonce)).map_err(|_| "新版已安装但启动预检未通过，请重新打开并使用恢复窗口")?;
    phase(app,updates,"restarting");
    let mut env=app.env();
    env.args_os.truncate(1); env.args_os.push("--alice-update-nonce".into()); env.args_os.push(nonce.into());
    let handle=app.clone();
    app.run_on_main_thread(move|| { handle.cleanup_before_exit(); tauri::process::restart(&env); })
        .map_err(|_| "新版已安装，请手动重新打开 Alice")?;
    Ok(())
}

pub fn finish(app: tauri::AppHandle, backend: Arc<Backend>, clean: bool) {
    let updates=app.state::<Arc<Updates>>().inner().clone();
    std::thread::spawn(move|| {
        let result=if clean { install_after_backup(&app,&updates) } else { Err("备份或服务退出未完成，未执行安装".into()) };
        if let Err(error)=result {
            { let mut session=updates.session.lock().unwrap(); session.phase="error".into();session.error=error; }
            backend.update_pending.store(false,Ordering::SeqCst);backend.maintenance_pending.store(false,Ordering::SeqCst);
            backend.closing.store(false,Ordering::SeqCst);backend.suspended.store(true,Ordering::SeqCst);
            updates.busy.store(false,Ordering::SeqCst);open(&app);notify(&app,&state(&app,&updates));maintenance::open(&app);
        }
    });
}
