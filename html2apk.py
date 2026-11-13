#!/usr/bin/env python3
"""
html2apk - turn an HTML file (or a folder of HTML/CSS/JS/images) into an Android APK.

It generates a small native Android app whose only job is to show your page
in a full-screen WebView, then (optionally) builds it with Gradle.

Usage examples
--------------
  # Single file
  python3 html2apk.py game.html --name "My Game"

  # Whole site folder (must contain index.html, or pass --entry)
  python3 html2apk.py ./my-site --name "My Site" --package com.weston.mysite --icon icon.png --build

  # Only generate the Android project (build it later / elsewhere)
  python3 html2apk.py ./my-site --name "My Site" --out ./android-project

Requirements for --build: JDK 17+ and either the Android SDK (ANDROID_HOME set)
or Android Studio installed, plus `gradle` 8.9+ on PATH.
No Android knowledge needed - just your HTML.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

AGP_VERSION = "8.7.3"
COMPILE_SDK = 35
MIN_SDK = 21


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "", text.lower())
    if not s or not s[0].isalpha():
        s = "app" + s
    return s


def valid_package(pkg):
    return re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+", pkg) is not None


# --------------------------------------------------------------------------- templates

SETTINGS_GRADLE = """pluginManagement {{
    repositories {{
        google()
        mavenCentral()
        gradlePluginPortal()
    }}
}}
dependencyResolutionManagement {{
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {{
        google()
        mavenCentral()
    }}
}}
rootProject.name = "{name}"
include(":app")
"""

ROOT_BUILD_GRADLE = """plugins {{
    id("com.android.application") version "{agp}" apply false
}}
"""

GRADLE_PROPERTIES = """org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
android.nonTransitiveRClass=true
"""

APP_BUILD_GRADLE = """plugins {{
    id("com.android.application")
}}

android {{
    namespace = "{package}"
    compileSdk = {compile_sdk}

    defaultConfig {{
        applicationId = "{package}"
        minSdk = {min_sdk}
        targetSdk = {compile_sdk}
        versionCode = {version_code}
        versionName = "{version_name}"
    }}
{signing}
    buildTypes {{
        release {{
            isMinifyEnabled = false
{release_signing}        }}
    }}

    compileOptions {{
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }}
}}
"""

SIGNING_BLOCK = """
    signingConfigs {{
        create("release") {{
            storeFile = file("{store}")
            storePassword = System.getenv("KEYSTORE_PASSWORD")
            keyAlias = "{alias}"
            keyPassword = System.getenv("KEY_PASSWORD") ?: System.getenv("KEYSTORE_PASSWORD")
        }}
    }}
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
{permissions}
    <application
        android:label="@string/app_name"
        android:icon="{icon}"
        android:roundIcon="{icon}"
        android:allowBackup="true"
        android:hardwareAccelerated="true"
        android:usesCleartextTraffic="true"
        android:theme="{theme}">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:screenOrientation="{orientation}"
            android:configChanges="orientation|screenSize|screenLayout|keyboardHidden|keyboard|smallestScreenSize|uiMode">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

STRINGS = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">{name}</string>
</resources>
"""

DEFAULT_ICON = """<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp" android:height="108dp"
    android:viewportWidth="108" android:viewportHeight="108">
    <path android:fillColor="{color}" android:pathData="M0,0h108v108h-108z"/>
    <path android:fillColor="#FFFFFF"
        android:pathData="M44,38 L30,54 L44,70 L48,66 L37.5,54 L48,42 Z M64,38 L60,42 L70.5,54 L60,66 L64,70 L78,54 Z"/>
