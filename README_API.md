# ForestryAgent - 简易后端 API

这个小服务用于把现有的计算工具暴露给前端，包含：

- `/api/subplots` : 返回所有样方汇总（基于 `ForestryDataRepository` 的缓存）
- `/api/subplots/{id}/trees` : 返回指定样方的树木列表
- `/api/subplots/{id}/metrics` : 调用现有工具计算并返回样方指标（并行/非并行由工具内部决定）
- `/api/precompute` (POST): 在后台预计算并缓存每个样方的指标到 `data/cache/subplot_{id}.json`
- `/api/precompute/status/{id}` : 查询该样方是否已缓存

运行（激活对应虚拟环境后）：

```bash
pip install -r requirements.txt
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

说明：
- 后端直接复用 `forestry_spatial_tools.py` 和 `forestry_visualization_engine.py` 中的计算逻辑，保证前端与现有工具的一致性。
- 预计算会在后台调度多个任务并写入 `data/cache/` 文件夹，无需 Redis 即可快速起步。
- 智能体对话接口（agent）尚未通过 HTTP 暴露；如果需要我可以把 `agent.run_agent` 包装成异步作业接口。
