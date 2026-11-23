package com.example.heart_radio

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject

/** Local sleep diagnostics. Interactive modes use Agent permission; offline listening has its own service. */
internal object SleepProbe {
    private const val BATTERY_FLOOR = 10
    private const val FALLBACK_MS = 20 * 60_000L
    private const val SLEEP_TRANSITION = 485
    private const val SLEEP_FLAG = NativeSleepState.MESSAGE_ID
    enum class Mode(val logName: String, val durationMs: Long) {
        SMOKE("smoke", 90_000L),
        OVERNIGHT("sleep", 10 * 60 * 60_000L),
        WORKING_STATE("working_state", 2 * 60 * 60_000L),
        OFFLINE_LISTEN("offline_listen", 9 * 60 * 60_000L)
    }
    private val handler = Handler(Looper.getMainLooper())
    private var active: Session? = null
    var status = "尚未开始睡眠探测"
        private set

    fun running() = active != null

    fun offlineRunning() = active?.mode == Mode.OFFLINE_LISTEN

    fun start(context: Context, mode: Mode): Boolean {
        if (running()) return false
        val blocked = when {
            !NativeHealth.supported() -> "此手表固件未验证"
            mode != Mode.OFFLINE_LISTEN && (!ConnectionService.running() || !AgentInteraction.enabled()) -> "请先开启连接和 Agent 交互"
            else -> null
        }
        if (blocked != null) { status = blocked; return false }
        status = "正在启动探测…"
        val session = Session(context.applicationContext, mode)
        active = session
        try { session.start() }
        catch (error: Exception) { stop("probe_error", error.javaClass.simpleName) }
        return active === session
    }

    fun startOffline(context: Context) {
        if (!running()) context.startForegroundService(Intent(context, OfflineService::class.java))
    }

    fun stopInteractive(reason: String) {
        if (active?.mode != Mode.OFFLINE_LISTEN) stop(reason)
    }

    fun stop(reason: String = "user_stop", error: String? = null) {
        val session = active ?: return
        active = null
        try { session.close(reason, error) }
        finally { session.stopOfflineService() }
        status = when (reason) {
            "interaction_disabled" -> "交互已关闭 · 探测结束"
            "connection_stopped" -> "连接服务已停止 · 探测结束"
            "battery_floor" -> "电量已达 $BATTERY_FLOOR% 停测线 · 探测结束"
            "charger_connected" -> "已接入充电 · 探测结束"
            "start_while_charging" -> "请先离开充电座"
            "probe_error" -> "探测失败 · ${error.orEmpty()}"
            else -> "探测已结束 · 查询 ${session.samples} 次 · ${session.pushStatus()}"
        }
    }

    class OfflineService : Service() {
        override fun onBind(intent: Intent?) = null

        override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
            if (intent?.action == "STOP") {
                if (active?.mode == Mode.OFFLINE_LISTEN) stop()
                stopSelf(startId)
                return START_NOT_STICKY
            }
            if (active?.mode == Mode.OFFLINE_LISTEN) return START_NOT_STICKY
            val channel = "sleep_offline"
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(channel, "离线睡眠监听", NotificationManager.IMPORTANCE_LOW))
            val open = PendingIntent.getActivity(this, 75, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
            val stop = PendingIntent.getService(this, 76, Intent(this, OfflineService::class.java).setAction("STOP"), PendingIntent.FLAG_IMMUTABLE)
            startForeground(6, Notification.Builder(this, channel)
                .setSmallIcon(android.R.drawable.ic_menu_recent_history).setContentTitle("离线睡眠监听")
                .setContentText("原厂推送 · 本机记录").setContentIntent(open).setOngoing(true)
                .addAction(Notification.Action.Builder(null, "停止并保存", stop).build()).build())
            if (!start(this, Mode.OFFLINE_LISTEN)) stopSelf(startId)
            return START_NOT_STICKY
        }

