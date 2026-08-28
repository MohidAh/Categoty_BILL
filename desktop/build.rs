// v8.15.2 FIX: this build script was missing entirely. Without it:
//   1. OUT_DIR is never set -> tauri::generate_context!() fails with
//      "OUT_DIR env var is not set, do you have a build script?"
//   2. The `desktop` / `mobile` cfg flags are never emitted, so ALL code
//      inside #[cfg(desktop)] - the entire auto-updater - was silently
//      compiled OUT of the binary (that is why DialogExt / UpdaterExt
//      showed up as "unused imports" in the CI log).
//   3. icons/icon.ico is never embedded into the .exe resource section.
//
// Cargo.toml already had tauri-build under [build-dependencies]; it just
// never ran because this file did not exist.
fn main() {
    tauri_build::build()
}
