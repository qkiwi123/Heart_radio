package com.example.heart_radio

import android.app.Activity
import android.app.KeyguardManager
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Build
import android.os.Bundle
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.*
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Local reading and management never request vibration or screen wake. */
class ReminderInboxActivity : Activity() {
    private lateinit var list: ListView
    private lateinit var summary: TextView
    private lateinit var normalHeading: View
    private lateinit var managementHeading: View
    private lateinit var manageButton: Button
    private lateinit var selectAllButton: Button
    private lateinit var deleteButton: Button
    private val adapter = InboxAdapter()
    private var rows = emptyList<JSONObject>()
    private val selected = linkedSetOf<String>()
    private var managing = false
    private var inlineDeleteId: String? = null
    private var deleting = false
    private var subscribed = false
    private var modernBackCallback: android.window.OnBackInvokedCallback? = null
    private var savedScroll: SavedScroll? = null
    private val observer: (JSONObject) -> Unit = { runOnUiThread { refresh() } }
    private val canvasColor = WatchUi.canvas
    private val time = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
    private val stableIds = mutableMapOf<String, Long>()
    private var nextStableId = 0L
    private data class SavedScroll(val id: String?, val position: Int, val top: Int)
    private data class RowViews(val outer: FrameLayout, val meta: TextView, val title: TextView,
                                val preview: TextView, val check: CheckBox, val delete: Button)
    private fun dp(v: Int) = (v * resources.displayMetrics.density + .5f).toInt()
    private fun rowId(row: JSONObject) = row.getString("reminderId")
    private fun compact(label: String, primary: Boolean = false, click: () -> Unit) = Button(this).apply {
        text = label; WatchUi.action(this, primary, heightDp = 28); textSize = 11f
        setOnClickListener { click() }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        managing = savedInstanceState?.getBoolean("managing", false) ?: false
        selected.addAll(savedInstanceState?.getStringArrayList("selected") ?: emptyList())
        inlineDeleteId = savedInstanceState?.getString("inline_delete")
        if (savedInstanceState != null) savedScroll = SavedScroll(savedInstanceState.getString("anchor_id"),
            savedInstanceState.getInt("anchor_position"), savedInstanceState.getInt("anchor_top"))
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(canvasColor))
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(10), dp(6), dp(10), dp(8)); setBackgroundColor(canvasColor)
        }
        val heading = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_VERTICAL }
        val top = FrameLayout(this)
        normalHeading = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            addView(TextView(context).apply {
                text = "最近提醒"; textSize = 22f; typeface = Typeface.DEFAULT_BOLD
                includeFontPadding = false; maxLines = 1; setTextColor(Color.WHITE)
            }, LinearLayout.LayoutParams(0, -2, 1f))
            manageButton = compact("管理") { enterManagement() }
            addView(manageButton, LinearLayout.LayoutParams(dp(44), dp(28)).apply { marginStart = dp(4) })
        }
        managementHeading = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            selectAllButton = compact("全选") {
                if (rows.isNotEmpty() && rows.all { rowId(it) in selected }) selected.clear()
                else selected.addAll(rows.map { rowId(it) })
                updateHeader(); redrawVisible()
            }
            deleteButton = compact("删除", primary = true) { deleteRows(selected.toSet()) }
            addView(selectAllButton, LinearLayout.LayoutParams(0, dp(28), 1f))
            addView(deleteButton, LinearLayout.LayoutParams(0, dp(28), 1f).apply { marginStart = dp(6) })
        }
        top.addView(normalHeading, FrameLayout.LayoutParams(-1, -1))
        top.addView(managementHeading, FrameLayout.LayoutParams(-1, -1))
        heading.addView(top, LinearLayout.LayoutParams(-1, dp(28)))
        summary = TextView(this).apply {
            textSize = 10f; includeFontPadding = false; maxLines = 1
            ellipsize = TextUtils.TruncateAt.END; setTextColor(WatchUi.muted)
        }
        heading.addView(summary, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(3) })
        // The two modes share a fixed header height; the list viewport never moves.
        layout.addView(heading, LinearLayout.LayoutParams(-1, dp(44)))
        list = ListView(this).apply {
            id = android.R.id.list; setBackgroundColor(canvasColor)
            isVerticalFadingEdgeEnabled = false; overScrollMode = View.OVER_SCROLL_NEVER
            cacheColorHint = Color.TRANSPARENT; isScrollingCacheEnabled = false; isAnimationCacheEnabled = false
            setSelector(android.graphics.drawable.ColorDrawable(Color.TRANSPARENT))
            divider = android.graphics.drawable.ColorDrawable(WatchUi.border); dividerHeight = dp(1)
            adapter = this@ReminderInboxActivity.adapter
        }
        list.setOnItemClickListener { _, _, position, _ ->
            val row = rows.getOrNull(position) ?: return@setOnItemClickListener
            if (managing) {
                val key = rowId(row)
                if (!selected.add(key)) selected.remove(key)
                updateHeader(); redrawVisible()
            } else {
                inlineDeleteId = null; updateHeader(); redrawVisible()
                startActivity(Intent(this, ReminderActivity::class.java).putExtra("reminder_id", rowId(row))
                    .putExtra("read_history", true))
            }
        }
        list.setOnItemLongClickListener { _, _, position, _ ->
            if (!managing) {
                inlineDeleteId = rows.getOrNull(position)?.let { rowId(it) }
                updateHeader(); redrawVisible()
            }
            true
        }
        layout.addView(list, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = dp(6) })
        setContentView(WatchBackLayout(this, back = { navigateBack() }).apply {
            addView(layout, FrameLayout.LayoutParams(-1, -1))
        })
        updateHeader()
    }

    private fun enterManagement() {
        // No data query, adapter replacement or notifyDataSetChanged on this path.
        managing = true; inlineDeleteId = null; selected.clear()
        updateHeader(); redrawVisible()
    }
    private fun navigateBack() {
        when {
            managing -> { managing = false; selected.clear(); updateHeader(); redrawVisible() }
            inlineDeleteId != null -> { inlineDeleteId = null; updateHeader(); redrawVisible() }
            else -> finish()
        }
    }
    private fun updateHeader() {
        normalHeading.visibility = if (managing) View.GONE else View.VISIBLE
        managementHeading.visibility = if (managing) View.VISIBLE else View.GONE
        manageButton.isEnabled = rows.isNotEmpty()
        selectAllButton.text = if (rows.isNotEmpty() && rows.all { rowId(it) in selected }) "取消全选" else "全选"
        selectAllButton.isEnabled = rows.isNotEmpty() && !deleting
        deleteButton.text = if (selected.isEmpty()) "删除" else "删除(${selected.size})"
        deleteButton.isEnabled = selected.isNotEmpty() && !deleting
        deleteButton.alpha = if (deleteButton.isEnabled) 1f else .4f
        val unread = rows.count { !it.has("acknowledgedAtMs") }
        summary.text = if (managing) "已选${selected.size}/${rows.size} · 右划退出管理"
            else if (rows.isEmpty()) "暂无提醒，收到后保存在这里" else "${unread}条未确认 · 共${rows.size}条"
        updateModernBack()
    }
    private fun redrawVisible() {
        for (i in 0 until list.childCount) {
            val row = rows.getOrNull(list.firstVisiblePosition + i) ?: continue
            (list.getChildAt(i).tag as? RowViews)?.let { bind(it, row) }
        }
    }
    private fun deleteRows(ids: Set<String>) {
        if (deleting || ids.isEmpty()) return
        deleting = true; updateHeader()
        val result = ReminderEngine.delete(ids)
        deleting = false
        if (result.success) {
            inlineDeleteId = null; refresh(); redrawVisible()
            Toast.makeText(this, "已删除${result.count}条提醒", Toast.LENGTH_SHORT).show()
        } else Toast.makeText(this, "删除失败，记录未移除", Toast.LENGTH_LONG).show()
        updateHeader()
    }
    private fun refresh() {
        val next = ReminderEngine.recent()
        if (next.map { rowId(it) to it.has("acknowledgedAtMs") } == rows.map { rowId(it) to it.has("acknowledgedAtMs") }) return
        val oldIds = rows.map { rowId(it) }
        val first = list.firstVisiblePosition.coerceAtLeast(0)
        val offset = list.getChildAt(0)?.top ?: 0
        val anchor = oldIds.getOrNull(first)
        rows = next
        val nextIds = rows.map { rowId(it) }.toSet()
        selected.retainAll(nextIds)
        if (inlineDeleteId !in nextIds) inlineDeleteId = null
        adapter.notifyDataSetChanged()
        val restore = savedScroll.also { savedScroll = null }
        if (rows.isNotEmpty()) {
            val survivor = anchor?.takeIf { it in nextIds }
                ?: oldIds.drop(first).firstOrNull { it in nextIds }
                ?: oldIds.take(first).lastOrNull { it in nextIds }
            val key = restore?.id ?: survivor
            val index = rows.indexOfFirst { rowId(it) == key }.takeIf { it >= 0 }
                ?: (restore?.position ?: first).coerceIn(rows.indices)
            list.setSelectionFromTop(index, restore?.top ?: offset)
        }
        updateHeader()
    }
    private inner class InboxAdapter : BaseAdapter() {
        override fun getCount() = rows.size
        override fun getItem(position: Int) = rows[position]
        override fun hasStableIds() = true
        override fun getItemId(position: Int) = stableIds.getOrPut(rowId(rows[position])) { nextStableId++ }
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val holder = (convertView?.tag as? RowViews) ?: createRow()
            bind(holder, rows[position]); return holder.outer
        }
    }
    private fun createRow(): RowViews {
        val outer = FrameLayout(this).apply { setBackgroundColor(canvasColor); isDrawingCacheEnabled = false }
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; setPadding(0, dp(8), 0, dp(8))
            fun rounded(color: Int) = GradientDrawable().apply { setColor(color); cornerRadius = dp(10).toFloat() }
            background = RippleDrawable(ColorStateList.valueOf(Color.argb(45, 255, 82, 104)),
                rounded(WatchUi.surface), rounded(Color.WHITE))
            isDuplicateParentStateEnabled = true
        }
        val text = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun label(size: Float) = TextView(this).apply {
            textSize = size; includeFontPadding = false; maxLines = 1; ellipsize = TextUtils.TruncateAt.END
        }.also { text.addView(it, LinearLayout.LayoutParams(-1, -2).apply { if (text.childCount > 0) topMargin = dp(4) }) }
        val meta = label(10f)
        val title = label(16f).apply { typeface = Typeface.DEFAULT_BOLD; setTextColor(Color.WHITE) }
        val preview = label(12f).apply { setTextColor(WatchUi.muted) }
        card.addView(text, LinearLayout.LayoutParams(0, -2, 1f))
        val check = CheckBox(this).apply {
            isClickable = false; isFocusable = false; isFocusableInTouchMode = false
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            minWidth = 0; minimumWidth = 0; minHeight = 0; minimumHeight = 0; setPadding(0, 0, 0, 0)
            buttonTintList = ColorStateList(arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf()),
                intArrayOf(WatchUi.accent, WatchUi.muted))
        }
        card.addView(check, LinearLayout.LayoutParams(dp(28), -2).apply { marginStart = dp(4) })
        val delete = compact("删除", primary = true) {}.apply { isFocusable = false }
        card.addView(delete, LinearLayout.LayoutParams(dp(52), dp(32)).apply { marginStart = dp(4) })
        outer.addView(card, FrameLayout.LayoutParams(-1, -2))
        return RowViews(outer, meta, title, preview, check, delete).also { outer.tag = it }
    }
    private fun bind(holder: RowViews, row: JSONObject) {
        val key = rowId(row)
        val confirmed = row.has("acknowledgedAtMs")
        val label = "${if (confirmed) "已确认" else "未确认"} · ${time.format(Date(row.optLong("receivedAtMs")))}"
        holder.meta.setTextColor(WatchUi.muted)
        holder.meta.text = android.text.SpannableString(label).apply {
            if (!confirmed) setSpan(android.text.style.ForegroundColorSpan(WatchUi.accent), 0, 3, android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        holder.title.text = row.getString("title")
        holder.preview.text = row.getString("body").replace('\n', ' ')
        holder.check.visibility = if (managing) View.VISIBLE else View.GONE
        holder.check.isChecked = key in selected
        holder.delete.visibility = if (!managing && inlineDeleteId == key) View.VISIBLE else View.GONE
        holder.delete.setOnClickListener { deleteRows(setOf(key)) }
        holder.outer.contentDescription = if (managing) "${if (key in selected) "已选中" else "未选中"}，${row.getString("title")}" else null
    }
    private fun updateModernBack() {
        if (Build.VERSION.SDK_INT < 33) return
        if ((managing || inlineDeleteId != null) && modernBackCallback == null) {
            modernBackCallback = android.window.OnBackInvokedCallback { navigateBack() }.also {
                onBackInvokedDispatcher.registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, it)
            }
        } else if (!managing && inlineDeleteId == null) {
            modernBackCallback?.let { onBackInvokedDispatcher.unregisterOnBackInvokedCallback(it) }
            modernBackCallback = null
        }
    }
    @android.annotation.SuppressLint("GestureBackNavigation")
    override fun onBackPressed() = navigateBack()
    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("managing", managing)
        outState.putStringArrayList("selected", ArrayList(selected))
        outState.putString("inline_delete", inlineDeleteId)
        outState.putString("anchor_id", rows.getOrNull(list.firstVisiblePosition)?.let { rowId(it) })
        outState.putInt("anchor_position", list.firstVisiblePosition)
        outState.putInt("anchor_top", list.getChildAt(0)?.top ?: 0)
        super.onSaveInstanceState(outState)
    }
    override fun onResume() {
        super.onResume()
        if (getSystemService(KeyguardManager::class.java).isDeviceLocked) { finish(); return }
        refresh(); ReminderEngine.subscribe(observer); subscribed = true
    }
    override fun onPause() {
        if (subscribed) { ReminderEngine.unsubscribe(observer); subscribed = false }
        super.onPause()
    }
    override fun onDestroy() {
        if (Build.VERSION.SDK_INT >= 33) modernBackCallback?.let { onBackInvokedDispatcher.unregisterOnBackInvokedCallback(it) }
        super.onDestroy()
    }
}