</vector>
"""

MAIN_ACTIVITY = r"""package {package};

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public class MainActivity extends Activity {{

    private static final String START_URL = "{start_url}";
    private static final boolean FULLSCREEN = {fullscreen};
    private static final boolean OPEN_LINKS_IN_BROWSER = {external_links};
    private static final int FILE_CHOOSER_REQUEST = 1001;

    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        if (FULLSCREEN) {{
            getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                    WindowManager.LayoutParams.FLAG_FULLSCREEN);
        }}
        {keep_awake}

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("{background}"));
        setContentView(webView);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // localStorage / sessionStorage
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setAllowFileAccessFromFileURLs(true);      // lets local pages fetch() other local files
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom({zoom});
        s.setBuiltInZoomControls({zoom});
        s.setDisplayZoomControls(false);
        if (Build.VERSION.SDK_INT >= 21) {{
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }}

        webView.setWebViewClient(new WebViewClient() {{
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {{
                return handleUrl(request.getUrl());
            }}

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView view, String url) {{
                return handleUrl(Uri.parse(url));
            }}
        }});

        webView.setWebChromeClient(new WebChromeClient() {{
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {{
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {{
                    startActivityForResult(params.createIntent(), FILE_CHOOSER_REQUEST);
                }} catch (ActivityNotFoundException e) {{
                    fileCallback = null;
                    return false;
                }}
                return true;
            }}
        }});

        if (savedInstanceState != null) {{
            webView.restoreState(savedInstanceState);
        }} else {{
            webView.loadUrl(START_URL);
        }}
    }}

    private boolean handleUrl(Uri uri) {{
        String scheme = uri.getScheme() == null ? "" : uri.getScheme();
        if (scheme.equals("file")) return false;  // stay inside the app
        boolean web = scheme.equals("http") || scheme.equals("https");
        if (web && !OPEN_LINKS_IN_BROWSER) return false;
        try {{
            // mailto:, tel:, market:, intent:, and (optionally) web links
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        }} catch (ActivityNotFoundException ignored) {{ }}
        return true;
    }}

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {{
        if (requestCode == FILE_CHOOSER_REQUEST && fileCallback != null) {{
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
            return;
        }}
        super.onActivityResult(requestCode, resultCode, data);
    }}

    @Override
    public void onBackPressed() {{
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }}

    @Override
    protected void onSaveInstanceState(Bundle outState) {{
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }}

    @Override
    protected void onPause() {{ super.onPause(); webView.onPause(); }}

    @Override
    protected void onResume() {{ super.onResume(); webView.onResume(); }}
}}
"""

# --------------------------------------------------------------------------- generator


def generate(args):
    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        die(f"{src} does not exist")

    # Work out which files go in, and which page opens first.
    if src.is_file():
        if src.suffix.lower() not in (".html", ".htm"):
            die("input file must be .html or .htm")
        site_dir, entry = src.parent, src.name
        single_file = not args.include_siblings
    else:
        site_dir, single_file = src, False
        entry = args.entry
        if not entry:
            for cand in ("index.html", "index.htm"):
                if (site_dir / cand).exists():
                    entry = cand
                    break
            else:
                htmls = sorted(site_dir.glob("*.htm*"))
                if len(htmls) == 1:
                    entry = htmls[0].name
                else:
                    die("folder has no index.html - pass --entry yourpage.html")
        if not (site_dir / entry).exists():
            die(f"entry page {entry} not found in {site_dir}")

    name = args.name or Path(entry).stem.replace("-", " ").replace("_", " ").title()
    package = args.package or f"com.html2apk.{slug(name)}"
    if not valid_package(package):
        die(f"invalid package name '{package}' (use something like com.yourname.appname)")

    out = Path(args.out or f"{slug(name)}-android").expanduser().resolve()
    if out.exists():
        if not args.force:
            die(f"{out} already exists - use --force to overwrite or --out to pick another folder")
        shutil.rmtree(out)

    app = out / "app" / "src" / "main"
    java_dir = app / "java" / Path(*package.split("."))
    www = app / "assets" / "www"
    res = app / "res"
    for d in (java_dir, www, res / "values", res / "drawable"):
        d.mkdir(parents=True, exist_ok=True)

    # Copy web files.
    ignore = shutil.ignore_patterns(".git", ".github", "node_modules", "*-android", "__pycache__", ".DS_Store")
    if single_file:
        shutil.copy2(src, www / entry)
    else:
        shutil.copytree(site_dir, www, dirs_exist_ok=True, ignore=ignore)
        if out.is_relative_to(site_dir):  # never copy the output into itself
            shutil.rmtree(www / out.relative_to(site_dir), ignore_errors=True)

    # Icon.
    if args.icon:
        icon_path = Path(args.icon).expanduser()
        if not icon_path.is_file():
            die(f"icon {icon_path} not found")
        ext = icon_path.suffix.lower()
        if ext not in (".png", ".webp", ".jpg", ".jpeg"):
            die("icon must be .png, .webp or .jpg")
        mip = res / "mipmap-xxxhdpi"
        mip.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_path, mip / f"ic_launcher{'.jpg' if ext == '.jpeg' else ext}")
        icon_ref = "@mipmap/ic_launcher"
    else:
        (res / "drawable" / "ic_launcher.xml").write_text(DEFAULT_ICON.format(color=args.color))
        icon_ref = "@drawable/ic_launcher"

    perms = ['    <uses-permission android:name="android.permission.INTERNET" />']
    for p in args.permission or []:
        p = p if "." in p else f"android.permission.{p.upper()}"
        perms.append(f'    <uses-permission android:name="{escape(p)}" />')

    theme = ("@android:style/Theme.DeviceDefault.NoActionBar.Fullscreen" if args.fullscreen
             else "@android:style/Theme.DeviceDefault.NoActionBar")
    (app / "AndroidManifest.xml").write_text(MANIFEST.format(
        permissions="\n".join(perms), icon=icon_ref, theme=theme, orientation=args.orientation))
    (res / "values" / "strings.xml").write_text(
        STRINGS.format(name=escape(name).replace("'", "\\'").replace('"', '\\"')))

    start_url = "file:///android_asset/www/" + entry.replace("\\", "/").replace('"', "%22")
    (java_dir / "MainActivity.java").write_text(MAIN_ACTIVITY.format(
        package=package,
        start_url=start_url,
        fullscreen=str(args.fullscreen).lower(),
        external_links=str(not args.links_in_app).lower(),
        zoom=str(args.zoom).lower(),
        background=args.background,
        keep_awake=("getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);"
                    if args.keep_awake else ""),
    ))

    # Gradle files.
    signing = release_signing = ""
    if args.keystore:
        ks = Path(args.keystore).expanduser().resolve()
        if not ks.is_file():
            die(f"keystore {ks} not found")
        shutil.copy2(ks, out / "app" / "release.keystore")
        signing = SIGNING_BLOCK.format(store="release.keystore", alias=args.key_alias)
        release_signing = '            signingConfig = signingConfigs.getByName("release")\n'
    (out / "settings.gradle.kts").write_text(SETTINGS_GRADLE.format(name=slug(name)))
    (out / "build.gradle.kts").write_text(ROOT_BUILD_GRADLE.format(agp=AGP_VERSION))
    (out / "gradle.properties").write_text(GRADLE_PROPERTIES)
    (out / "app" / "build.gradle.kts").write_text(APP_BUILD_GRADLE.format(
        package=package, compile_sdk=COMPILE_SDK, min_sdk=MIN_SDK,
        version_code=args.version_code, version_name=args.version,
        signing=signing, release_signing=release_signing))
    (out / ".gitignore").write_text(".gradle/\nbuild/\napp/build/\nlocal.properties\n*.keystore\n")

    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        (out / "local.properties").write_text(f"sdk.dir={Path(sdk).as_posix()}\n")

    n_files = sum(1 for _ in www.rglob("*") if _.is_file())
    print(f"Generated Android project for '{name}' ({package})")
    print(f"  {n_files} web file(s), opens {entry}")
    print(f"  project: {out}")
    return out, name


def build(out, name, release):
    gradle = shutil.which("gradle")
    if not gradle:
        die("gradle not found on PATH. Install Gradle 8.9+ or open the project in Android Studio.")
    if not (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
            or (out / "local.properties").exists()):
        die("Android SDK not found. Set ANDROID_HOME (Android Studio installs it to "
            "~/Android/Sdk on Linux, ~/Library/Android/sdk on macOS, %LOCALAPPDATA%\\Android\\Sdk on Windows).")
    task = "assembleRelease" if release else "assembleDebug"
    print(f"Building ({task})... the first build downloads Android tools and can take a few minutes.")
    r = subprocess.run([gradle, task, "--no-daemon", "-q"], cwd=out)
    if r.returncode != 0:
        die("build failed - see the Gradle output above")
    kind = "release" if release else "debug"
    apks = list((out / "app" / "build" / "outputs" / "apk" / kind).glob("*.apk"))
    if not apks:
        die("build finished but no APK was found")
    final = out.parent / f"{slug(name)}-{kind}.apk"
    shutil.copy2(apks[0], final)
    print(f"\nAPK ready: {final}")
    if not release:
        print("This is a debug-signed APK: fine for installing on your own phones. "
              "For the Play Store use --release with --keystore.")


def main():
    p = argparse.ArgumentParser(
        description="Turn an HTML file or folder into an Android APK (WebView wrapper).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", help="an .html file, or a folder containing your site")
    p.add_argument("--name", help="app name shown under the icon (default: from file name)")
    p.add_argument("--package", help="unique app id, e.g. com.yourname.myapp")
    p.add_argument("--entry", help="page to open first when input is a folder (default: index.html)")
    p.add_argument("--icon", help="square PNG/WebP/JPG app icon (512x512 recommended)")
    p.add_argument("--color", default="#3B5BDB", help="background color of the default icon")
    p.add_argument("--background", default="#FFFFFF", help="WebView background color while loading")
    p.add_argument("--version", default="1.0", help="version name")
    p.add_argument("--version-code", type=int, default=1, help="integer version, bump for each update")
    p.add_argument("--orientation", default="unspecified",
                   choices=["unspecified", "portrait", "landscape", "sensor", "fullSensor"])
    p.add_argument("--fullscreen", action="store_true", help="hide the status bar")
    p.add_argument("--keep-awake", action="store_true", help="keep the screen on (games, kiosks)")
    p.add_argument("--zoom", action="store_true", help="allow pinch-to-zoom")
    p.add_argument("--links-in-app", action="store_true",
                   help="open http(s) links inside the app instead of the phone's browser")
    p.add_argument("--permission", action="append",
                   help="extra Android permission, e.g. CAMERA or VIBRATE (repeatable)")
    p.add_argument("--include-siblings", action="store_true",
                   help="with a single .html input, also bundle the other files in its folder")
    p.add_argument("--out", help="where to write the Android project")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    p.add_argument("--build", action="store_true", help="build the APK after generating")
    p.add_argument("--release", action="store_true", help="build a release APK (needs --keystore)")
    p.add_argument("--keystore", help="keystore for release signing (passwords via KEYSTORE_PASSWORD env)")
    p.add_argument("--key-alias", default="upload", help="key alias inside the keystore")
    args = p.parse_args()

    if args.release and not args.keystore:
        die("--release needs --keystore (or drop --release to get an installable debug APK)")
    for c in (args.color, args.background):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", c):
            die(f"color '{c}' must look like #RRGGBB")

    out, name = generate(args)
    if args.build or args.release:
        build(out, name, args.release)
    else:
        print("\nNext: re-run with --build, or open the project folder in Android Studio and press Run.")


if __name__ == "__main__":
    main()
