package com.erkin.hybridp2p;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.WindowInsets;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * static/serverless.html'i uygulamanın içinden açar. Sayfa, paketteki dosyadan
 * https://appassets.androidplatform.net/serverless.html adresiyle sunulur: https kökeni
 * "güvenli bağlam" sayıldığı için şifreleme API'si (crypto.subtle), kamera ve mikrofon
 * çalışır. İnternetten hiçbir şey yüklenmez; sayfanın CSP'si de WebRTC dışındaki her
 * ağ isteğini yasaklar. Başka bir adrese gitmek engellenir.
 */
public class MainActivity extends Activity {

    static final String ORIGIN_HOST = "appassets.androidplatform.net";
    static final String PAGE_URL = "https://" + ORIGIN_HOST + "/serverless.html";

    private static final int REQ_MEDIA = 1;
    private static final int REQ_FILE = 2;

    private WebView web;
    private PermissionRequest pendingPermission;
    private ValueCallback<Uri[]> pendingFiles;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Debug derlemede WebView'i bilgisayardan incelemek mümkün olsun (chrome://inspect)
        if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) WebView.setWebContentsDebuggingEnabled(true);
        web = new WebView(this);
        // Android 15+ uygulamayı kenardan kenara çizer: sistem çubukları ve klavye kadar boşluk bırak
        FrameLayout root = new FrameLayout(this);
        root.addView(web);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets i = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime()
                        | WindowInsets.Type.displayCutout());
                v.setPadding(i.left, i.top, i.right, i.bottom);
                return WindowInsets.CONSUMED;
            }
            v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        root.setBackgroundColor(0xFF09090B);           // sayfanın koyu zemini (çubukların arkası)
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);                  // IndexedDB ("Bu cihazda hatırla")
        s.setMediaPlaybackRequiresUserGesture(false);  // karşı tarafın sesi/görüntüsü kendiliğinden çalsın
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (ORIGIN_HOST.equals(u.getHost()) && "/serverless.html".equals(u.getPath())) {
                    try {
                        return new WebResourceResponse("text/html", "utf-8", getAssets().open("serverless.html"));
                    } catch (IOException e) {
                        return notFound();
                    }
                }
                return notFound();                     // paket dışından hiçbir şey yüklenmez
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return !PAGE_URL.equals(req.getUrl().toString());   // başka sayfaya gidilmez
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                if (!ORIGIN_HOST.equals(request.getOrigin().getHost())) {
                    request.deny();
                    return;
                }
                List<String> needed = new ArrayList<>();
                for (String r : request.getResources()) {
                    if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r)) addIfMissing(needed, Manifest.permission.CAMERA);
                    if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) addIfMissing(needed, Manifest.permission.RECORD_AUDIO);
                }
                if (needed.isEmpty()) {
                    request.grant(request.getResources());
                } else {
                    pendingPermission = request;
                    requestPermissions(needed.toArray(new String[0]), REQ_MEDIA);
                }
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFiles != null) pendingFiles.onReceiveValue(null);
                pendingFiles = callback;
                try {
                    startActivityForResult(params.createIntent(), REQ_FILE);
                } catch (Exception e) {
                    pendingFiles = null;
                    return false;
                }
                return true;
            }
        });

        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else web.loadUrl(PAGE_URL);
    }

    private static WebResourceResponse notFound() {
        WebResourceResponse r = new WebResourceResponse("text/plain", "utf-8", new ByteArrayInputStream(new byte[0]));
        r.setStatusCodeAndReasonPhrase(404, "Not Found");
        return r;
    }

    private void addIfMissing(List<String> list, String perm) {
        if (checkSelfPermission(perm) != PackageManager.PERMISSION_GRANTED) list.add(perm);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code != REQ_MEDIA || pendingPermission == null) return;
        boolean all = true;
        for (int r : results) all &= r == PackageManager.PERMISSION_GRANTED;
        // Reddedilirse sayfa getUserMedia hatasını ("izin verilmedi") kendisi gösterir
        if (all) pendingPermission.grant(pendingPermission.getResources());
        else pendingPermission.deny();
        pendingPermission = null;
    }

    @Override
    protected void onActivityResult(int code, int result, Intent data) {
        if (code == REQ_FILE && pendingFiles != null) {
            pendingFiles.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            pendingFiles = null;
            return;
        }
        super.onActivityResult(code, result, data);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onBackPressed() {
        // Geri tuşu bağlantıyı yanlışlıkla kapatmasın: uygulamayı arka plana al
        moveTaskToBack(true);
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
