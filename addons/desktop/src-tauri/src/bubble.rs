//! Desktop-only presentation. All conversations still use the same-origin HTTP GUI.
use super::Backend;
use serde_json::{json, Value};
use std::{fs, io::Write, sync::{atomic::Ordering, Arc, Mutex}, time::{Duration, Instant}};
use tauri::{Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

#[derive(Clone)]
struct Preferences { enabled: bool, show_body: bool, solid: bool, position: Option<(i32,i32)>, chat_position: Option<(i32,i32)>, avatar: String, accent: String }
impl Default for Preferences {
    fn default() -> Self {Self {enabled:false,show_body:true,solid:false,position:None,chat_position:None,
        avatar:"letter".into(),accent:"sage".into()}}
}
struct Model {
    preferences: Preferences, expanded: bool, pinned: bool, unread: bool, actions_until: Option<Instant>, actions_hover: bool, main_settings_pending: bool,
    preview: String, preview_until: Option<Instant>, last_notice: Option<(String,u64)>,
    moved: Option<Instant>, chat_moved: Option<Instant>, background_since: Option<Instant>, load_error: String,
}
impl Default for Model {
    fn default() -> Self { Self { preferences:Preferences { show_body:true, ..Default::default() },
        expanded:false, pinned:false, unread:false, actions_until:None, actions_hover:false, main_settings_pending:false, preview:String::new(), preview_until:None,
        last_notice:None, moved:None, chat_moved:None, background_since:None, load_error:String::new() } }
}
#[derive(Default)]
pub struct Bubble { model: Mutex<Model> }

fn available(backend: &Backend) -> bool {
    backend.launch_done.load(Ordering::SeqCst) && !backend.reader_done.load(Ordering::SeqCst)
        && !backend.closing.load(Ordering::SeqCst) && !backend.suspended.load(Ordering::SeqCst)
        && !backend.maintenance_pending.load(Ordering::SeqCst) && !backend.maintenance_busy.load(Ordering::SeqCst)
}
fn local(url: &tauri::Url, path: &str) -> bool {
    url.path()==path && url.query().is_none() && url.fragment().is_none()
        && url.username().is_empty() && url.password().is_none()
        && ((url.scheme()=="tauri" && url.host_str()==Some("localhost"))
            || (url.scheme()=="http" && url.host_str()==Some("tauri.localhost")))
}
fn preferences_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf,String> {
    app.path().app_config_dir().map(|p|p.join("bubble.json")).map_err(|_|"气泡偏好目录不可用".into())
}
fn save(app: &tauri::AppHandle, preferences: &Preferences) -> Result<(),String> {
    let path=preferences_path(app)?;
    let directory=path.parent().ok_or("气泡偏好目录不可用")?;
    super::updates::private_directory(directory)?;
    if path.is_symlink() { return Err("气泡偏好不能是符号链接".into()); }
    let temporary=directory.join(format!(".bubble-{}.tmp",uuid::Uuid::new_v4().simple()));
    let result=(|| {
        let mut options=fs::OpenOptions::new(); options.write(true).create_new(true);
        #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
        let mut file=options.open(&temporary).map_err(|_|"无法保存气泡偏好")?;
        file.write_all(json!({"enabled":preferences.enabled,"show_body":preferences.show_body,
            "solid":preferences.solid,"position":preferences.position,"chat_position":preferences.chat_position,
            "avatar":preferences.avatar,"accent":preferences.accent}).to_string().as_bytes())
            .and_then(|_|file.sync_all()).map_err(|_|"气泡偏好未能落盘")?;
        fs::rename(&temporary,&path).map_err(|_|"气泡偏好未能替换".to_string())
    })();
    if result.is_err() { let _=fs::remove_file(temporary); }
    result
}
fn load(app: &tauri::AppHandle) -> Result<Preferences,String> {
    let path=preferences_path(app)?;
    if path.is_symlink() { return Err("气泡偏好不可用，请重新保存".into()); }
    let metadata=match fs::metadata(&path) {
        Err(e) if e.kind()==std::io::ErrorKind::NotFound => return Ok(Preferences {show_body:true,..Default::default()}),
        value=>value.map_err(|_|"无法读取气泡偏好")?,
    };
    if !metadata.is_file() || metadata.len()>4096 { return Err("气泡偏好格式无效".into()); }
    let value:Value=serde_json::from_slice(&fs::read(path).map_err(|_|"无法读取气泡偏好")?)
        .map_err(|_|"气泡偏好格式无效")?;
    let parse_position=|value: &Value| -> Option<(i32,i32)> {value.as_array().filter(|a|a.len()==2).and_then(|a|
        Some((i32::try_from(a[0].as_i64()?).ok()?,i32::try_from(a[1].as_i64()?).ok()?)))};
    Ok(Preferences {enabled:cfg!(target_os="macos") && value["enabled"].as_bool().unwrap_or(false),
        show_body:value["show_body"].as_bool().unwrap_or(false),solid:value["solid"].as_bool().unwrap_or(false),
        position:parse_position(&value["position"]),chat_position:parse_position(&value["chat_position"]),
        avatar:value["avatar"].as_str().filter(|v|matches!(*v,"letter"|"orb"|"moon"|"flower")).unwrap_or("letter").into(),
        accent:value["accent"].as_str().filter(|v|matches!(*v,"sage"|"blue"|"rose"|"amber")).unwrap_or("sage").into()})
}
fn state(app: &tauri::AppHandle) -> Value {
    let bubble=app.state::<Arc<Bubble>>(); let model=bubble.model.lock().unwrap();
    let backend=app.state::<Arc<Backend>>();
    json!({"supported":cfg!(target_os="macos"),"enabled":model.preferences.enabled,
        "show_body":model.preferences.show_body,"solid":model.preferences.solid,
        "expanded":model.expanded,"pinned":model.pinned,"unread":model.unread,
        "actions_open":model.actions_until.is_some(),
        "avatar":model.preferences.avatar,"accent":model.preferences.accent,
        "main_settings_pending":model.main_settings_pending,
        "available":available(&backend),"error":model.load_error})
}
fn evaluate(app: &tauri::AppHandle, label: &str, function: &str, value: &Value) {
    if let Some(window)=app.get_webview_window(label) {
        let _=window.eval(&format!("window.{function} && window.{function}({value})"));
    }
}
fn set_visible(app: &tauri::AppHandle, label: &str, visible: bool) {
    if let Some(window)=app.get_webview_window(label) {
        if window.is_visible().unwrap_or(false)!=visible {
            if visible {let _=window.show();} else {let _=window.hide();}
        }
    }
}
pub fn hide(app: &tauri::AppHandle) {
    for label in ["bubble-anchor","bubble-actions","bubble-preview","bubble-chat"] {set_visible(app,label,false);}
}
pub fn show_main(app: &tauri::AppHandle) {
    if let Some(window)=app.get_webview_window("main") {
        let _=window.unminimize(); let _=window.show(); let _=window.set_focus();
    }
    let bubble=app.state::<Arc<Bubble>>(); let mut model=bubble.model.lock().unwrap();
    model.expanded=false; model.actions_until=None; model.unread=false; model.preview.clear(); model.preview_until=None;
    drop(model); hide(app);
}
pub fn close_main_to_background(app: &tauri::AppHandle) -> bool {
    if !cfg!(target_os="macos") {return false;}
    let backend=app.state::<Arc<Backend>>();
    let bubble=app.state::<Arc<Bubble>>();
    if !available(&backend) || !bubble.model.lock().unwrap().preferences.enabled {return false;}
    // Never hide the only usable window if a bubble failed to initialize.
    if let Err(error)=ensure_windows(app) {bubble_error(app,error);return true;}
    if let Some(window)=app.get_webview_window("main") { let _=window.hide(); }
    true
}
pub fn open_settings(app: &tauri::AppHandle) {
    hide(app);
    if let Some(window)=app.get_webview_window("bubble-settings") {
        let _=window.show(); let _=window.set_focus(); return;
    }
    let _=WebviewWindowBuilder::new(app,"bubble-settings",WebviewUrl::App("bubble-settings.html".into()))
        .title("Alice — 桌面气泡").inner_size(490.0,540.0).resizable(false)
        .on_navigation(|url|local(url,"/bubble-settings.html"))
        .on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny).build();
}
fn open_panel(app: &tauri::AppHandle, label: &'static str, path: &'static str, title: &str, width: f64, height: f64) -> Result<(),String> {
    {let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();model.actions_until=None;model.expanded=false;}
    hide(app);
    if let Some(window)=app.get_webview_window(label) {
        window.show().and_then(|_|window.set_focus()).map_err(|_|"无法打开窗口")?;return Ok(());
    }
    WebviewWindowBuilder::new(app,label,WebviewUrl::App(path.trim_start_matches('/').into()))
        .title(title).inner_size(width,height).resizable(false)
        .on_navigation(move|url|local(url,path))
        .on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny)
        .build().map(|_|()).map_err(|_|"无法打开窗口".into())
}
fn open_quit(app: &tauri::AppHandle) -> Result<(),String> {
    open_panel(app,"bubble-quit","/bubble-quit.html","Alice — 确认退出",430.0,300.0)
}
fn open_appearance(app: &tauri::AppHandle) -> Result<(),String> {
    open_panel(app,"bubble-appearance","/bubble-appearance.html","Alice — 装扮",490.0,620.0)
}
fn open_app_settings(app: &tauri::AppHandle) {
    app.state::<Arc<Bubble>>().model.lock().unwrap().main_settings_pending=true;
    show_main(app);
    if let Some(window)=app.get_webview_window("main") {
        let _=window.eval("window.__aliceOpenSettings=true; window.AliceDesktopOpenSettings && window.AliceDesktopOpenSettings();");
    }
}
fn ensure_windows(app: &tauri::AppHandle) -> Result<(),String> {
    let backend=app.state::<Arc<Backend>>();
    let origin=backend.origin.lock().unwrap().clone().ok_or("Alice 尚未连接")?;
    for (label,path,width,height) in [("bubble-anchor","bubble-anchor.html",44.0,44.0),
        ("bubble-actions","bubble-actions.html",200.0,44.0),
        ("bubble-preview","bubble-preview.html",320.0,110.0)] {
        if app.get_webview_window(label).is_none() {
            let window=WebviewWindowBuilder::new(app,label,WebviewUrl::App(path.into()))
                .title("Alice 桌面气泡").inner_size(width,height).transparent(true).decorations(false)
                .shadow(false).resizable(false).skip_taskbar(true).always_on_top(true)
                .visible(false).focused(false).focusable(false).accept_first_mouse(true)
                .initialization_script(if label=="bubble-preview" {include_str!("../../../../web/desktop-speech.js")} else {""})
                .on_navigation(move|url|local(url,&format!("/{path}")))
                .on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny)
                .build().map_err(|_|"无法创建桌面气泡窗口")?;
            if label=="bubble-preview" {
                if window.set_ignore_cursor_events(true).is_err() {let _=window.destroy();return Err("无法启用消息鼠标穿透".into());}
            }
        }
    }
    if app.get_webview_window("bubble-chat").is_none() {
        let url=tauri::Url::parse(&format!("{origin}/desktop-chat.html")).map_err(|_|"聊天地址不可用")?;
        let expected=origin.clone();
        WebviewWindowBuilder::new(app,"bubble-chat",WebviewUrl::External(url))
            .title("Alice — 桌面聊天").inner_size(340.0,420.0).transparent(true).decorations(false)
            .background_throttling(tauri::utils::config::BackgroundThrottlingPolicy::Disabled)
            .shadow(false).resizable(false).skip_taskbar(true).always_on_top(true)
            .visible(false).focused(false).accept_first_mouse(true)
            .on_navigation(move|url|url.origin().ascii_serialization()==expected && url.path()=="/desktop-chat.html"
                && url.query().is_none() && url.fragment().is_none() && url.username().is_empty() && url.password().is_none())
            .on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny)
            .build().map_err(|_|"无法创建桌面聊天窗口")?;
    }
    Ok(())
}
pub fn authorize_origin(app: &tauri::AppHandle, origin: &str) -> Result<(),String> {
    if let Some(window)=app.get_webview_window("bubble-chat") {
        if window.url().ok().is_some_and(|url|url.origin().ascii_serialization()!=origin) {let _=window.destroy();}
    }
    // The backend chooses an ephemeral loopback port; grant only this instance's GUI.
    app.add_capability(json!({"identifier":format!("bubble-loopback-{}",uuid::Uuid::new_v4().simple()),
        "local":false,"windows":["main","bubble-chat"],"remote":{"urls":[format!("{origin}/*")]},
        "permissions":["allow-bubble-action"]}).to_string()).map_err(|_|"气泡窗口授权未完成".into())
}
fn layout(app: &tauri::AppHandle) {
    let Some(anchor)=app.get_webview_window("bubble-anchor") else {return;};
    let bubble=app.state::<Arc<Bubble>>();
    let (preference,settled,chat_moving)={let model=bubble.model.lock().unwrap();
        (model.preferences.clone(),model.moved.is_some_and(|t|t.elapsed()>Duration::from_millis(700)),
            model.chat_moved.is_some_and(|t|t.elapsed()<Duration::from_millis(700)))};
    let Ok(monitors)=anchor.available_monitors() else {return;};
    let monitor=preference.position.and_then(|(x,y)|monitors.iter().find(|m| {
        let a=m.work_area(); x>=a.position.x && y>=a.position.y
            && i64::from(x)<i64::from(a.position.x)+i64::from(a.size.width)
            && i64::from(y)<i64::from(a.position.y)+i64::from(a.size.height)
    })).or_else(||monitors.first());
    let Some(monitor)=monitor else {return;};
    let area=monitor.work_area(); let scale=monitor.scale_factor();
    let px=|n:f64|(n*scale).round() as i32;
    let right=area.position.x.saturating_add(area.size.width.min(i32::MAX as u32) as i32);
    let bottom=area.position.y.saturating_add(area.size.height.min(i32::MAX as u32) as i32);
    let size=px(44.0); let margin=px(12.0);
    let (x,y)=preference.position.unwrap_or((right-size-margin,bottom-size-px(100.0)));
    let mut x=x.clamp(area.position.x,right.saturating_sub(size).max(area.position.x));
    if settled {
        if x-area.position.x<px(24.0) {x=area.position.x;}
        else if right-size-x<px(24.0) {x=right-size;}
    }
    let y=y.clamp(area.position.y,bottom.saturating_sub(size).max(area.position.y));
    if anchor.outer_position().ok()!=Some(PhysicalPosition::new(x,y)) {let _=anchor.set_position(PhysicalPosition::new(x,y));}
    for (label,width,height) in [("bubble-actions",200.0,44.0),("bubble-preview",320.0,110.0),("bubble-chat",340.0,420.0)] {
        if let Some(window)=app.get_webview_window(label) {
            if label=="bubble-chat" && preference.chat_position.is_some() {
                // Leave native movement alone while the pointer is moving; never anchor it back.
                if chat_moving {continue;}
                let (cx,cy)=preference.chat_position.unwrap();
                let target=monitors.iter().find(|m| {
                    let a=m.work_area();cx>=a.position.x && cy>=a.position.y
                        && i64::from(cx)<i64::from(a.position.x)+i64::from(a.size.width)
                        && i64::from(cy)<i64::from(a.position.y)+i64::from(a.size.height)
                }).unwrap_or(monitor);
                let a=target.work_area();let scale=target.scale_factor();
                let w=((width*scale).round() as u32).min(a.size.width).max(1);
                let h=((height*scale).round() as u32).min(a.size.height).max(1);
                let max_x=(i64::from(a.position.x)+i64::from(a.size.width)-i64::from(w)).clamp(i64::from(i32::MIN),i64::from(i32::MAX)) as i32;
                let max_y=(i64::from(a.position.y)+i64::from(a.size.height)-i64::from(h)).clamp(i64::from(i32::MIN),i64::from(i32::MAX)) as i32;
                let position=PhysicalPosition::new(cx.clamp(a.position.x,max_x.max(a.position.x)),cy.clamp(a.position.y,max_y.max(a.position.y)));
                if window.inner_size().ok()!=Some(PhysicalSize::new(w,h)) {let _=window.set_size(PhysicalSize::new(w,h));}
                if window.outer_position().ok()!=Some(position) {let _=window.set_position(position);}
                continue;
            }
            let w=px(width).min(area.size.width as i32).max(1); let h=px(height).min(area.size.height as i32).max(1);
            let left=if x-area.position.x>w+margin {x-w-margin} else {x+size+margin};
            let left=left.clamp(area.position.x,(right-w).max(area.position.x));
            let top=(y+size-h).clamp(area.position.y,(bottom-h).max(area.position.y));
            if label=="bubble-actions" {evaluate(app,label,"bubbleActionsLayout",&json!({"side":if left<x {"left"} else {"right"}}));}
            if window.inner_size().ok()!=Some(PhysicalSize::new(w as u32,h as u32)) {let _=window.set_size(PhysicalSize::new(w as u32,h as u32));}
            if window.outer_position().ok()!=Some(PhysicalPosition::new(left,top)) {let _=window.set_position(PhysicalPosition::new(left,top));}
        }
    }
}
pub fn moved(app: &tauri::AppHandle, label: &str, position: PhysicalPosition<i32>) {
    let bubble=app.state::<Arc<Bubble>>(); let mut model=bubble.model.lock().unwrap();
    if label=="bubble-chat" {
        // Initial automatic placement still follows the anchor until the user first drags.
        if model.preferences.chat_position.is_some() && model.preferences.chat_position!=Some((position.x,position.y)) {
            model.preferences.chat_position=Some((position.x,position.y));
            model.chat_moved=Some(Instant::now());model.moved=Some(Instant::now());
        }
    } else if model.preferences.position!=Some((position.x,position.y)) {
        model.preferences.position=Some((position.x,position.y)); model.moved=Some(Instant::now());
    }
}
pub fn collapse(app: &tauri::AppHandle) {
    app.state::<Arc<Bubble>>().model.lock().unwrap().expanded=false;
    set_visible(app,"bubble-chat",false);
}
fn toggle_actions(app: &tauri::AppHandle) -> Result<(),String> {
    let backend=app.state::<Arc<Backend>>();
    if !available(&backend) {return Err("Alice 正在准备或维护".into());}
    ensure_windows(app)?;layout(app);
    let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();
    if !model.preferences.enabled {return Err("请先启用桌面气泡".into());}
    let open=model.actions_until.is_some();
    model.actions_until=if open {None} else {Some(Instant::now()+Duration::from_secs(12))};
    model.actions_hover=false;
    model.expanded=false;model.background_since=Some(Instant::now()-Duration::from_secs(1));
    drop(model);set_visible(app,"bubble-chat",false);set_visible(app,"bubble-preview",false);
    set_visible(app,"bubble-actions",!open);
    Ok(())
}
pub fn open_chat(app: &tauri::AppHandle) -> Result<(),String> {
    let backend=app.state::<Arc<Backend>>();
    if !available(&backend) {show_main(app); return Err("Alice 正在准备或维护，请在主界面查看".into());}
    if !app.state::<Arc<Bubble>>().model.lock().unwrap().preferences.enabled {
        open_settings(app); return Ok(());
    }
    ensure_windows(app)?; layout(app);
    { let bubble=app.state::<Arc<Bubble>>(); let mut model=bubble.model.lock().unwrap();
        model.expanded=true; model.actions_until=None; model.unread=false; model.preview.clear(); model.preview_until=None;
        // Explicit user activation bypasses the passive background debounce.
        model.background_since=Some(Instant::now()-Duration::from_secs(1)); }
    set_visible(app,"bubble-actions",false); set_visible(app,"bubble-preview",false);
    if let Some(window)=app.get_webview_window("bubble-chat") { let _=window.show(); let _=window.set_focus(); }
    Ok(())
}
fn tick(app: &tauri::AppHandle) {
    let backend=app.state::<Arc<Backend>>();
    let bubble=app.state::<Arc<Bubble>>();
    let enabled=bubble.model.lock().unwrap().preferences.enabled;
    if enabled && available(&backend) {
        if let Err(error)=ensure_windows(app) {
            bubble.model.lock().unwrap().load_error=error; hide(app); return;
        }
        layout(app);
    }
    let main_focused=app.get_webview_window("main").is_some_and(|w|w.is_focused().unwrap_or(false));
    let owned_focused=["maintenance","updates","bubble-settings","bubble-appearance","bubble-quit"].iter().any(|label|
        app.get_webview_window(label).is_some_and(|w|w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false)));
    let chat_focused=app.get_webview_window("bubble-chat").is_some_and(|w|w.is_focused().unwrap_or(false));
    let permitted=enabled && available(&backend) && !main_focused && !owned_focused;
    let mut model=bubble.model.lock().unwrap();
    if !permitted {
        model.background_since=None; model.expanded=false; model.actions_until=None;
        if main_focused {model.unread=false; model.preview.clear(); model.preview_until=None;}
    } else if model.background_since.is_none() {model.background_since=Some(Instant::now());}
    let background=permitted && model.background_since.is_some_and(|t|t.elapsed()>Duration::from_millis(800));
    let chat_moving=model.chat_moved.is_some_and(|t|t.elapsed()<Duration::from_millis(900));
    if model.expanded && !model.pinned && !chat_focused && !chat_moving {model.expanded=false;}
    let expanded=background && model.expanded;
    if !model.actions_hover && model.actions_until.is_some_and(|t|Instant::now()>=t) {model.actions_until=None;}
    let actions=background && !expanded && model.actions_until.is_some();
    let preview=background && !expanded && !actions && model.preferences.show_body && !model.preview.is_empty()
        && model.preview_until.is_some_and(|t|Instant::now()<t);
    let preview_text=if preview {model.preview.clone()} else {String::new()};
    if model.preview_until.is_some_and(|t|Instant::now()>=t) {model.preview.clear();model.preview_until=None;}
    let solid=model.preferences.solid; let unread=model.unread; let preview_key=model.last_notice.clone();
    let avatar=model.preferences.avatar.clone();let accent=model.preferences.accent.clone();
    let save_due=model.moved.is_some_and(|t|t.elapsed()>Duration::from_secs(1));
    let preferences=if save_due {model.moved=None;Some(model.preferences.clone())} else {None};
    drop(model);
    if let Some(preferences)=preferences {if let Err(error)=save(app,&preferences) {bubble.model.lock().unwrap().load_error=error;}}
    set_visible(app,"bubble-anchor",background);
    set_visible(app,"bubble-actions",actions);
    set_visible(app,"bubble-preview",preview);
    set_visible(app,"bubble-chat",expanded);
    evaluate(app,"bubble-anchor","bubbleState",&json!({"unread":unread,"actions_open":actions,"avatar":avatar,"accent":accent}));
    evaluate(app,"bubble-preview","bubbleState",&json!({"text":preview_text,"solid":solid,"key":preview_key}));
    let mut value=state(app); value["background"]=json!(background);value["main_focused"]=json!(main_focused);
    evaluate(app,"bubble-chat","bubbleHostState",&value);
}
pub fn initialize(app: &tauri::AppHandle) -> Result<(),Box<dyn std::error::Error>> {
    { let bubble=app.state::<Arc<Bubble>>(); let mut model=bubble.model.lock().unwrap();
        match load(app) {Ok(preferences)=>model.preferences=preferences,Err(error)=>model.load_error=error} }
    if cfg!(target_os="macos") {
        use tauri::{menu::{Menu,MenuItem},tray::TrayIconBuilder};
        let menu=Menu::with_items(app,&[
            &MenuItem::with_id(app,"bubble_chat","打开桌面聊天",true,None::<&str>)?,
            &MenuItem::with_id(app,"bubble_main","打开 Alice 主界面",true,None::<&str>)?,
            &MenuItem::with_id(app,"bubble_settings","桌面气泡设置…",true,None::<&str>)?,
            &MenuItem::with_id(app,"bubble_pause","暂停桌面气泡",true,None::<&str>)?,
            &MenuItem::with_id(app,"bubble_quit","退出 Alice",true,None::<&str>)?,
        ])?;
        let mut rgba=vec![0u8;20*20*4];
        for y in 0..20i32 {for x in 0..20i32 {if (x-10).pow(2)+(y-10).pow(2)<=64 {
            let i=((y*20+x)*4) as usize; rgba[i+3]=255;
        }}}
        TrayIconBuilder::with_id("alice-bubble").icon(tauri::image::Image::new_owned(rgba,20,20))
            .icon_as_template(true).tooltip("Alice").menu(&menu).build(app)?;
    }
    let handle=app.clone();
    std::thread::spawn(move||loop {
        std::thread::sleep(Duration::from_millis(500));
        if handle.state::<Arc<Backend>>().allow_exit.load(Ordering::SeqCst) {break;}
        let current=handle.clone(); let _=handle.run_on_main_thread(move||tick(&current));
    });
    Ok(())
}
pub fn menu(app: &tauri::AppHandle, id: &str) {
    match id {
        "bubble_main"=>show_main(app),"bubble_settings"=>open_settings(app),
        "bubble_chat"=>{if let Err(error)=open_chat(app) {bubble_error(app,error);}},
        "bubble_pause"=>{
            let preferences={let bubble=app.state::<Arc<Bubble>>();let model=bubble.model.lock().unwrap();
                let mut preferences=model.preferences.clone();preferences.enabled=false;preferences};
            match save(app,&preferences) {Ok(())=>{
                app.state::<Arc<Bubble>>().model.lock().unwrap().preferences=preferences;show_main(app);
            },Err(error)=>bubble_error(app,error)}
        },
        "bubble_quit"=>{if let Err(error)=open_quit(app) {bubble_error(app,error);}},_=>{}
    }
}
fn bubble_error(app: &tauri::AppHandle, error: String) {
    app.state::<Arc<Bubble>>().model.lock().unwrap().load_error=error;open_settings(app);
}

