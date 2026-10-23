package com.example.heart_radio

/** Observation of one explicit capture attempt; never schedules or retries a sensor. */
class HeartCaptureDiagnostics {
    companion object { const val FIRST_CALLBACK_TIMEOUT_MS = 90_000L }
    data class Snapshot(
        val state: String, val message: String, val attemptId: String?,
        val elapsedMs: Long?, val firstCallbackAfterMs: Long?, val firstValidAfterMs: Long?,
        val lastCallbackAgeMs: Long?, val lastValidReceiptAgeMs: Long?,
        val firstCallbackTimedOut: Boolean, val stopReason: String?, val failureReason: String?
    )
    private var attemptId: String? = null
    private var startedNs = 0L
    private var stoppedNs: Long? = null
    private var firstCallbackNs: Long? = null
    private var firstValidNs: Long? = null
    private var wasRegistered = false
    private var stopReason: String? = null
    private var failureReason: String? = null

    fun begin(id: String, nowNs: Long) {
        attemptId=id; startedNs=nowNs; stoppedNs=null; firstCallbackNs=null; firstValidNs=null
        wasRegistered=false; stopReason=null; failureReason=null
    }
    fun registered() { wasRegistered=true }
    fun callback(nowNs: Long, valid: Boolean) {
        if (attemptId==null || stoppedNs!=null || failureReason!=null) return
        if (firstCallbackNs==null) firstCallbackNs=nowNs
        if (valid && firstValidNs==null) firstValidNs=nowNs
    }
    fun failed(reason: String, nowNs: Long) { failureReason=reason; stoppedNs=nowNs }
    fun stopped(reason: String, nowNs: Long) { stopReason=reason; stoppedNs=nowNs }
    private fun ms(from: Long, to: Long): Long? = if(to>=from) (to-from)/1_000_000L else null

    fun snapshot(reading: ReadingState, nowNs: Long): Snapshot {
        val reference=stoppedNs?:nowNs
        val elapsed=if(attemptId!=null)ms(startedNs,reference) else null
        val firstDelay=firstCallbackNs?.let{ms(startedNs,it)}
        val firstValidDelay=firstValidNs?.let{ms(startedNs,it)}
        // Callback ages describe app receipt, never the raw source sample's age.
        val lastAge=if(reading.callbacks>0)ms(reading.lastDeliveryNs,nowNs) else null
        val lastValidAge=if(reading.validCount>0)ms(reading.lastValidDeliveryNs,nowNs) else null
        val timedOut=wasRegistered && (firstDelay?:elapsed?:0L)>=FIRST_CALLBACK_TIMEOUT_MS
        val (code,label)=when {
            failureReason!=null -> "registration_failed" to when(failureReason) {
                "permission_denied" -> "没有心率权限"
                "sensor_unavailable" -> "未找到心率传感器"
                "original_capture_running" -> "原心率采集正在运行"
                else -> "心率注册失败"
            }
            attemptId==null -> "idle" to "心率尚未开始"
            !reading.running -> "stopped" to "心率已停止"
            !reading.registered -> "registering" to "正在注册心率"
            nowNs<startedNs || (reading.callbacks>0 && lastAge==null) -> "clock_anomaly" to "接收时间异常"
            reading.callbacks==0 && timedOut -> "first_data_timeout" to "90秒未收到回调，继续等待"
            reading.callbacks==0 -> "waiting_first_data" to "已注册，等待首条数据"
            reading.callbacks>0 && nowNs-reading.lastDeliveryNs>ReadingState.STALE_NS -> "stale" to "30秒未收到新回调"
            reading.accuracy==-1 -> "no_contact" to "未检测到接触，请佩戴"
            reading.recentValue(nowNs)==null -> "invalid_data" to "收到数据，但未通过检查"
            else -> "ready" to "已收到心率数值"
        }
        return Snapshot(code,label,attemptId,elapsed,firstDelay,firstValidDelay,lastAge,lastValidAge,
            timedOut,stopReason,failureReason)
    }
}
