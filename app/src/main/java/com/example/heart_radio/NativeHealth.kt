package com.example.heart_radio

import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.lang.reflect.Proxy
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/** Read-only adapter for the verified OPPO firmware. All MCU requests are serialized. */
internal object NativeHealth {
    private const val FIRMWARE = "OW20W2_11_A.92"
    private const val WINDOW_SECONDS = 86_400L
    private const val MAX_PAGES = 150
    private const val REQUEST_MS = 9_000L
    private const val HEART_HISTORY_REQUEST = 434
    private const val HEART_HISTORY_HEADER = 435
    private const val HEART_HISTORY_PAGE_REQUEST = 436
    private const val HEART_HISTORY_PAGE = 437
    private val handler = Handler(Looper.getMainLooper())
    private var worker: Thread? = null
    private var generation = 0L

    fun supported() = Build.DISPLAY == FIRMWARE && Build.VERSION.SDK_INT == 27 && Build.MODEL == "OW20W2"

    fun execute(context: Context, request: JSONObject, reply: (JSONObject) -> Unit) {
        val id = request.getString("id")
        fun send(value: JSONObject) = reply(value.put("id", id).put("op", request.getString("op")))
        if (!supported()) {
            send(JSONObject().put("status", "unverified_firmware")); return
        }
        val app = context.applicationContext
        val params = request.getJSONObject("params")
        val metric = params.getString("metric")
        if (request.getString("op") == "health_now" && metric == "current_sleep") {
            val snapshot = NativeSleepState.snapshot()
            val device = runCatching { deviceId(app) }.getOrNull()
            if (device == null) send(JSONObject().put("status", "native_read_failed"))
            else send(identify(snapshot, device, metric))
            return
        }
        submit(app, REQUEST_MS, { deadline ->
            val device = deviceId(app)
            val value = try {
                if (request.getString("op") == "health_now") now(app, metric, deadline)
                else history(app, metric, params, deadline)
            } catch (e: Failure) {
                JSONObject().put("status", e.status)
            }
            identify(value, device, metric)
        }) { value ->
            if (metric == "wear" && value.optString("status") == "ok" && value.optInt("rawCode", -1) == 0) {
                NativeSleepState.clearOffWrist()
            }
            send(value)
        }
    }

    @Synchronized private fun deviceId(context: Context): String {
        val prefs = context.getSharedPreferences("native-health", Context.MODE_PRIVATE)
        return prefs.getString("deviceId", null) ?: UUID.randomUUID().toString().also {
            check(prefs.edit().putString("deviceId", it).commit())
        }
    }

    private fun identify(value: JSONObject, device: String, metric: String) =
        value.put("deviceId", device).put("metric", metric).put("firmware", FIRMWARE)
            .put("watchReadAtMs", System.currentTimeMillis())

    fun listenSleepPush(context: Context, messageId: Int, receive: (Long, Long, JSONObject) -> Unit): AutoCloseable =
        Exchange(context, messageId, onPush = { bytes ->
            receive(System.currentTimeMillis(), SystemClock.elapsedRealtime(), probePacket(bytes, stateField = 3))
        })

    fun sampleSleep(context: Context, reply: (JSONObject) -> Unit) =
        submit(context, 4_000, { deadline ->
            fun read(command: Int, response: Int): JSONObject = try {
                probePacket(query(context, command, response, minOf(deadline, SystemClock.elapsedRealtime() + 1800)))
            } catch (e: Failure) {
                JSONObject().put("status", e.status)
            }
            JSONObject().put("status", "ok").put("sleep", read(486, 487)).put("wear", read(408, 405))
        }, reply)

    private fun probePacket(bytes: ByteArray, stateField: Int = 1): JSONObject {
        val value = JSONObject().put("rawBase64", Base64.encodeToString(bytes, Base64.NO_WRAP)).put("bytes", bytes.size)
        return try {
            val fields = decode(bytes).numbers
            value.put("status", "received").put("statePresent", fields.containsKey(stateField))
                .put("rawCode", fields[stateField] ?: 0L).put("fields", JSONObject(fields.mapKeys { it.key.toString() }))
        } catch (e: Failure) {
            value.put("status", e.status)
        }
    }