        override fun onDestroy() {
            if (active?.mode == Mode.OFFLINE_LISTEN) stop("service_stopped")
            stopForeground(STOP_FOREGROUND_REMOVE)
            super.onDestroy()
        }
    }

    class AlarmReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            active?.let { if (it.id == intent.getStringExtra("session")) it.onAlarm() }
        }
    }

    private class Session(private val app: Context, val mode: Mode) {
        val id = java.util.UUID.randomUUID().toString()
        private val started = SystemClock.elapsedRealtime()
        private val short = mode == Mode.SMOKE
        private val offline = mode == Mode.OFFLINE_LISTEN
        private val deadline = started + mode.durationMs
        private val interval = if (short) 15_000L else 4 * 60_000L
        private var nextPoll = started
        private var wakeDue = 0L
        private var lastAttempt = started - 30_000
        private var pending = false
        private var batteryRegistered = false
        private var lastBatteryPercent = -1
        private var listener: AutoCloseable? = null
        private var flagListener: AutoCloseable? = null
        private var wakeAlarm: PendingIntent? = null
        private var releaseWorkingState: (() -> Unit)? = null
        var samples = 0
            private set
        private var pushes = 0
        private var flagPushes = 0
        private val tick = Runnable { if (offline) stop("duration_limit") else sample("timer", nextPoll) }
        private val batteryReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (active !== this@Session) return
                val level = battery(intent)
                if (!checkBattery(level, "charger_connected")) return
                val percent = level.optInt("percent", -1)
                if (offline && percent >= 0 && percent != lastBatteryPercent) {
                    lastBatteryPercent = percent
                    log("battery", mapOf("battery" to level))
                }
            }
        }

        fun start() {
            val battery = battery()
            log("start", mapOf("mode" to mode.logName, "battery" to battery,
                "periodMs" to if (offline) 0L else interval, "deadlineElapsedMs" to deadline,
                "batteryFloorPercent" to BATTERY_FLOOR,
                "pid" to android.os.Process.myPid()))
            if (!checkBattery(battery, "start_while_charging")) return
            lastBatteryPercent = battery.optInt("percent", -1)
            app.registerReceiver(batteryReceiver, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            batteryRegistered = true
            listener = listen(SLEEP_TRANSITION)
            flagListener = try { listen(SLEEP_FLAG) }
            catch (error: Exception) {
                log("listeners", mapOf("messageId" to SLEEP_FLAG, "registered" to false,
                    "error" to error.javaClass.simpleName))
                null
            }
            if (offline) {
                check(armWake(deadline)) { "无法安排停测闹钟" }
                handler.postDelayed(tick, (deadline - SystemClock.elapsedRealtime()).coerceAtLeast(0))
                enterWorkingState()
            } else {
                if (mode == Mode.WORKING_STATE) enterWorkingState()
                if (short) armWake(started + 45_000)
                sample("timer", nextPoll)
            }
        }

        private fun listen(messageId: Int): AutoCloseable {
            val receive: (Long, Long, JSONObject) -> Unit = { wall, elapsed, packet ->
                handler.post {
                    if (active !== this || elapsed >= deadline || !allowed()) return@post
                    if (messageId == SLEEP_FLAG) flagPushes++ else pushes++
                    log("push", mapOf("messageId" to messageId, "receivedAtMs" to wall,
                        "receivedElapsedMs" to elapsed, "reply" to packet))
                    updateStatus()
                    if (messageId == SLEEP_TRANSITION && !offline &&
                        SystemClock.elapsedRealtime() - lastAttempt >= 30_000) sample("push", elapsed)
                }
            }
            val subscription = if (messageId == SLEEP_FLAG) NativeSleepState.observe(app, receive)
                else NativeHealth.listenSleepPush(app, messageId, receive)
            log("listeners", mapOf("messageId" to messageId, "registered" to true))
            return subscription
        }

        private fun allowed(): Boolean {
            if (offline) return active === this
            if (!AgentInteraction.enabled()) stop("interaction_disabled")
            else if (!ConnectionService.running()) stop("connection_stopped")
            return active === this
        }

        private fun sample(trigger: String, due: Long) {
            if (active !== this || !allowed()) return
            val now = SystemClock.elapsedRealtime()
            if (now >= deadline) { stop("duration_limit"); return }
            val skipped = if (now >= nextPoll) (now - nextPoll) / interval else 0L
            if (now >= nextPoll) nextPoll += (skipped + 1) * interval
            handler.removeCallbacks(tick)
            handler.postDelayed(tick, minOf(nextPoll, deadline) - now)
            if (pending) return
            lastAttempt = now
            pending = true
            if (!short) armWake(minOf(now + FALLBACK_MS, deadline))
            NativeHealth.sampleSleep(app) { result ->
                pending = false
                if (active !== this) return@sampleSleep
                if (result.optString("status") == "ok") samples++
                log("sample", mapOf("trigger" to trigger, "dueElapsedMs" to due, "startedElapsedMs" to now,
                    "skippedIntervals" to skipped, "result" to result, "battery" to battery()))
                updateStatus()
            }
        }

        fun onAlarm() {
            if (wakeDue == 0L || SystemClock.elapsedRealtime() < wakeDue) return
            val due = wakeDue
            wakeDue = 0L
            log("wake_alarm", mapOf("dueElapsedMs" to due))
            if (offline) { stop("duration_limit"); return }
            if (!pending && (SystemClock.elapsedRealtime() >= deadline || SystemClock.elapsedRealtime() - lastAttempt >= 5_000)) {
                sample("wake_alarm", due)
            }
        }

        private fun armWake(due: Long): Boolean =
            try {
                val alarm = wakeAlarm ?: PendingIntent.getBroadcast(app, 74,
                    Intent(app, AlarmReceiver::class.java).putExtra("session", id),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT).also { wakeAlarm = it }
                app.getSystemService(AlarmManager::class.java).setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, due, alarm)
                wakeDue = due
                true
            } catch (error: Exception) {
                log("wake_alarm_error", mapOf("error" to error.javaClass.simpleName))
                false
            }

        private fun battery(intent: Intent? = app.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))): JSONObject {
            val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            val counter = app.getSystemService(BatteryManager::class.java).getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER)
            return JSONObject().put("plugged", intent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1) ?: -1)
                .put("percent", if (level >= 0 && scale > 0) level * 100 / scale else JSONObject.NULL)
                .put("chargeMicroAh", if (counter > 0) counter else JSONObject.NULL)
        }

        private fun checkBattery(battery: JSONObject, chargerReason: String): Boolean {
            if (battery.optInt("percent", -1) in 0..BATTERY_FLOOR) stop("battery_floor")
            else if (!short && battery.optInt("plugged") > 0) stop(chargerReason)
            return active === this
        }

        private fun enterWorkingState() {
            val manager = Class.forName("android.os.BmManager")
            val read = manager.getMethod("getStandardState")
            val write = manager.getMethod("setStandardState", Boolean::class.javaPrimitiveType)
            check(read.invoke(null) == false) { "工作状态已被占用" }
            releaseWorkingState = {
                write.invoke(null, false)
                check(read.invoke(null) == false) { "工作状态未释放" }
            }
            write.invoke(null, true)
            check(read.invoke(null) == true) { "系统未接受工作状态" }
            log("working_state", mapOf("enabled" to true))
            updateStatus()
        }

        fun pushStatus(): String = "485 $pushes 次 · " +
            if (flagListener == null) "490 未注册" else "490 $flagPushes 次"

        private fun updateStatus() {
            if (offline) {
                status = "离线监听中 · 工作状态已开启 · ${pushStatus()}"
                return
            }
            val label = if (mode == Mode.WORKING_STATE) "工作状态已开启" else "探测中"
            status = "$label · 查询 $samples 次 · ${pushStatus()}"
        }

        private fun log(event: String, fields: Map<String, Any?>) {
            Probe.log(app, "sleep_probe_$event", fields + mapOf("probeId" to id, "uptimeMs" to SystemClock.uptimeMillis()))
        }

        fun stopOfflineService() {
            if (offline) app.stopService(Intent(app, OfflineService::class.java))
        }

        fun close(reason: String, error: String?) {
            handler.removeCallbacks(tick)
            releaseWorkingState?.let { release ->
                val failure = runCatching(release).exceptionOrNull()
                log("working_state", mapOf("enabled" to if (failure == null) false else null,
                    "error" to failure?.javaClass?.simpleName))
            }
            wakeAlarm?.let {
                runCatching { app.getSystemService(AlarmManager::class.java).cancel(it) }
                it.cancel()
            }
            if (batteryRegistered) runCatching { app.unregisterReceiver(batteryReceiver) }
            if (pending) NativeHealth.cancel()
            val listenerError = runCatching { listener?.close() }.exceptionOrNull()
            val flagListenerError = runCatching { flagListener?.close() }.exceptionOrNull()
            log("stop", mapOf("reason" to reason, "error" to error, "listenerError" to listenerError?.javaClass?.simpleName,
                "flagListenerError" to flagListenerError?.javaClass?.simpleName,
                "samples" to samples, "pushes" to pushes, "flagPushes" to flagPushes, "battery" to battery()))
        }
    }
}
