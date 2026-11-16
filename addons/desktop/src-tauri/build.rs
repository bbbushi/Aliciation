fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["bubble_action","maintenance_action","update_action"])
    )).expect("Alice native command permissions")
}
