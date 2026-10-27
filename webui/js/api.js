// REST 接口封装与运行状态探测（在线 / 睡眠 / 离线 三态）
// 后端端点见 im_agent_gateway/manage/api.py 与看门狗睡眠态托管

import { getLang } from './i18n.js';

// 会话令牌：服务每次启动随机生成，页面通过同源 /api/session 获取。
// 所有 /api 请求带 X-WebUI-Token，防止任意网页跨站调用本机管理端点。
let sessionToken = null;

// 获取令牌；force=true 强制刷新（服务重启后旧令牌失效）
export async function getToken(force = false) {
  if (!sessionToken || force) {
    const resp = await fetch('/api/session', { cache: 'no-store' });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    sessionToken = data.token;
  }
  return sessionToken;
}

// 实际发送：/api 请求自动附带令牌与语言；睡眠态托管没有令牌接口，拿不到就不带
// 语言用自定义头 X-Ui-Language：Accept-Language 属浏览器禁止脚本改写的请求头，设了也会被丢弃
async function send(url, opts) {
  const headers = new Headers(opts.headers || {});
  if (url.startsWith('/api/')) {
    headers.set('X-Ui-Language', getLang());
    try {
      headers.set('X-WebUI-Token', await getToken());
    } catch (_) { /* 服务不可达（睡眠态）时由探测逻辑判定 */ }
  }
  return fetch(url, { ...opts, headers, cache: 'no-store' });
}

// 通用 JSON 请求：非 2xx 或网络不可达时抛异常；403 先刷新令牌重试一次
async function json(url, opts = {}) {
  let resp = await send(url, opts);
  if (resp.status === 403) {
    sessionToken = null;
    resp = await send(url, opts);
  }
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

// 当前生效配置：常用 / 高级 / 未登记三组，每项带说明与当前值（敏感值已脱敏）
export const getConfig = () => json('/api/config');

// 修改配置并写回项目 .env（与 CLI config set 同一命令层，重启服务后生效）
export function setConfig(key, value) {
  return json('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, value }),
  });
}

// 睡眠态一键唤醒（由看门狗托管的 /api/wake 处理）
export const wakeService = () => json('/api/wake', { method: 'POST' });

// 重启服务：当前实例退出后由独立进程自动拉起新实例
export const restartService = () => json('/api/restart', { method: 'POST' });
