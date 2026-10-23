package com.example.heart_radio

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import kotlin.math.abs
import kotlin.math.roundToInt

/** Paints the dots after the page, without a footer, background or touch target. */
class WatchPageLayout(context:Context,private val pageCount:Int):LinearLayout(context){
    private val density=resources.displayMetrics.density
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    var selectedPage=0
        set(value){field=value;invalidate()}
    init {
        orientation=VERTICAL
        setBackgroundColor(WatchUi.canvas)
    }
    override fun dispatchDraw(canvas:Canvas){
        super.dispatchDraw(canvas)
        val y=height-7*density
        val first=width*.5f-(pageCount-1)*6.5f*density
        for(index in 0 until pageCount){
            paint.color=if(index==selectedPage)Color.WHITE else Color.rgb(110,110,110)
            paint.style=if(index==selectedPage)Paint.Style.FILL else Paint.Style.STROKE
            paint.strokeWidth=density
            canvas.drawCircle(first+index*13*density,y,if(index==selectedPage)2.5f*density else 2*density,paint)
        }
    }
}

/** Framework scrolling keeps the vendor's canScrollHorizontally check for swipe-back. */
class WatchPager(context: Context) : HorizontalScrollView(context) {
    private val pages = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var downX = 0f
    private var downY = 0f
    private var vertical = false
    private var flung = false
    var currentPage = 0
        private set
    var onPageChanged: ((Int) -> Unit)? = null

    init {
        setBackgroundColor(WatchUi.canvas)
        // Page accessibility text can focus this container; its theme highlight
        // would otherwise cover the entire OLED page with a translucent white fill.
        setDefaultFocusHighlightEnabled(false)
        isHorizontalScrollBarEnabled = false
        isVerticalScrollBarEnabled = false
        isHorizontalFadingEdgeEnabled = false
        overScrollMode = View.OVER_SCROLL_NEVER
        addView(pages, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.MATCH_PARENT))
    }

    fun addPage(view: View) {
        pages.addView(view, LinearLayout.LayoutParams(resources.displayMetrics.widthPixels, LayoutParams.MATCH_PARENT))
    }

    fun showPage(index: Int, animate: Boolean = true) {
        currentPage = index.coerceIn(0, (pages.childCount - 1).coerceAtLeast(0))
        if (animate) smoothScrollTo(currentPage * width, 0) else scrollTo(currentPage * width, 0)
        onPageChanged?.invoke(currentPage)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        for (i in 0 until pages.childCount) pages.getChildAt(i).layoutParams = LinearLayout.LayoutParams(w, LayoutParams.MATCH_PARENT)
        post { showPage(currentPage, false) }
    }

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> { downX = event.x; downY = event.y; vertical = false }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.x - downX
                val dy = event.y - downY
                if (abs(dy) > slop && abs(dy) > abs(dx)) vertical = true
                if (vertical || (currentPage == 0 && dx > slop)) return false
                if (abs(dx) > slop && abs(dx) > abs(dy) * 1.3f) parent?.requestDisallowInterceptTouchEvent(true)
                else return false
            }
        }
        return super.onInterceptTouchEvent(event)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_UP) flung = false
        val handled = super.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_CANCEL || (event.actionMasked == MotionEvent.ACTION_UP && !flung)) {
            if (width > 0) showPage((scrollX.toFloat() / width).roundToInt())
        }
        return handled
    }

    override fun fling(velocityX: Int) {
        flung = true
        showPage(currentPage + if (velocityX > 0) 1 else -1)
    }
}

/** Fallback for plain Activities; a vendor-handled gesture sends CANCEL and never pops twice. */
class WatchBackLayout(context: Context, private val back: () -> Unit, private val enabled: () -> Boolean = { true }) : FrameLayout(context) {
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var startX = 0f
    private var startY = 0f
    private var tracking = false
    private var vertical = false

    init { setBackgroundColor(WatchUi.canvas) }

    // ScrollView can disallow parent interception on DOWN. Observe dispatch so the
    // first page still returns; cancel the child before consuming a back gesture.
    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                startX = event.x; startY = event.y; tracking = false; vertical = false
                // The vendor decor otherwise intercepts MOVE before this layout's
                // threshold and can cancel without completing a system return.
                if (enabled()) parent?.requestDisallowInterceptTouchEvent(true)
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.x - startX
                val dy = event.y - startY
                if (abs(dy) > slop && abs(dy) > abs(dx)) vertical = true
                if (!tracking && enabled() && !vertical && dx > slop * 2 && dx > abs(dy) * 1.5f) {
                    tracking = true
                    parent?.requestDisallowInterceptTouchEvent(true)
                    val cancel = MotionEvent.obtain(event).apply { action = MotionEvent.ACTION_CANCEL }
                    super.dispatchTouchEvent(cancel)
                    cancel.recycle()
                }
            }
            MotionEvent.ACTION_UP -> {
                if (tracking) {
                    tracking = false
                    if (enabled() && event.x - startX > width * .25f) back()
                    return true
                }
            }
            MotionEvent.ACTION_CANCEL -> {
                if (tracking) { tracking = false; return true }
            }
        }
        // Empty space must own DOWN too, otherwise no subsequent MOVE/UP arrives.
        return if (tracking) true else super.dispatchTouchEvent(event) || enabled()
    }
}
