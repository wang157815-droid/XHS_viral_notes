# RedMuse Backend

## 目标

该目录是 RedMuse 重构后的新后端，遵循 FastAPI 分层架构并服务 Conversation OS、Canvas、多 Agent 编排、知识库、历史中心和系统设置。

阶段 4.5 起，`viral_app.py` 已降级为维护 stub，主 Web 后端入口以本目录为准。

## 目录结构

```
backend/
  app/
    main.py
    core/
      config.py
      responses.py
    api/
      router.py
      routes/
        auth.py
        tasks.py
        history.py
        knowledge.py
        settings.py
        health.py
    schemas/
      common.py
```

## 运行方式

在仓库根目录执行：

```bash
python backend/run.py

# 生产/容器模式
python backend/run.py --no-reload --host 0.0.0.0 --port 8100
```

## 说明

- API 前缀为 `/api/v1`，详见 `docs/refactor_phase0_ui_api_contract.md`
- 旧系统 `viral_app.py` 只保留 `/`、`/health` 和旧 `/api/*` 的 410 兼容提示
- 旧 endpoint 去向详见 `docs/phase4_deprecation_list.md`
