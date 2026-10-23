package com.example.heart_radio

import android.content.Context
import android.graphics.Color
import android.view.Gravity
import android.widget.*

/** Human controls need no watch keyboard. Unsaved edits are never replaced by polling. */
class ReminderPolicyView(context:Context):LinearLayout(context){
    private var updating=false;private var dirty=false
    private var draft=ReminderLimits()
    private val vibrate=CheckBox(context).apply{text="允许提醒振动";setTextColor(Color.WHITE);textSize=12f;buttonTintList=android.content.res.ColorStateList.valueOf(WatchUi.accent)}
    private val wake=CheckBox(context).apply{text="允许自动亮屏";setTextColor(Color.WHITE);textSize=12f;buttonTintList=android.content.res.ColorStateList.valueOf(WatchUi.accent)}
    private val sound=CheckBox(context).apply{text="允许声音提醒";setTextColor(Color.WHITE);textSize=12f;buttonTintList=android.content.res.ColorStateList.valueOf(WatchUi.accent)}
    private val vibrationValue=TextView(context).apply{setTextColor(Color.WHITE);textSize=16f;gravity=Gravity.CENTER}
    private val screenValue=TextView(context).apply{setTextColor(Color.WHITE);textSize=16f;gravity=Gravity.CENTER}
    private val status=TextView(context).apply{setTextColor(Color.LTGRAY);textSize=11f}
    private fun TextView.show(value:String){if(text.toString()!=value)text=value}
    private fun values(){
        val seconds=draft.maxVibrationMs/1000.0
        vibrationValue.show(if(draft.maxVibrationMs%1000==0L)"${draft.maxVibrationMs/1000}秒" else "${seconds}秒")
        screenValue.show("${draft.maxScreenSeconds}秒")
    }
    private fun stepper(value:TextView,name:String,change:(Int)->Unit){
        val row=LinearLayout(context).apply{orientation=HORIZONTAL;gravity=Gravity.CENTER_VERTICAL}
        fun action(label:String,description:String,delta:Int)=Button(context).apply{
            WatchUi.action(this)
            text=label;textSize=18f;contentDescription=description+name;minWidth=0;minimumWidth=0
            setPadding(0,0,0,0);setOnClickListener{change(delta);dirty=true;values()}
        }
        row.addView(action("－","减少",-1),LayoutParams(0,-2,1f))
        row.addView(value,LayoutParams(0,-2,1.4f))
        row.addView(action("＋","增加",1),LayoutParams(0,-2,1f));addView(row)
    }
    init{
        orientation=VERTICAL
        fun label(value:String){addView(TextView(context).apply{text=value;setTextColor(Color.WHITE);textSize=12f})}
        addView(vibrate);label("整组振动上限（1～10秒，含间隔）")
        stepper(vibrationValue,"振动上限"){draft=draft.copy(maxVibrationMs=(draft.maxVibrationMs+it*500L).coerceIn(1000L,10000L))}
        addView(wake);label("页面显示上限（5～30秒）")
        stepper(screenValue,"页面上限"){draft=draft.copy(maxScreenSeconds=(draft.maxScreenSeconds+it).coerceIn(5,30))}
        addView(sound);label("声音使用系统默认提示音，播放一次；静音及勿扰时不发声。修改设置会结束正在执行的提醒。")
        addView(Button(context).apply{text="保存提醒限制";WatchUi.action(this,primary=true);setOnClickListener{
            try{
                ReminderPolicy.edit(draft.copy(vibrationEnabled=vibrate.isChecked,wakeEnabled=wake.isChecked,soundEnabled=sound.isChecked));dirty=false;refresh()
            }catch(_:Exception){Toast.makeText(context,"设置未保存，请检查存储状态",Toast.LENGTH_LONG).show()}
        }})
        addView(status)
        vibrate.setOnCheckedChangeListener{_,_->if(!updating)dirty=true};wake.setOnCheckedChangeListener{_,_->if(!updating)dirty=true}
        sound.setOnCheckedChangeListener{_,_->if(!updating)dirty=true}
        refresh()
    }
    fun refresh(){
        val view=ReminderPolicy.state()
        if(dirty){status.show("有修改，点保存后生效并同步");return}
        status.show(view.optString("syncStatus"))
        updating=true;draft=ReminderPolicy.limits();vibrate.isChecked=draft.vibrationEnabled;wake.isChecked=draft.wakeEnabled;sound.isChecked=draft.soundEnabled;values();updating=false
    }
}
