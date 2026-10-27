// 界面语言：中文 / English。选择结果存 localStorage，切换后即时重渲染，无需刷新页面。
// 静态文案用 data-i18n="中文||English"（data-i18n-attr="placeholder" 可改写属性）。

const STORE_KEY = 'im-agent-language';
const listeners = new Set();

function normalize(value) {
  const v = String(value || '').trim().toLowerCase();
  if (v.startsWith('en')) return 'en';
  if (v.startsWith('zh')) return 'zh';
  return '';
}

// 取值优先级：本机上次的选择（localStorage）> 服务端 LANGUAGE（由 initLang 注入）> 默认中文
let current = normalize(localStorage.getItem(STORE_KEY)) || 'zh';

export function getLang() {
  return current;
}

// 取当前语言下的文案
export function t(zh, en) {
  return current === 'en' ? en : zh;
}

// 切换语言：持久化 + 通知监听者（页面自行重渲染）
export function setLang(next, { persist = true } = {}) {
  const value = normalize(next) || 'zh';
  if (persist) localStorage.setItem(STORE_KEY, value);
  if (value === current) {
    applyDocumentLang();
    return current;
  }
  current = value;
  applyDocumentLang();
  for (const fn of listeners) {
    try { fn(value); } catch (_) { /* 单个监听者异常不影响其他 */ }
  }
  return current;
}

function applyDocumentLang() {
  document.documentElement.lang = current === 'en' ? 'en' : 'zh-CN';
}

// 首次进入时若本地未选择过，采用服务端当前语言
export function initLang(serverLang) {
  if (!localStorage.getItem(STORE_KEY)) {
    const value = normalize(serverLang);
    if (value) setLang(value, { persist: false });
  }
  applyDocumentLang();
  return current;
}

export function onLangChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// 应用静态文案：data-i18n="中文||English"；带 data-i18n-attr 时改写该属性
export function applyI18n(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) {
    const [zh, en = zh] = el.dataset.i18n.split('||');
    const text = t(zh, en);
    const attr = el.dataset.i18nAttr;
    if (attr) el.setAttribute(attr, text);
    else el.textContent = text;
  }
}

applyDocumentLang();
