package com.example.heart_radio

import kotlin.math.sqrt

/** Bounded numeric preprocessing only. User-state thresholds run on the computer. */
class MotionWindow(val type:Int,val beginNs:Long,val seconds:Int=30,private val limit:Int=4096) {
    init { require(type==1 || type==4);require(seconds==30);require(limit>0) }
    data class Point(val offset:Long,val magnitude:Double)
    data class Bin(val second:Int,val count:Int,val rms:Double)
    private val points=ArrayList<Point>()
    var invalidEvents=0;private set
    var truncated=false;private set
    fun accept(values:FloatArray,arrivalNs:Long):Boolean {
        val offset=arrivalNs-beginNs
        if(offset<0 || offset>=seconds*1_000_000_000L)return true
        if(values.size<3 || values.take(3).any{!it.isFinite()}){invalidEvents++;return true}
        if(points.size>=limit){truncated=true;return false}
        val magnitude=sqrt(values.take(3).sumOf{it.toDouble()*it.toDouble()})
        points.add(Point(offset,magnitude));return true
    }
    private fun rms(values:List<Double>):Double {
        if(values.isEmpty())return 0.0
        val sorted=values.sorted()
        val center=if(type==1)if(sorted.size%2==1)sorted[sorted.size/2] else (sorted[sorted.size/2-1]+sorted[sorted.size/2])/2 else 0.0
        return sqrt(values.sumOf{(it-center)*(it-center)}/values.size)
    }
    fun count()=points.size
    fun rms()=rms(points.map{it.magnitude})
    fun spanMs()=if(points.size<2)0.0 else (points.last().offset-points.first().offset)/1_000_000.0
    fun maxGapMs()=points.zipWithNext{a,b->(b.offset-a.offset)/1_000_000.0}.maxOrNull()?:0.0
    fun bins()=points.groupBy{(it.offset/1_000_000_000L).toInt()}.toSortedMap().map{(second,rows)->Bin(second,rows.size,rms(rows.map{it.magnitude}))}
}
