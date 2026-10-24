# 关键字意图路由（Jev 退化方案）规格

## 背景

自动路由当前依赖 Jev 决策模型：未配置 Key、调用失败或低置信度时，退化逻辑只有
一条编码关键词正则（`_CODE_INTENT_RE`），只能判断「编码 / 非编码」二元意图，
办公等其他意图全部落到默认 Agent。

## 需求

Jev 判断模型支持退化：没配置的时候使用简单的关键字意图解析，让多 Agent
分流在零配置、离线状态下依然可用。

## 设计

### 1. Agent 侧声明（agents/base.py）

- `BaseAgent` 新增类属性 `routing_keywords: tuple[str, ...] = ()`：
  Agent 自行声明的意图关键词，参与关键字路由。
- 新增属性 `route_keywords`（合成规则，子类一般无需覆写）：
  1. 基础词表：`kind == "coding"` 默认继承内置编码词表（原 `_CODE_INTENT_RE`
     的词表迁出，保证向后兼容），其余为空；
  2. `routing_keywords` 声明的是**追加词**，并入基础词表后生效
     （编码 Agent 可追加名字直达词、办公 Agent 声明办公词）。

### 2. 关键字路由器（router/keyword_router.py，与 jev_router.py 平级）

`route(query, agents) -> str | None`，纯同步、零依赖、无网络：

- 每个 Agent 用其 `route_keywords` 在 query 中计数命中（大小写不敏感），
  得分 = 命中总次数；
- 最高分 > 0 才有结果；多个 Agent 并列最高分时，取注册顺序中最先出现者
  （与旧编码兜底「命中即选第一个 coding Agent」行为一致）；
- 全部未命中返回 None（保守，落默认 Agent）。

### 3. 路由链集成（router/dispatcher.py）

`_resolve_agent` 的自动路由段改为：

```
Jev 可用 → jev.route（失败/低置信返回 None）
        ↘ 关键字意图解析 route(query, agents)
                ↘ 仍未命中 → 默认 Agent
```

即：**Jev 没配置、失败、低置信三种情况统一退化到关键字意图解析**；
`_CODE_INTENT_RE` 从 dispatcher 移除（词表由 keyword_router 承载）。

### 4. 内置 Agent 关键词声明

- `qoder` / `trae`：不显式声明编码词（继承默认）；各自追加名字词
  （"qoder" / "trae"），用户点名即直达；
- `workbuddy`：声明办公词表（文档、表格、PPT、会议纪要等），
  办公意图在零配置下可自动分流；
- `siliconflow`：不声明，继续作为聊天默认兜底。

## 验收标准

1. 未配置 Jev Key 时：编码消息 → 第一个 coding Agent（与旧行为一致）；
   办公消息 → workbuddy；点名「trae / qoder」→ 对应 Agent；闲聊 → 默认 Agent。
2. Jev 已配置时：行为与现状一致（关键字解析只在 Jev 不可用/失败/低置信时介入）。
3. 全部既有测试（契约 / 注册 / 唤醒等）不回归。
