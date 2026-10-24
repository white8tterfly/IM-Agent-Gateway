// REST 接口封装与运行状态探测（在线 / 睡眠 / 离线 三态）
// 后端端点见 im_agent_gateway/manage/api.py 与看门狗睡眠态托管

// 通用 JSON 请求：非 2xx 或网络不可达时抛异常
async function json(url, opts = {}) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  return resp.json();
}

// 探测服务运行状态：
// 1. /api/status 可达 → online（服务在线，正常使用）
// 2. 不可达但 /api/watch-state 可达 → sleeping（看门狗托管睡眠态，可唤醒）
// 3. 都不可达 → offline（服务与看门狗都不在运行）
export async function detectState() {
  try {
    await json('/api/status');
    return 'online';
  } catch (_) { /* 继续探测睡眠态 */ }
  try {
    await json('/api/watch-state');
    return 'sleeping';
  } catch (_) { /* 都不可达 */ }
  return 'offline';
}

// 服务概要：运行状态 / 通道连接 / Agent 统计 / 空闲信息
export const getStatus = () => json('/api/status');

// Agent 列表
export const getAgents = () => json('/api/agents');

// 切换 Agent 开关（与 IM 指令、CLI 走同一管理命令层，含 .env 写回）
export function setAgentActive(name, active) {
  return json(`/api/agents/${encodeURIComponent(name)}/active`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active }),
  });
}

// 任务：进行中数量 + 最近任务
export const getTasks = () => json('/api/tasks');

// 运行日志尾部
export const getLogs = (lines = 200) => json(`/api/logs?lines=${lines}`);

// 当前生效配置（敏感值已脱敏）
export const getConfig = () => json('/api/config');

// 睡眠态一键唤醒（由看门狗托管的 /api/wake 处理）
export const wakeService = () => json('/api/wake', { method: 'POST' });
