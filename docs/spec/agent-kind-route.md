# Spec：Agent 用途配置与两级路由（用途 → 复杂度）

## 1. 需求

1. Agent 的**用途**支持三种：`chat`（对话）、`coding`（编码）、`office`（办公）；
   用途可在 `.env` 配置，覆盖 Agent 类默认 `kind`。
2. 意图识别路由改为**两级**：先由 Jev 判定消息属于哪个**用途**，
   再在该用途的活跃 Agent 中选择具体 Agent。
3. 同一用途配置多个 Agent 时，由 Jev 按**问题复杂度**选择对应 Agent
   （每个 Agent 声明复杂度档位）；**复杂度选择强制依赖 Jev**——
   Jev 不可用或开关未开时，按 `.env` 的 `AGENTS` 声明顺序取第一个。

## 2. 配置（全部独立，默认行为与现状一致）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_KINDS` | `""` | 用途覆写映射：`名称=用途` 逗号分隔，如 `workbuddy=office,qoder=coding`；空 = 用各 Agent 类默认 `kind`。非法用途值启动时告警并忽略 |
| `AGENT_COMPLEXITY` | `""` | 复杂度档位映射：`名称=档位`，档位固定 `simple` / `mid` / `complex` 三档；仅同用途多 Agent 时参与选择 |
| `COMPLEXITY_ROUTE` | `"0"` | 复杂度选择开关：`1` = 同用途多 Agent 时由 Jev 按问题复杂度选（需 Jev 可用）；`0` = 按声明顺序取第一个 |

内置示例 Agent 的类默认用途调整：`workbuddy` → `office`（办公），
`qoder` / `trae` → `coding`（编码），`siliconflow` → `chat`（对话）。

## 3. 路由流程（`_resolve_agent` 改造）

```
/agent <名> 固定            → 直接返回（不变）
auto_route=1：
  ① 用途决策（活跃用途 > 1 时才执行，单用途短路）
     Jev 可用   → 第一问：choice「用途」，criteria = 各用途一句话描述，
                  置信度过低 → 落到关键词
     Jev 不可用 → 关键词按用途词表计分（coding 词表沿用内置；
                  office 新增内置词表；chat 不声明词表，未命中兜底）
     全部未命中 → DEFAULT_AGENT 所在用途
  ② 用途内 Agent 选择
     用途内活跃 Agent 仅 1 个 → 直接返回（短路）
     多个且 COMPLEXITY_ROUTE=1 且 Jev 可用且候选都已配档位
       → 第二问：choice「复杂度档位」，criteria = 档位语义描述
        （simple=简单直接的问题，mid=中等，complex=复杂推理与长任务），
         按返回档位选 Agent；低置信 → 声明顺序第一个
     其余情况 → 声明顺序第一个
  ③ 用途决策结果无活跃 Agent → DEFAULT_AGENT 兜底（不变）
DEFAULT_AGENT / active[0] 兜底（不变）
```

约束与语义：

- 两级决策是**串行两次** `/systemone` 请求（第二问候选依赖第一问结果）。
- 复杂度候选 = 该用途内活跃且**显式配置了档位**的 Agent；
  全部未配档位时不进入 Jev，直接按声明顺序。
- 关键词用途计分沿用 Agent 级计分口径：命中次数求和、并列取先注册。

## 4. 影响面

| 模块 | 改动 |
| --- | --- |
| `config.py` | 新增 `agent_kinds` / `agent_complexity` / `complexity_route` 解析 |
| `agents/base.py` | `kind` 文档更新三值；新增实例属性 `complexity`（默认空）；`office` 默认路由描述与办公词表 |
| `agents/registry.py` 或装配处 | 加载后按 `AGENT_KINDS` 覆写实例 `kind`、按 `AGENT_COMPLEXITY` 覆写 `complexity` |
| `router/keyword_router.py` | 新增用途级计分入口与内置 office 词表 |
| `router/jev_router.py` | 新增「用途选择」「档位选择」两个 choice 方法（复用 `_parse` 置信度口径） |
| `router/dispatcher.py` | `_resolve_agent` 两级编排 |
| `manage/service.py` | agents 列表带用途与档位（管理台/CLI 展示） |

## 5. 测试（TDD，全部无网络依赖）

| 测试文件 | 覆盖 |
| --- | --- |
| `test_kind_config.py` | 用途/档位配置解析与覆写、非法值告警忽略、workbuddy 类默认 office |
| `test_keyword_route.py`（扩展） | 用途级计分：office 词表命中、coding 命中、未命中退化 |
| `test_jev_route.py`（扩展） | 两级 choice 请求与解析、单用途/单 Agent 短路、低置信退化顺序 |
| `test_dispatcher_route.py` | 编排全路径：两级、退化路径、固定路由不受影响 |

## 6. 提交拆分

1. 配置层：三配置项 + kind 覆写 + 内置 Agent 默认用途调整（含 test_kind_config）
2. 关键词用途级退化（keyword_router + office 词表 + 测试）
3. Jev 两级决策（jev_router 两方法 + 测试）
4. dispatcher 编排 + 管理展示（测试）
5. 文档：README 能力表 / 路由说明 / .env.example 配置段
