# Spec：管理台配置区可编辑化（说明 + 分组 + 页面修改）

## 1. 需求

1. 配置区支持在管理台页面直接**修改**，写回项目 `.env`（重启生效）；
2. 每个配置项展示通俗的**含义与影响**说明；
3. **常用配置**排前面直接展示；通常不需要修改的收进「**高级设置**」折叠区（默认收起）。

## 2. 配置元数据表（单一事实源）

新增 `im_agent_gateway/manage/config_meta.py`：`CONFIG_META: list[ConfigItem]`，
每项字段：

| 字段 | 说明 |
| --- | --- |
| `key` | .env 键名（大写，写回用） |
| `field` | Config 字段名（取值用） |
| `label` | 通俗名称（界面标题，如「谁可以使用」） |
| `desc` | 含义与影响，一句话通俗文案（如「留空表示所有人可用」） |
| `sensitive` | 是否敏感（展示脱敏为 ******） |
| `type` | `text` / `bool`（bool 在页面渲染成开关，点击即保存） |
| `advanced` | 是否高级设置（True 收进折叠区） |

登记原则：

- 覆盖 `Config` 全部可配置字段；`env_file` 是内部字段不展示不登记。
- `sensitive` 口径与现有 `_SENSITIVE_NAME_RE`（key/secret/token/pat/password）一致。
- 键名与读取口径一致：`sf_api_key` 写回 `API_KEY`（读取优先级 API_KEY > SILICONFLOW_API_KEY），
  `qodercn_pat` 写回 `QODERCN_PERSONAL_ACCESS_TOKEN`，`jev_api_key` 写回 `MAKERS_MODELS_KEY`。
- 以后新增 Config 字段若忘记登记：`GET /api/config` 自动归入 `unknown` 列表
  （仅展示、不可编辑、说明为「（暂无说明）」），避免配置凭空消失。

### 常用配置清单（advanced=False，其余全部为高级）

| key | label |
| --- | --- |
| `AGENTS` | 接入哪些智能体 |
| `ENABLED_AGENTS` | 默认开启的智能体 |
| `DEFAULT_AGENT` | 默认交给哪个智能体 |
| `AUTO_ROUTE` | 自动选路开关 |
| `JEV_ROUTE` | 智能选路开关 |
| `AGENT_KINDS` | 智能体用途分配 |
| `AGENT_COMPLEXITY` | 智能体难度档位 |
| `COMPLEXITY_ROUTE` | 按难度自动选智能体 |
| `ALLOWED_USERS` | 谁可以使用 |
| `IDLE_EXIT` | 空闲自动休息开关 |
| `IDLE_EXIT_MIN` | 多久没人说话自动休息 |
| `WAKE_REPLY` | 唤醒时先回的话 |
| `WORKBUDDY_CWD` | 办公智能体工作目录 |
| `WEBUI_ENABLED` | 网页控制台开关 |

## 3. 统一命令层与 API（CLI / 页面同一实现）

### ConfigOps.set（`manage/commands.py`）

`ConfigOps.set(cfg, key, value) -> str | None`：成功返回 None，失败返回错误文案。

- 校验 `key` 必须在元数据表内（`unknown` 键拒绝：无说明的键不给改，防误写）；
- `persist.set_env_value` 写回项目 `.env`（原子替换，原位保留）；
- **不修改内存 cfg**：页面展示的仍是「当前运行值」，改完提示重启生效；
- 写回失败（OSError）返回可读错误文案。

### REST 端点（`manage/api.py`）

- `GET /api/config` 改造返回结构（与 CLI show 共用同一视图函数）：

```json
{
  "common":   [{"key", "field", "label", "desc", "sensitive", "type", "value"}],
  "advanced": [ ... ],
  "unknown":  [ ... ]
}
```

`value` 为当前运行值（脱敏口径沿用：敏感非空 `******`，空保持空）；
列表/集合值转逗号分隔字符串便于展示与回填。

- `POST /api/config`：body `{"key", "value"}` → `ConfigOps.set`；
  返回 `{"ok": bool, "error": str | null}`。成功时前端提示「已保存，重启服务后生效」。

### CLI 适配（`manage.py`）

- `config show`：按同一视图输出——常用一段、高级一段、未登记一段，每行带 label 说明；
- `config set`：改调 `ConfigOps.set`（原逻辑迁入命令层，行为不变）。

## 4. 前端（webui/index.html 配置区）

结构：

- **常用配置**：平铺展示，每项 = label + 说明文字 + 当前值 + 修改入口；
- **高级设置**：`<details>` 折叠（默认收起），summary 文案「高级设置（一般不用改）」；
- `type=bool` 项渲染成与 Agent 开关一致的 toggle，点击即保存；
- 敏感项值显示 `******`；编辑时输入框留空保存 = 不修改（保持原值）。

交互与细节：

- 点「修改」→ 该项行内变输入框 + 保存/取消；
- 保存成功：toast「已保存，重启服务后生效」，该项显示新值并带「待重启」角标
  （前端记 `pending` 映射，轮询刷新时保持显示新值直到离开页面）；
- 保存失败：alert 错误文案，保持编辑框打开；
- **编辑中暂停本区块的轮询刷新**（防止输入框被 5 秒轮询重建丢失）；
- 文案全部通俗（「重启服务后生效」而不是「reload daemon」类术语），标题无表情符号。

## 5. 测试（TDD，无网络依赖）

| 测试 | 覆盖 |
| --- | --- |
| `test_config_meta.py`（新增） | 元数据表完整性：覆盖全部 Config 字段（除 env_file）、key 唯一、label/desc 非空、敏感口径与正则一致、常用/高级清单、bool 类型项集合 |
| `test_admin_api.py`（扩展） | GET 新结构（分组、脱敏、值格式化）、POST 写回成功、未登记键拒绝、敏感项写回、OSError 文案 |
| `test_cli.py`（扩展） | show 分组输出含 label；set 走命令层、未登记键报错 |
| 浏览器手动验收 | 分组展示、行内编辑、bool 开关、敏感项留空语义、编辑中轮询不丢输入 |

## 6. 提交拆分

1. 元数据层 + 分组视图 + ConfigOps.set + REST GET/POST（含 test_config_meta、admin_api 扩展）
2. CLI 适配（show 分组 + set 走命令层，test_cli 扩展）
3. 前端配置区改造（常用/高级/编辑/toast/pending 标记）
4. 文档：README 配置管理说明 + .env.example 补三配置段
