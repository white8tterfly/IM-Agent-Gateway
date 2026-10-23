"""网关层：多 IM 通道接入。

各 IM 在 channels/<im>/ 下实现 IMChannel 并暴露 Channel 类，经 .env 的 CHANNELS 声明即可接入；
路由层只依赖归一化后的消息模型，不感知具体协议。
"""
