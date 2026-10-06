package com.example.heart_radio

/** Numeric private IPv4 only: no DNS, URL redirects or arbitrary Internet destination. */
data class WifiConfig(val host: String, val port: Int, val pin: String, val token: String) {
    val pinBytes get() = pin.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    val tokenBytes get() = token.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    companion object {
        fun parse(code: String): WifiConfig {
            val fields = code.trim().split('|')
            require(fields.size == 3) { "请使用电脑生成的完整连接码" }
            val endpoint = fields[0].split(':')
            require(endpoint.size == 2) { "地址格式错误" }
            val octets = endpoint[0].split('.')
            require(octets.size == 4 && octets.all { it.matches(Regex("0|[1-9][0-9]{0,2}")) && it.toInt() in 0..255 }) { "请输入IPv4地址" }
            val a = octets.map { it.toInt() }
            require(a[0] == 10 || a[0] == 172 && a[1] in 16..31 || a[0] == 192 && a[1] == 168) { "仅允许局域网私有地址" }
            val port = endpoint[1].toIntOrNull() ?: 0
            require(port in 1024..65535) { "端口应为1024至65535" }
            require(fields.drop(1).all { it.matches(Regex("[0-9a-fA-F]{64}")) }) { "连接码校验字段错误" }
            return WifiConfig(endpoint[0], port, fields[1].lowercase(), fields[2].lowercase())
        }
    }
}
