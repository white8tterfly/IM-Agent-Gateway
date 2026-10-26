// 聊天 WebSocket 封装：自动重连（指数退避）+ 连接状态回调
// 帧格式见 webui 通道：发送 {"type":"text","content":...}，
// 接收 {"type":"text"} / {"type":"stream","id","content","finish"}
// 握手需携带会话令牌（?token=），由 api.js 的同源令牌接口获取
import { getToken } from './api.js';

// 创建聊天连接
// onFrame(frame)：收到一帧 JSON
// onState(state)：连接状态变化 "online" | "offline"
export function createChatSocket({ onFrame, onState }) {
  let ws = null;          // 当前 WebSocket
  let closed = false;     // 用户主动关闭后不再重连
  let retry = 0;          // 重连次数（指数退避基数）
  let timer = null;       // 重连定时器

  async function connect() {
    // 每次重连都刷新令牌：服务重启后旧令牌失效
    let query = '';
    try {
      query = `?token=${encodeURIComponent(await getToken(true))}`;
    } catch (_) { /* 睡眠态：不带令牌，连接失败后按退避重试 */ }
    if (closed) return;   // 取令牌期间已主动关闭

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/chat${query}`);

    ws.onopen = () => {
      retry = 0;
      onState('online');
    };

    ws.onmessage = (event) => {
      try {
        onFrame(JSON.parse(event.data));
      } catch (_) { /* 忽略无法解析的帧 */ }
    };

    ws.onclose = () => {
      onState('offline');
      if (!closed) {
        // 指数退避重连：1s → 2s → 4s → … 上限 10s
        const delay = Math.min(1000 * 2 ** retry, 10000);
        retry += 1;
        timer = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => ws.close(); // 出错统一走 onclose 的重连逻辑
  }

  connect();

  return {
    // 发送一帧（连接未就绪时静默丢弃）
    send(frame) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(frame));
      }
    },
    // 主动关闭：不再重连
    close() {
      closed = true;
      clearTimeout(timer);
      if (ws) ws.close();
    },
  };
}