    private fun submit(context: Context, budget: Long, read: (Long) -> JSONObject, reply: (JSONObject) -> Unit) {
        if (worker != null) { reply(JSONObject().put("status", "busy")); return }
        val token = generation
        worker = Thread({
            val result = try {
                val lock = context.getSystemService(PowerManager::class.java)
                    .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "HeartRadio:native-health")
                lock.acquire(budget + 1500)
                try { read(SystemClock.elapsedRealtime() + budget) }
                finally { if (lock.isHeld) lock.release() }
            } catch (_: InterruptedException) {
                JSONObject().put("status", "cancelled")
            } catch (_: IllegalArgumentException) {
                JSONObject().put("status", "invalid_request")
            } catch (_: Exception) {
                JSONObject().put("status", "native_read_failed")
            }
            handler.post {
                worker = null
                if (token == generation && AgentInteraction.enabled()) reply(result)
                else reply(JSONObject().put("status", "cancelled"))
            }
        }, "heart-radio-native-health").also { it.start() }
    }

    fun cancel() {
        generation++
        worker?.interrupt()
    }

    private class Failure(val status: String) : Exception()

    private fun query(context: Context, command: Int, response: Int, deadline: Long): ByteArray =
        Exchange(context, response).use { bus ->
            bus.send(command, byteArrayOf())
            bus.receiveBytes(response, deadline)
        }

    private fun now(context: Context, metric: String, deadline: Long): JSONObject {
        val (command, response) = when (metric) {
            "activity" -> 320 to 321
            "wear" -> 408 to 405
            else -> throw IllegalArgumentException()
        }
        val packet = decode(query(context, command, response, deadline))
        val result = JSONObject().put("status", "ok")
        if (metric == "activity") {
            result.put("heartHistorySupported", true)
                .put("steps", packet.numbers[2] ?: JSONObject.NULL)
                .put("sourceAtMs", packet.numbers[6]?.times(1000L) ?: JSONObject.NULL)
        } else {
            result.put("rawCode", packet.numbers[1] ?: 0)
                .put("fieldPresent", packet.numbers.containsKey(1))
        }
        return result
    }

    private fun history(context: Context, metric: String, params: JSONObject, deadline: Long): JSONObject {
        require(metric == "activity" || metric == "historical_sleep" || metric == "heart_rate")
        val from = params.getLong("from")
        val to = params.getLong("to")
        val now = System.currentTimeMillis() / 1000
        require(from in (now - 7 * WINDOW_SECONDS - 120)..now && to in from..minOf(now + 60, Int.MAX_VALUE.toLong()))
        require(to - from < WINDOW_SECONDS && from % 60 == 0L && to % 60 == 0L)
        if (metric == "heart_rate") {
            require(to <= now && to + 60 <= Int.MAX_VALUE)
            return heartHistory(context, from, to, deadline)
        }
        val activity = metric == "activity"
        val command = if (activity) 322 else 481
        val result = JSONObject().put("from", from).put("to", to)
        Exchange(context, command + 1, command + 3).use { bus ->
            bus.send(command, encode(1 to from, 2 to to))
            val header = bus.receive(command + 1, deadline) {
                it.numbers[2]?.let { start -> start in from..to } == true
            }
            val count = header.numbers[1] ?: 0
            if (count == 0L) return result.put("status", "no_data")
            if (count !in 1L..MAX_PAGES.toLong()) throw Failure("split_required")
            val steps = linkedMapOf<Long, Long>()
            var cursor = header.numbers.getValue(2)
            var nextSleep = from
            var firstSleep: Long? = null
            var lastSleep: Long? = null
            var sleepMinutes = 0
            val sleepStates = StringBuilder()
            for (index in 1L..count) {
                val request = if (activity) encode(1 to index, 2 to cursor) else encode(1 to index)
                bus.send(command + 2, request)
                val page = bus.receive(command + 3, deadline) { it.numbers[1] == index }
                val base = page.numbers[2] ?: throw Failure("invalid_page")
                if (activity) {
                    for (bytes in page.blobs) {
                        val row = decode(bytes).numbers
                        val offset = row[1] ?: 0
                        if (offset !in 0..1440) throw Failure("invalid_page")
                        cursor = base + 60 * offset
                        val value = row[7] ?: 0
                        if (cursor !in from..to || cursor % 60 != 0L || value !in 0..Int.MAX_VALUE.toLong())
                            throw Failure("invalid_page")
                        if (steps.put(cursor, value)?.let { it != value } == true) throw Failure("invalid_page")
                    }
                    if (steps.size > 650) throw Failure("split_required")
                } else {
                    val states = page.blobs.lastOrNull() ?: throw Failure("invalid_page")
                    if (base != nextSleep || states.isEmpty() || base + (states.size - 1) * 60L > to)
                        throw Failure("incomplete_history")
                    states.forEachIndexed { offset, byte ->
                        val state = byte.toInt() and 7
                        sleepStates.append(state)
                        if (state in 1..3) {
                            val time = base + offset * 60L
                            if (firstSleep == null) firstSleep = time
                            lastSleep = time
                            sleepMinutes++
                        }
                    }
                    nextSleep += states.size * 60L
                }
            }
            result.put("pages", count)
            if (activity) {
                result.put("rows", JSONArray(steps.entries.sortedBy { it.key }.map {
                    JSONArray().put((it.key - from) / 60).put(it.value)
                }))
                if (result.toString().toByteArray().size > 7900) throw Failure("split_required")
                return result.put("status", if (steps.isEmpty()) "no_data" else "ok")
            }
            if (nextSleep != to + 60) throw Failure("incomplete_history")
            val first = firstSleep ?: return result.put("status", "no_data")
            val last = checkNotNull(lastSleep)
            val stages = sleepStates.substring(((first-from)/60).toInt(), ((last-from)/60).toInt()+1)
            return result.put("status", "ok").put("first", first).put("last", last)
                .put("minutes", sleepMinutes).put("decoderVersion", 2).put("minuteStates", stages)
        }
    }

    private fun heartHistory(context: Context, from: Long, to: Long, deadline: Long): JSONObject {
        // 'to' identifies the last minute. Ask through the next boundary, then exclude it.
        val endExclusive = to + 60
        val result = JSONObject().put("from", from).put("to", to)
        Exchange(context, HEART_HISTORY_HEADER, HEART_HISTORY_PAGE).use { bus ->
            bus.send(HEART_HISTORY_REQUEST, encode(1 to from, 2 to endExclusive))
            val header = bus.receive(HEART_HISTORY_HEADER, deadline) { it.numbers[2] == from }
            val count = header.numbers[1] ?: 0
            if (count == 0L) return result.put("status", "no_data")
            if (count !in 1L..MAX_PAGES.toLong()) throw Failure("split_required")
            val samples = sortedMapOf<Long, Pair<Long, Long>>()
            var previousBase = from
            for (index in 1L..count) {
                bus.send(HEART_HISTORY_PAGE_REQUEST, encode(1 to index, 2 to from))
                val page = bus.receive(HEART_HISTORY_PAGE, deadline) {
                    it.numbers[1] == index && it.numbers[2]?.let { base -> base in previousBase..endExclusive } == true
                }
                val base = page.numbers.getValue(2)
                previousBase = base
                for (bytes in page.blobs) {
                    val row = decode(bytes).numbers
                    val offset = row[1] ?: 0
                    val bpm = row[2] ?: 0
                    val quality = row[3] ?: 0
                    if (offset !in 0..WINDOW_SECONDS || bpm !in 0..255 || quality !in 0..255)
                        throw Failure("invalid_page")
                    val timestamp = base + offset
                    if (bpm == 0L || timestamp !in from until endExclusive) continue
                    val sample = bpm to quality
                    if (samples.put(timestamp, sample)?.let { it != sample } == true) throw Failure("invalid_page")
                }
            }
            val rows = JSONArray(samples.map { (timestamp, sample) ->
                JSONArray().put(timestamp - from).put(sample.first).put(sample.second)
            })
            result.put("pages", count).put("rows", rows)
            if (rows.length() > 650 || result.toString().toByteArray().size > 7900) throw Failure("split_required")
            return result.put("status", if (rows.length() == 0) "no_data" else "ok")
        }
    }

    private class Exchange(context: Context, vararg ids: Int, onPush: ((ByteArray) -> Unit)? = null) : AutoCloseable {
        private val api = Class.forName("android.app.wear.McuManager")
        private val listenerType = Class.forName("android.app.wear.DataEventListener")
        private val eventType = Class.forName("android.app.wear.DataEvent")
        private val manager: Any
        private val registered = mutableListOf<Int>()
        private val queues = ids.associateWith { ArrayBlockingQueue<ByteArray>(8) }
        private val listener: Any

        init {
            val eventId = eventType.getField("msgType")
            val eventBytes = eventType.getField("values")
            api.getMethod("GlobalInit", Context::class.java, Int::class.javaPrimitiveType)
                .invoke(null, context, 0)
            manager = api.getMethod("getInstance").invoke(null)!!
            listener = Proxy.newProxyInstance(context.classLoader, arrayOf(listenerType)) { proxy, method, args ->
                when (method.name) {
                    "hashCode" -> System.identityHashCode(proxy)
                    "equals" -> proxy === args?.get(0)
                    "toString" -> "HeartRadio native health"
                    "onDataChanged" -> {
                        val event = args!![0]
                        val bytes = eventBytes.get(event) as? ByteArray
                        if (bytes != null && bytes.size <= 2048) {
                            val id = eventId.getInt(event)
                            queues[id]?.let { queue ->
                                val packet = bytes.copyOf()
                                if (onPush != null) onPush(packet) else queue.offer(packet)
                            }
                        }
                        null
                    }
                    else -> null
                }
            }
            try {
                val register = api.getMethod("registerListener", listenerType, Int::class.javaPrimitiveType)
                for (id in ids) {
                    if (register.invoke(manager, listener, id) != true) throw Failure("registration_failed")
                    registered.add(id)
                }
            } catch (error: Exception) {
                close()
                throw error
            }
        }

        fun send(id: Int, data: ByteArray) {
            if (Thread.currentThread().isInterrupted) throw InterruptedException()
            val method = api.getMethod("sendMessageToMcu", Int::class.javaPrimitiveType, ByteArray::class.java)
            if (method.invoke(manager, id, data) != true) throw Failure("request_rejected")
        }

        fun receiveBytes(id: Int, deadline: Long): ByteArray {
            val queue = queues.getValue(id)
            val remaining = deadline - SystemClock.elapsedRealtime()
            if (remaining <= 0) throw Failure("request_timeout")
            return queue.poll(remaining, TimeUnit.MILLISECONDS) ?: throw Failure("request_timeout")
        }

        fun receive(id: Int, deadline: Long, accept: (Packet) -> Boolean): Packet {
            while (true) {
                val packet = decode(receiveBytes(id, deadline))
                if (accept(packet)) return packet
            }
        }

        override fun close() {
            val unregister = api.getMethod("unregisterListener", listenerType, Int::class.javaPrimitiveType)
            var failure: Exception? = null
            for (id in registered) {
                try { unregister.invoke(manager, listener, id) }
                catch (error: Exception) { failure = error }
            }
            if (failure != null) throw failure
        }
    }

    private data class Packet(val numbers: Map<Int, Long>, val blobs: List<ByteArray>)

    private fun decode(bytes: ByteArray): Packet {
        var offset = 0
        fun varint(): Long {
            var value = 0L
            for (shift in 0 until 64 step 7) {
                if (offset >= bytes.size) throw Failure("invalid_payload")
                val part = bytes[offset++].toInt() and 255
                value = value or ((part and 127).toLong() shl shift)
                if (part < 128) return value
            }
            throw Failure("invalid_payload")
        }
        val numbers = mutableMapOf<Int, Long>()
        val blobs = mutableListOf<ByteArray>()
        while (offset < bytes.size) {
            val tag = varint()
            val field = (tag ushr 3).toInt()
            if (field <= 0) throw Failure("invalid_payload")
            when (tag.toInt() and 7) {
                0 -> numbers[field] = varint()
                2 -> {
                    val size = varint()
                    if (size !in 0..(bytes.size - offset).toLong()) throw Failure("invalid_payload")
                    val end = offset + size.toInt()
                    if (field == 3) blobs.add(bytes.copyOfRange(offset, end))
                    offset = end
                }
                else -> throw Failure("invalid_payload")
            }
        }
        return Packet(numbers, blobs)
    }

    private fun encode(vararg fields: Pair<Int, Long>): ByteArray {
        val out = ByteArrayOutputStream()
        for ((field, original) in fields) {
            out.write(field shl 3)
            var value = original
            while (value >= 128) {
                out.write((value.toInt() and 127) or 128)
                value = value ushr 7
            }
            out.write(value.toInt())
        }
        return out.toByteArray()
    }
}
