package com.erkin.hybridp2p;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;

import java.io.OutputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Sayfaya (serverless.html) window.HybridP2PAndroid olarak açılan köprü: WebView'in
 * kendi başına yapamadıklarını Android'e yaptırır. Sayfa köprüyü yalnızca varsa kullanır;
 * tarayıcıda aynı sayfa kendi yollarıyla (indirme bağlantısı, navigator.share …) çalışır.
 *
 * Köprü yalnızca paketten sunulan sayfaya açıktır: MainActivity başka hiçbir adresi
 * yüklemez ve sayfanın CSP'si dış betiği yasaklar.
 */
public class AndroidBridge {

    private static final String SAVE_DIR = Environment.DIRECTORY_DOWNLOADS + "/HybridP2P";

    private final MainActivity activity;
    private final Map<String, Uri> saving = new HashMap<>();
    private final Map<String, OutputStream> streams = new HashMap<>();

    AndroidBridge(MainActivity activity) {
        this.activity = activity;
    }

    @JavascriptInterface
    public int version() {
        return 1;
    }

    // ── pano ve paylaşım ──
    @JavascriptInterface
    public void copy(String text) {
        ClipboardManager cm = (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText("HybridP2P", text));
    }

    @JavascriptInterface
    public String paste() {
        ClipboardManager cm = (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
        ClipData clip = cm.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) return "";
        CharSequence t = clip.getItemAt(0).coerceToText(activity);
        return t == null ? "" : t.toString();
    }

    @JavascriptInterface
    public void share(String text) {
        activity.runOnUiThread(() -> {
            Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text);
            activity.startActivity(Intent.createChooser(send, "Bağlantı kodunu gönder"));
        });
    }

    // ── QR okuma (ZXing; Google servisi gerekmez). Sonuç sayfaya geri çağrıyla döner ──
    @JavascriptInterface
    public void scanQr() {
        activity.runOnUiThread(activity::startQrScan);
    }

    // ── arama sırasında ekran kapanmasın ──
    @JavascriptInterface
    public void keepScreenOn(boolean on) {
        activity.runOnUiThread(() -> {
            if (on) activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else activity.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        });
    }

    // ── alınan dosyayı İndirilenler/HybridP2P'ye parça parça kaydetme ──
    // WebView "blob:" bağlantısını indiremez; sayfa dosyayı base64 parçalar hâlinde verir.
    @JavascriptInterface
    public String saveBegin(String name) {
        try {
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.DISPLAY_NAME, name);    // sayfa adı zaten temizledi (safeFilename)
            v.put(MediaStore.Downloads.RELATIVE_PATH, SAVE_DIR);
            v.put(MediaStore.Downloads.IS_PENDING, 1);
            ContentResolver cr = activity.getContentResolver();
            Uri uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (uri == null) return "";
            String id = UUID.randomUUID().toString();
            synchronized (this) {
                saving.put(id, uri);
                streams.put(id, cr.openOutputStream(uri));
            }
            return id;
        } catch (Exception e) {
            return "";
        }
    }

    @JavascriptInterface
    public boolean saveChunk(String id, String base64) {
        OutputStream out;
        synchronized (this) { out = streams.get(id); }
        if (out == null) return false;
        try {
            out.write(Base64.decode(base64, Base64.DEFAULT));
            return true;
        } catch (Exception e) {
            saveAbort(id);
            return false;
        }
    }

    /** Başarılıysa kullanıcıya gösterilecek konum, değilse "". */
    @JavascriptInterface
    public String saveEnd(String id) {
        Uri uri;
        OutputStream out;
        synchronized (this) { uri = saving.remove(id); out = streams.remove(id); }
        if (uri == null || out == null) return "";
        try {
            out.close();
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.IS_PENDING, 0);
            activity.getContentResolver().update(uri, v, null, null);
            return "İndirilenler/HybridP2P";
        } catch (Exception e) {
            activity.getContentResolver().delete(uri, null, null);
            return "";
        }
    }

    @JavascriptInterface
    public void saveAbort(String id) {
        Uri uri;
        OutputStream out;
        synchronized (this) { uri = saving.remove(id); out = streams.remove(id); }
        try { if (out != null) out.close(); } catch (Exception ignored) { }
        if (uri != null) activity.getContentResolver().delete(uri, null, null);   // yarım dosya kalmasın
    }
}
