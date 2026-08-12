# zzcode-py313 评分环境

该目录定义 Phase 4 唯一受支持的评分镜像。镜像内只包含固定 Python、Git、pytest 和 zzcode 测试所需的运行依赖，不包含任务仓库、模型密钥或 private 数据。

构建命令：

```bash
docker build --pull=false \
  -t zzcode-eval-py313:phase4 \
  evaluation/environments/zzcode-py313
```

真正评分时不要直接执行 `docker run`。`DockerTestExecutor` 会创建短生命周期容器，并强制执行禁网、只读根文件系统、非 root 用户、能力移除、CPU/内存/PID 限制、有限 `/tmp` 与 mount allowlist。任务 workspace 只读挂载到 `/workspace`，JUnit 和日志目录可写挂载到 `/artifacts`。

依赖或 Dockerfile 变化后必须重建镜像，并通过 `docker image inspect zzcode-eval-py313:phase4` 记录新的 image ID。运行结果中的 `image_digest` 使用这个不可变 ID，而不是仅记录可变 tag。
