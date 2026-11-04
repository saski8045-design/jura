import os

def W(p, s):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(s)

W('settings.gradle', r'''pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "Jura"
include ":app"
''')

W('build.gradle', r'''plugins {
    id 'com.android.application' version '8.5.2' apply false
    id 'org.jetbrains.kotlin.android' version '1.9.24' apply false
}
''')

W('gradle.properties', r'''org.gradle.jvmargs=-Xmx2048m
android.useAndroidX=true
''')

W('app/build.gradle', r'''plugins {
    id 'com.android.application'
    id 'org.jetbrains.kotlin.android'
}
android {
    namespace 'com.jura.app'
    compileSdk 34
    defaultConfig {
        applicationId 'com.jura.app'
        minSdk 26
        targetSdk 34
        versionCode 1
        versionName '1.0'
    }
    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = '17' }
}
''')

W('app/src/main/AndroidManifest.xml', r'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.RECORD_AUDIO"/>
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE"/>
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
    <uses-permission android:name="android.permission.CALL_PHONE"/>
    <uses-permission android:name="android.permission.READ_CONTACTS"/>
    <uses-permission android:name="com.android.alarm.permission.SET_ALARM"/>
    <uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW"/>
    <uses-permission android:name="android.permission.QUERY_ALL_PACKAGES"/>
    <uses-permission android:name="android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS"/>
    <queries>
        <intent><action android:name="android.speech.RecognitionService"/></intent>
        <intent><action android:name="android.intent.action.TTS_SERVICE"/></intent>
    </queries>
    <application android:label="Jura">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
        <service android:name=".JuraService" android:exported="false" android:foregroundServiceType="microphone"/>
    </application>
</manifest>
''')

W('app/src/main/java/com/jura/app/MainActivity.kt', r'''package com.jura.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.*

class MainActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        val sp = getSharedPreferences("jura", MODE_PRIVATE)
        val l = LinearLayout(this)
        l.orientation = LinearLayout.VERTICAL
        l.setPadding(48, 120, 48, 48)
        val t = TextView(this); t.text = "Jura"; t.textSize = 40f
        val key = EditText(this); key.hint = "API-ключ (Gemini или Claude)"; key.setText(sp.getString("key", ""))
        val perms = Button(this); perms.text = "1. Разрешения"
        val over = Button(this); over.text = "2. Показ поверх других окон"
        val bat = Button(this); bat.text = "3. Не отключать в фоне"
        val go = Button(this); go.text = "4. Запустить Джуру"
        val stop = Button(this); stop.text = "Остановить"
        listOf(t, key, perms, over, bat, go, stop).forEach { l.addView(it) }
        setContentView(l)

        perms.setOnClickListener {
            requestPermissions(arrayOf(
                Manifest.permission.RECORD_AUDIO,
                "android.permission.POST_NOTIFICATIONS",
                Manifest.permission.CALL_PHONE,
                Manifest.permission.READ_CONTACTS), 1)
        }
        over.setOnClickListener {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        bat.setOnClickListener {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        }
        go.setOnClickListener {
            sp.edit().putString("key", key.text.toString().trim()).apply()
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "Сначала нажми «1. Разрешения»", Toast.LENGTH_LONG).show()
            } else {
                startForegroundService(Intent(this, JuraService::class.java))
            }
        }
        stop.setOnClickListener { stopService(Intent(this, JuraService::class.java)) }
    }
}
''')

W('app/src/main/java/com/jura/app/JuraService.kt', r'''package com.jura.app

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.*
import android.provider.AlarmClock
import android.provider.ContactsContract
import android.speech.*
import android.speech.tts.*
import org.json.*
import java.net.*
import java.util.*

class JuraService : Service(), TextToSpeech.OnInitListener {
    lateinit var tts: TextToSpeech
    lateinit var sp: SharedPreferences
    lateinit var history: JSONArray
    var rec: SpeechRecognizer? = null
    val h = Handler(Looper.getMainLooper())
    var speaking = false
    var busy = false
    var running = true
    var awakeUntil = 0L

    override fun onBind(i: Intent?): IBinder? = null
    override fun onStartCommand(i: Intent?, f: Int, id: Int): Int = START_STICKY

