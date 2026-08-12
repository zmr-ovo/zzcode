# Evaluation 配置

本目录将在后续 Phase 中保存 provider、模型、资源限制、执行环境和 release gate 配置。

Phase 1 只实现数据边界，因此暂不创建占位配置，也不会在配置文件中保存 API Key 或其他凭据。密钥必须通过环境变量传入，并且不能进入公开任务、Agent prompt、运行轨迹或评测报告。
