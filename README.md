# html2apk

Turns an HTML file, or a whole folder of HTML/CSS/JS/images, into an Android app (APK).
Your page runs full-screen in a native WebView, works offline, and keeps `localStorage`.
Links to other websites open in the phone's browser, and the Back button works.

## Option A: build on GitHub (nothing to install, works from your phone)

1. Create a new GitHub repository and upload everything in this folder (keep the `.github` folder).
2. Put your site in `www/`. The first page must be `www/index.html`. To set your own icon, add `www/icon.png` (a square image, 512×512).
3. Edit `app.env` to set the app name, the package id and the orientation.
4. Commit. Then open the **Actions** tab, select the latest **Build APK** run and download **apk** under *Artifacts*. The APK is inside the zip.
5. Open the APK on your phone and allow "install unknown apps" when Android asks.

Each new commit builds a new APK. You can also start a build by hand with **Run workflow**.

## Option B: build on your computer

You need: Python 3.9+, JDK 17+, Gradle 8.9+, and the Android SDK. Installing Android Studio gives you the SDK; then set `ANDROID_HOME`.

```bash
python3 html2apk.py mypage.html --name "My App" --build
python3 html2apk.py ./my-site --name "My Site" --package com.me.mysite --icon icon.png --fullscreen --build
```

If you leave out `--build`, the tool only generates the Android project. You can then open that folder in Android Studio and press ▶ Run.

### Useful flags

| Flag | What it does |
|---|---|
| `--name` | App name under the icon |
| `--package` | Unique id, e.g. `com.yourname.app`. Keep it the same between versions so updates install over the old app |
| `--icon` | Square PNG/WebP/JPG |
| `--entry` | Page to open first if it isn't `index.html` |
| `--orientation` | `portrait`, `landscape`, … |
| `--fullscreen` | Hide the status bar |
| `--keep-awake` | Keep the screen from turning off |
| `--zoom` | Allow pinch-to-zoom |
| `--links-in-app` | Open web links inside the app |
| `--permission CAMERA` | Add an Android permission (repeatable) |
| `--include-siblings` | When you give a single .html file, also bundle the other files in its folder (images, css) |
| `--release --keystore my.keystore` | Build a signed release APK for the Play Store. Pass the password in the `KEYSTORE_PASSWORD` environment variable |

## Notes

- The default build is a *debug-signed* APK. You can install it on any Android 5.0+ phone, but the Play Store won't accept it. Use `--release` for the store.
- Use relative paths in your HTML (`img/cat.png`, not `/img/cat.png`). A leading `/` points to the root of the phone's storage, not your site folder.
- Pages that load content from the internet (CDNs, APIs) need a connection. Everything bundled in `www/` works offline.