    override fun onCreate() {
        super.onCreate()
        sp = getSharedPreferences("jura", MODE_PRIVATE)
        history = try { JSONArray(sp.getString("hist", "[]")) } catch (e: Exception) { JSONArray() }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("j", "Jura", NotificationManager.IMPORTANCE_LOW))
        val n = Notification.Builder(this, "j")
            .setContentTitle("Джура слушает")
            .setContentText("Скажи «Джура»")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        else startForeground(1, n)
        tts = TextToSpeech(this, this)
    }

    override fun onDestroy() {
        running = false
        rec?.destroy()
        tts.shutdown()
        super.onDestroy()
    }

    override fun onInit(s: Int) {
        if (s != TextToSpeech.SUCCESS) return
        tts.language = Locale("ru", "RU")
        val vs = tts.voices?.filter { it.locale.language == "ru" } ?: listOf()
        val m = vs.firstOrNull {
            val n = it.name.lowercase()
            !n.contains("female") && (n.contains("male") || n.contains("-rud-") || n.contains("-ruf-"))
        } ?: vs.firstOrNull()
        if (m != null) tts.voice = m
        tts.setPitch(0.7f)
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(u: String?) {}
            override fun onError(u: String?) { h.post { done() } }
            override fun onDone(u: String?) { h.post { done() } }
        })
        say("Привет! Я Джура. Чем могу помочь?")
    }

    fun done() {
        speaking = false
        awakeUntil = System.currentTimeMillis() + 15000
        listen()
    }

    fun say(t: String) {
        speaking = true
        tts.speak(t, TextToSpeech.QUEUE_FLUSH, null, "u")
    }

    fun listen() {
        if (!running || speaking || busy) return
        rec?.destroy()
        if (!SpeechRecognizer.isRecognitionAvailable(this)) return
        val r = SpeechRecognizer.createSpeechRecognizer(this)
        rec = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onResults(b: Bundle?) {
                val t = b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull() ?: ""
                handle(t)
            }
            override fun onError(e: Int) { h.postDelayed({ listen() }, 700) }
            override fun onReadyForSpeech(p: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(v: Float) {}
            override fun onBufferReceived(b: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(b: Bundle?) {}
            override fun onEvent(t: Int, b: Bundle?) {}
        })
        val i = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ru-RU")
        r.startListening(i)
    }

    fun handle(t: String) {
        val wake = Regex("джур|jura|жур|юр", RegexOption.IGNORE_CASE).containsMatchIn(t)
        if (t.isBlank() || (!wake && System.currentTimeMillis() > awakeUntil)) { listen(); return }
        val q = t.replace(Regex("^(эй|привет|слушай)?[, ]*(джура|жура|юра|jura)[, ]*", RegexOption.IGNORE_CASE), "")
            .trim().ifEmpty { "Привет" }
        busy = true
        Thread {
            val a = try { ask(q) } catch (e: Exception) { "Не получилось связаться с интернетом." }
            h.post { busy = false; reply(a) }
        }.start()
    }

    fun ask(q: String): String {
        history.put(JSONObject().put("role", "user").put("content", q))
        while (history.length() > 20 || (history.length() > 0 && history.getJSONObject(0).getString("role") != "user")) {
            history.remove(0)
        }
        val now = java.text.SimpleDateFormat("EEEE, d MMMM yyyy, HH:mm", Locale("ru")).format(Date())
        val facts = sp.getString("facts", "") ?: ""
        val sys = "Тебя зовут Джура, ты мужской голосовой помощник на телефоне. " +
            "Отвечай по-русски, коротко (1-3 предложения), без markdown и эмодзи: ответ озвучивается. " +
            "Сейчас: " + now + ". Что ты знаешь о пользователе: " + facts + "\n" +
            "Если пользователь просит действие, добавь в конце ответа команду в квадратных скобках: " +
            "[CALL:имя контакта в именительном падеже], [ALARM:ЧЧ:ММ], [TIMER:секунды], " +
            "[OPEN:название приложения], [REMEMBER:короткий факт о пользователе]. " +
            "Добавляй команду только если пользователь её просил или сообщил важный факт о себе."
        val key = (sp.getString("key", "") ?: "").trim()
        val claude = key.startsWith("sk-ant-")
        val body = JSONObject()
        if (claude) {
            body.put("model", "claude-haiku-5-5").put("max_tokens", 400).put("system", sys).put("messages", history)
        } else {
            val cs = JSONArray()
            for (i in 0 until history.length()) {
                val m = history.getJSONObject(i)
                val role = if (m.getString("role") == "user") "user" else "model"
                cs.put(JSONObject().put("role", role).put("parts", JSONArray().put(JSONObject().put("text", m.getString("content")))))
            }
            body.put("system_instruction", JSONObject().put("parts", JSONArray().put(JSONObject().put("text", sys))))
            body.put("contents", cs)
            body.put("generationConfig", JSONObject().put("maxOutputTokens", 600).put("thinkingConfig", JSONObject().put("thinkingBudget", 0)))
        }
        val url = if (claude) "https://api.anthropic.com/v1/messages" else "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        val c = URL(url).openConnection() as HttpURLConnection
        c.requestMethod = "POST"
        c.doOutput = true
        c.connectTimeout = 15000
        c.readTimeout = 30000
        c.setRequestProperty("content-type", "application/json")
        if (claude) {
            c.setRequestProperty("x-api-key", key)
            c.setRequestProperty("anthropic-version", "2023-06-01")
        } else {
            c.setRequestProperty("x-goog-api-key", key)
        }
        c.outputStream.use { it.write(body.toString().toByteArray()) }
        val code = c.responseCode
        val txt = (if (code in 200..299) c.inputStream else c.errorStream).bufferedReader().readText()
        if (code !in 200..299) {
            history.remove(history.length() - 1)
            return if (code == 401 || code == 403) "Проверь API-ключ." else if (code == 429) "Слишком много запросов, подожди минуту." else "Ошибка " + code
        }
        val a = if (claude) JSONObject(txt).getJSONArray("content").getJSONObject(0).getString("text")
            else JSONObject(txt).getJSONArray("candidates").getJSONObject(0).getJSONObject("content").getJSONArray("parts").getJSONObject(0).getString("text")
        history.put(JSONObject().put("role", "assistant").put("content", a))
        sp.edit().putString("hist", history.toString()).apply()
        return a
    }

    fun reply(a: String) {
        val re = Regex("\\[(CALL|ALARM|TIMER|OPEN|REMEMBER):([^\\]]*)\\]")
        re.findAll(a).forEach { act(it.groupValues[1], it.groupValues[2].trim()) }
        say(a.replace(re, "").trim().ifEmpty { "Готово" })
    }

    fun go(i: Intent) {
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(i)
    }

    fun act(c: String, v: String) {
        try {
            when (c) {
                "CALL" -> {
                    val n = findNumber(v)
                    if (n != null) go(Intent(Intent.ACTION_CALL, Uri.parse("tel:" + Uri.encode(n))))
                }
                "ALARM" -> {
                    val p = v.split(":")
                    go(Intent(AlarmClock.ACTION_SET_ALARM)
                        .putExtra(AlarmClock.EXTRA_HOUR, p[0].trim().toInt())
                        .putExtra(AlarmClock.EXTRA_MINUTES, p[1].trim().toInt())
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true))
                }
                "TIMER" -> {
                    go(Intent(AlarmClock.ACTION_SET_TIMER)
                        .putExtra(AlarmClock.EXTRA_LENGTH, v.trim().toInt())
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true))
                }
                "OPEN" -> {
                    val pm = packageManager
                    val app = pm.getInstalledApplications(0).firstOrNull {
                        pm.getApplicationLabel(it).toString().contains(v, true) &&
                            pm.getLaunchIntentForPackage(it.packageName) != null
                    }
                    if (app != null) go(pm.getLaunchIntentForPackage(app.packageName)!!)
                }
                "REMEMBER" -> {
                    sp.edit().putString("facts", (sp.getString("facts", "") ?: "") + "\n- " + v).apply()
                }
                else -> {}
            }
        } catch (e: Exception) {
        }
    }

    fun findNumber(name: String): String? {
        val c = contentResolver.query(
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.NUMBER),
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " LIKE ?",
            arrayOf("%" + name + "%"), null)
        c?.use { if (it.moveToFirst()) return it.getString(0) }
        return null
    }
}
''')