#[tauri::command]
pub fn bubble_action(window: WebviewWindow, app: tauri::AppHandle, action: String, payload: Option<Value>) -> Result<Value,String> {
    let url=window.url().map_err(|_|"窗口来源不可用")?;
    let label=window.label();
    let backend=app.state::<Arc<Backend>>();
    let origin=backend.origin.lock().unwrap().clone();
    let same_origin=origin.as_deref()==Some(url.origin().ascii_serialization().as_str())
        && url.query().is_none() && url.fragment().is_none() && url.username().is_empty() && url.password().is_none();
    let settings=label=="bubble-settings" && local(&url,"/bubble-settings.html");
    let anchor=label=="bubble-anchor" && local(&url,"/bubble-anchor.html");
    let actions=label=="bubble-actions" && local(&url,"/bubble-actions.html");
    let appearance=label=="bubble-appearance" && local(&url,"/bubble-appearance.html");
    let quit=label=="bubble-quit" && local(&url,"/bubble-quit.html");
    let chat=label=="bubble-chat" && same_origin && url.path()=="/desktop-chat.html";
    let main=label=="main" && same_origin && matches!(url.path(),"/"|"/index.html");
    if !(settings||anchor||actions||appearance||quit||chat||main) {return Err("此窗口不能操作桌面气泡".into());}
    let payload=payload.unwrap_or(Value::Null);
    match action.as_str() {
        "status"=>{},
        "settings" if main||anchor||actions||chat=>open_settings(&app),
        "app_settings" if actions=>open_app_settings(&app),
        "settings_opened" if main=>{app.state::<Arc<Bubble>>().model.lock().unwrap().main_settings_pending=false;},
        "appearance" if actions=>open_appearance(&app)?,
        "appearance_close" if appearance=>window.close().map_err(|_|"无法关闭装扮窗口")?,
        "appearance_save" if appearance=>{
            let avatar=payload["avatar"].as_str().filter(|v|matches!(*v,"letter"|"orb"|"moon"|"flower")).ok_or("请选择支持的图案")?;
            let accent=payload["accent"].as_str().filter(|v|matches!(*v,"sage"|"blue"|"rose"|"amber")).ok_or("请选择支持的配色")?;
            let mut preferences=app.state::<Arc<Bubble>>().model.lock().unwrap().preferences.clone();
            preferences.avatar=avatar.into();preferences.accent=accent.into();save(&app,&preferences)?;
            let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();model.preferences=preferences;model.load_error.clear();
        },
        "quit_prompt" if actions=>open_quit(&app)?,
        "quit_cancel" if quit=>window.close().map_err(|_|"无法关闭退出确认")?,
        "quit_confirm" if quit=>{
            if payload["confirmed"]!=true {return Err("请明确确认退出".into());}
            if backend.maintenance_pending.load(Ordering::SeqCst) || backend.maintenance_busy.load(Ordering::SeqCst) || backend.update_pending.load(Ordering::SeqCst) {
                return Err("Alice 正在处理本机数据或更新，请等待完成后退出".into());
            }
            backend.quit(&app);
            if !backend.closing.load(Ordering::SeqCst) && !backend.allow_exit.load(Ordering::SeqCst) {return Err("退出尚未开始，请稍后重试".into());}
        },
        "save" if settings=>{
            if !cfg!(target_os="macos") {return Err("当前仅支持 macOS 桌面气泡".into());}
            let preferences={let bubble=app.state::<Arc<Bubble>>();let model=bubble.model.lock().unwrap();
                Preferences {enabled:payload["enabled"].as_bool().ok_or("设置无效")?,
                    show_body:payload["show_body"].as_bool().ok_or("设置无效")?,
                    solid:payload["solid"].as_bool().ok_or("设置无效")?,position:model.preferences.position,
                    chat_position:model.preferences.chat_position,avatar:model.preferences.avatar.clone(),accent:model.preferences.accent.clone()}};
            if preferences.enabled {ensure_windows(&app)?;}
            save(&app,&preferences)?;
            let disabled=!preferences.enabled;
            {let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();
                model.preferences=preferences;model.load_error.clear();model.preview.clear();model.preview_until=None;}
            if disabled {show_main(&app);}
        },
        "reset_position" if settings=>{
            let preferences={let bubble=app.state::<Arc<Bubble>>();let model=bubble.model.lock().unwrap();
                let mut preferences=model.preferences.clone();preferences.position=None;preferences.chat_position=None;preferences};
            save(&app,&preferences)?;app.state::<Arc<Bubble>>().model.lock().unwrap().preferences=preferences;layout(&app);
        },
        "toggle_actions" if anchor=>toggle_actions(&app)?,
        "actions_hover" if actions=>{
            let inside=payload["inside"].as_bool().ok_or("悬停状态无效")?;
            let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();model.actions_hover=inside;
            if model.actions_until.is_some() {model.actions_until=Some(Instant::now()+Duration::from_secs(12));}
        },
        "open" if actions=>open_chat(&app)?,
        "drag" if anchor||chat=>{
            if chat {
                let position=window.outer_position().map_err(|_|"无法读取聊天框位置")?;
                let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();
                model.preferences.chat_position=Some((position.x,position.y));
                model.chat_moved=Some(Instant::now());model.moved=Some(Instant::now());
            }
            window.start_dragging().map_err(|_|"无法移动聊天窗口")?;
        },
        "collapse" if chat=>collapse(&app),
        "main" if chat=>show_main(&app),
        "pin" if chat=>{app.state::<Arc<Bubble>>().model.lock().unwrap().pinned=payload["pinned"].as_bool().ok_or("固定状态无效")?;},
        "read" if chat=>{
            if window.is_visible().unwrap_or(false) && window.is_focused().unwrap_or(false) {
                app.state::<Arc<Bubble>>().model.lock().unwrap().unread=false;
            }
        },
        "notice" if chat=>{
            let epoch=payload["epoch"].as_str().filter(|s|!s.is_empty()&&s.len()<=128).ok_or("会话标识无效")?;
            let id=payload["id"].as_u64().filter(|n|*n>0 && *n<=9007199254740991).ok_or("消息标识无效")?;
            let text=payload["text"].as_str().ok_or("消息无效")?.chars().take(240).collect::<String>();
            let bubble=app.state::<Arc<Bubble>>();let mut model=bubble.model.lock().unwrap();
            if model.last_notice.as_ref()!=Some(&(epoch.to_owned(),id)) {
                model.last_notice=Some((epoch.to_owned(),id));
                if model.preferences.enabled && available(&backend) && !model.expanded {
                    model.unread=true;
                    if model.preferences.show_body {model.preview=text;model.preview_until=Some(Instant::now()+Duration::from_secs(10));}
                }
            }
        },
        _=>return Err("此窗口不支持该气泡操作".into()),
    }
    Ok(state(&app))
}
