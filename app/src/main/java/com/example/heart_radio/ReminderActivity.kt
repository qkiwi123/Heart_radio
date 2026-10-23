package com.example.heart_radio

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.*
import android.view.Gravity
import android.view.WindowManager
import android.widget.*
import org.json.JSONObject

/** A readable, bounded notification page. Visibility and acknowledgement are separate receipts. */
class ReminderActivity : Activity() {
    companion object {
        private var live:ReminderActivity?=null
        fun close(id:String?,includingHistory:Boolean=false){live?.let { if((includingHistory || !it.history) && (id==null || it.reminderId==id))it.finish() }}
    }
    private val handler = Handler(Looper.getMainLooper())
    private var reminderId = ""
    private var history = false
    private var countdownStarted = false
    private val closeScreen = Runnable { finish() }
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState);live=this; show(intent) }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); show(intent) }
    private fun show(value: Intent) {
        val row = ReminderEngine.receipt(value.getStringExtra("reminder_id") ?: "")
        val readingHistory=value.getBooleanExtra("read_history",false)
        if (row == null || row.has("deletedAtMs") || !ReminderPresentation.canOpen(readingHistory,contextLocked(),!readingHistory && AgentInteraction.enabled(),
                row.has("cancelledAtMs"),row.has("acknowledgedAtMs"),row.optLong("expiresAtMs"),System.currentTimeMillis())) { finish(); return }
        if(reminderId==row.getString("reminderId") && history==readingHistory)return
        if(reminderId.isNotEmpty() && !history)ReminderEngine.mark(reminderId,"screen_closed")
        reminderId = row.getString("reminderId")
        history=readingHistory;countdownStarted=false;handler.removeCallbacks(closeScreen)
        val wake = !history && value.getBooleanExtra("wake_screen", false)
        setShowWhenLocked(false); setTurnScreenOn(wake)
        // This API-27 vendor window manager still consumes the legacy visibility flags.
        if(Build.VERSION.SDK_INT==27) {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
            if(wake)window.addFlags(WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
            else window.clearFlags(WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        if (wake) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        val background = WatchUi.canvas
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(background))
        val density = resources.displayMetrics.density
        fun dp(v:Int)=(v*density+0.5f).toInt()
        val layout = LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; setPadding(dp(10),dp(8),dp(10),dp(8)); setBackgroundColor(background) }
        layout.addView(TextView(this).apply {text=if(history)"最近提醒 · 本地记录" else "新提醒";textSize=11f;includeFontPadding=false;setTextColor(WatchUi.accent);setPadding(0,0,0,dp(6))})
        val content=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setBackgroundColor(background)}
        content.addView(TextView(this).apply {
            text=row.getString("title"); textSize=20f; setTextColor(Color.WHITE); typeface=Typeface.DEFAULT_BOLD
            setPadding(0,0,0,dp(6))
        })
        if(history)content.addView(TextView(this).apply {
            val time=java.text.SimpleDateFormat("MM-dd HH:mm",java.util.Locale.getDefault()).format(java.util.Date(row.optLong("receivedAtMs")))
            text="$time · ${if(row.has("acknowledgedAtMs"))"已确认" else "未确认"}${if(row.optLong("expiresAtMs")<=System.currentTimeMillis())" · 已过期" else ""}"
            textSize=11f;setTextColor(WatchUi.muted);setPadding(0,0,0,dp(6))
        })
        val body=TextView(this).apply {
            text=row.getString("body"); textSize=17f; setTextColor(Color.WHITE); setLineSpacing(dp(3).toFloat(),1f)
        }
        content.addView(body)
        val scroll=ScrollView(this).apply {
            setBackgroundColor(background);isFillViewport=true; isVerticalFadingEdgeEnabled=false; overScrollMode=android.view.View.OVER_SCROLL_NEVER; addView(content)
        }
        layout.addView(scroll, LinearLayout.LayoutParams(-1,0,1f))
        val acknowledge=if(history && row.has("acknowledgedAtMs"))null else Button(this).apply {
            text="知道了"
            WatchUi.action(this,primary=true,heightDp=32);textSize=14f
            setOnClickListener { ReminderEngine.mark(reminderId,"acknowledged"); finish() }
        }
        if(acknowledge!=null)layout.addView(acknowledge, LinearLayout.LayoutParams(dp(96),dp(32)).apply { topMargin=dp(4);gravity=Gravity.CENTER_HORIZONTAL })
        setContentView(WatchBackLayout(this,back={finish()}).apply{addView(layout,FrameLayout.LayoutParams(-1,-1))})
        if(!history)ReminderEngine.mark(reminderId,"activity_created")
        val shownId=reminderId
        val historyMode=history
        layout.post {
            if(reminderId!=shownId || history!=historyMode || isFinishing)return@post
            if(hasWindowFocus())recordVisible()
            if(history || acknowledge==null)return@post
            val bounds=android.graphics.Rect();val visible=acknowledge.getGlobalVisibleRect(bounds)
            ReminderEngine.recordLayout(reminderId,JSONObject().put("widthPx",layout.width).put("heightPx",layout.height)
                .put("bodyViewportHeightPx",scroll.height).put("contentHeightPx",content.height).put("bodyTextSp",body.textSize/resources.displayMetrics.scaledDensity)
                .put("acknowledgeVisible",visible && bounds.height()==acknowledge.height).put("acknowledgeHeightPx",acknowledge.height)
                .put("hasWindowFocus",hasWindowFocus()).put("scrollable",content.height>scroll.height))
        }
    }
    private fun contextLocked()=getSystemService(android.app.KeyguardManager::class.java).isDeviceLocked
    override fun onResume() {
        super.onResume()
        if(contextLocked()){finish();return}
        if(reminderId.isNotEmpty() && !history && !isFinishing)ReminderEngine.mark(reminderId,"activity_resumed")
    }
    override fun onWindowFocusChanged(hasFocus:Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if(hasFocus)recordVisible()
    }
    private fun recordVisible(){
        if(!isFinishing&&reminderId.isNotEmpty()&&Probe.screen(this)){
            if(contextLocked()){finish();return}
            ReminderEngine.mark(reminderId,if(history)"history_displayed" else "displayed")
            if(!history && !countdownStarted){
                countdownStarted=true
                ReminderEngine.receipt(reminderId)?.let{row ->
                    handler.postDelayed(closeScreen,minOf(row.optInt("screenSeconds",15)*1000L,(row.optLong("expiresAtMs")-System.currentTimeMillis()).coerceAtLeast(1)))
                }
            }
        }
    }
    override fun onDestroy() { handler.removeCallbacksAndMessages(null);if(reminderId.isNotEmpty() && !history)ReminderEngine.mark(reminderId,"screen_closed");if(live===this)live=null;super.onDestroy() }
}
