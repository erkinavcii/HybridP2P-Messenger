"""android/ — serverless.html'i saran APK. Derleme (Gradle + Android SDK) burada çalıştırılmaz;
sayfa ile uygulama arasındaki sözleşme ve güvenlik ayarları dosyalardan denetlenir."""

import re

from conftest import ROOT

ANDROID = ROOT / "android"
JAVA = ANDROID / "app" / "src" / "main" / "java" / "com" / "erkin" / "hybridp2p"
PAGE = (ROOT / "static" / "serverless.html").read_text(encoding="utf-8")


def _bridge_methods():
    src = (JAVA / "AndroidBridge.java").read_text(encoding="utf-8")
    return set(re.findall(r"@JavascriptInterface\s+public\s+\w+\s+(\w+)\(", src))


def test_every_bridge_call_in_page_exists_in_java():
    used = set(re.findall(r"NATIVE\.(\w+)\(", PAGE))
    assert used, "sayfa köprüyü hiç kullanmıyor"
    assert used <= _bridge_methods(), used - _bridge_methods()
    main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
    assert 'addJavascriptInterface(new AndroidBridge(this), "HybridP2PAndroid")' in main
    assert "window.HybridP2PAndroid" in PAGE and "window.__hp2pScanResult" in main


def test_page_is_served_from_apk_on_a_secure_origin_only():
    main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
    assert 'PAGE_URL = "https://" + ORIGIN_HOST + "/serverless.html"' in main
    assert "return notFound();                     // paket dışından" in main     # başka hiçbir şey yüklenmez
    assert "setAllowFileAccess(false)" in main and "setAllowContentAccess(false)" in main
    # sayfanın kendisi de ağ isteklerini yasaklar
    assert "connect-src 'none'" in PAGE


def test_build_copies_the_single_page_source_and_pins_dependencies():
    gradle = (ANDROID / "app" / "build.gradle").read_text(encoding="utf-8")
    assert 'rootProject.file("../static/serverless.html")' in gradle
    assert re.findall(r'implementation "([^"]+)"', gradle) == ["com.journeyapps:zxing-android-embedded:4.3.0",
                                                                   "androidx.core:core:1.17.0"]
    assert 'applicationId "com.erkin.hybridp2p"' in gradle
    root = (ANDROID / "build.gradle").read_text(encoding="utf-8")
    assert '"com.android.application" version "8.13.2"' in root
    wrapper = (ANDROID / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(encoding="utf-8")
    assert "gradle-8.14.3-all.zip" in wrapper


def test_manifest_permissions_are_minimal():
    manifest = (ANDROID / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    perms = set(re.findall(r'uses-permission android:name="android\.permission\.(\w+)"', manifest))
    assert perms == {"INTERNET", "ACCESS_NETWORK_STATE", "CAMERA", "RECORD_AUDIO", "MODIFY_AUDIO_SETTINGS"}
    assert 'android:allowBackup="false"' in manifest           # kimlik/rehber yedeğe gitmesin


def test_gradlew_keeps_lf_line_endings():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^android/gradlew\s+text eol=lf", attrs, re.M)
