const MAX_LOG_LINES = 2000;
let pollTimer = null;

function $(id) { return document.getElementById(id); }

function setBadge(running) {
  const badge = $('status-badge');
  badge.textContent = running ? '运行中' : '未运行';
  badge.className = 'badge ' + (running ? 'running' : 'stopped');
  $('btn-start').disabled = running;
  $('btn-stop').disabled = !running;
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();
    setBadge(s.running);
    $('status-pid').textContent = s.running ? s.pid : '—';
  } catch (e) { /* ignore */ }
}

function appendLog(lines) {
  if (!lines || !lines.length) return;
  const panel = $('log-panel');
  let text = panel.textContent;
  if (text) text += '\n';
  text += lines.join('\n');
  const all = text.split('\n');
  if (all.length > MAX_LOG_LINES) {
    text = all.slice(all.length - MAX_LOG_LINES).join('\n');
  }
  panel.textContent = text;
  panel.scrollTop = panel.scrollHeight;
}

async function initLogs() {
  try {
    const r = await fetch('/api/logs?tail=300');
    const d = await r.json();
    $('log-panel').textContent = d.lines.join('\n');
    $('log-panel').scrollTop = $('log-panel').scrollHeight;
  } catch (e) { /* ignore */ }
  startPolling();
}

async function pollLogs() {
  try {
    const r = await fetch('/api/logs');
    const d = await r.json();
    appendLog(d.lines);
  } catch (e) { /* ignore */ }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollLogs, 1500);
}

async function start() {
  const r = await fetch('/api/start', { method: 'POST' });
  const d = await r.json();
  toast(d.msg);
  await refreshStatus();
  await initLogs();
  // 启动后检测缓存刷新登录状态
  try {
    await fetch('/api/login/refresh', { method: 'POST' });
    await refreshLoginStatus();
  } catch (e) { /* ignore */ }
}

async function stop() {
  const r = await fetch('/api/stop', { method: 'POST' });
  const d = await r.json();
  toast(d.msg);
  await refreshStatus();
  try {
    const fr = await fetch('/api/logs?flush=1');
    const fd = await fr.json();
    appendLog(fd.lines);
  } catch (e) { /* ignore */ }
}

async function loadConfig() {
  const r = await fetch('/api/config');
  const d = await r.json();
  $('config-editor').value = d.config_text || '';
  const acc = d.account || {};
  $('acc-username').value = acc.steam_username || '';
  $('acc-password').value = acc.steam_password || '';
  $('acc-shared').value = acc.shared_secret || '';
  $('acc-identity').value = acc.identity_secret || '';
  $('account-editor').value = d.account_text || '';
}

async function saveConfig() {
  const content = $('config-editor').value;
  const r = await fetch('/api/config/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  const d = await r.json();
  showMsg('config-msg', d.msg, d.ok);
}

async function saveAccount() {
  // 文本视图激活时，先尝试从文本同步表单（校验格式）
  if ($('acc-text-view').style.display !== 'none') {
    if (!accTextToForm()) {
      showMsg('account-msg', '文本 JSON 格式错误，无法保存', false);
      return;
    }
  }
  const data = {
    steam_username: $('acc-username').value,
    steam_password: $('acc-password').value,
    shared_secret: $('acc-shared').value,
    identity_secret: $('acc-identity').value,
  };
  const r = await fetch('/api/account/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  const d = await r.json();
  showMsg('account-msg', d.msg, d.ok);
  if (d.ok) loadConfig();
}

function showMsg(id, msg, ok) {
  const el = $(id);
  el.textContent = msg;
  el.className = 'msg ' + (ok ? 'ok' : 'err');
  setTimeout(() => { el.textContent = ''; }, 4000);
}

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.display = 'none'; }, 3000);
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'dashboard') initLogs();
    if (btn.dataset.tab === 'config') { loadConfig(); loadConfigTable(); }
  });
});

$('btn-start').addEventListener('click', start);
$('btn-stop').addEventListener('click', stop);
$('btn-save-config').addEventListener('click', saveConfig);
$('btn-save-account').addEventListener('click', saveAccount);
$('btn-clear-log').addEventListener('click', () => { $('log-panel').textContent = ''; });

// ==== 退出 GUI ====
async function quitGui() {
  if (!confirm('确定退出 GUI？将同时停止 Steamauto 子进程，整个控制台会关闭。')) return;
  await fetch('/api/shutdown', { method: 'POST' });
}

$('btn-quit-gui').addEventListener('click', quitGui);

// ==== 日志等级 ====
async function loadLogLevel() {
  try {
    const r = await fetch('/api/log_level');
    const d = await r.json();
    $('log-level-select').value = d.level || 'info';
  } catch (e) { /* ignore */ }
}

async function setLogLevel() {
  const level = $('log-level-select').value;
  const r = await fetch('/api/log_level', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level }),
  });
  const d = await r.json();
  toast(d.msg);
}

$('log-level-select').addEventListener('change', setLogLevel);
loadLogLevel();

refreshStatus();
initLogs();
setInterval(refreshStatus, 3000);

// ==== 平台登录 ====
let shownInputPrompt = null;
let shownQrUrl = null;

const LOGIN_STATUS_TEXT = { idle: '未登录', running: '登录中…', success: '已登录', failed: '失败' };

async function refreshLoginStatus() {
  try {
    const r = await fetch('/api/login/status');
    const d = await r.json();
    ['steam', 'buff', 'uu'].forEach(p => {
      const s = (d[p] || { status: 'idle' }).status;
      const badge = $('login-' + p + '-badge');
      const msg = $('login-' + p + '-msg');
      if (badge) {
        badge.textContent = LOGIN_STATUS_TEXT[s] || '未登录';
        badge.className = 'badge ' + (s === 'success' || s === 'running' ? 'running' : 'stopped');
      }
      if (msg) msg.textContent = (d[p] || {}).msg || '';
    });
  } catch (e) { /* ignore */ }
}

async function startLogin(platform) {
  const r = await fetch('/api/login/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ platform }),
  });
  const d = await r.json();
  toast(d.msg);
  refreshLoginStatus();
}

async function pollInteract() {
  try {
    const r = await fetch('/api/login/interact');
    const d = await r.json();
    const req = d.request;
    const box = $('login-interact');
    if (!req) {
      shownInputPrompt = null;
      shownQrUrl = null;
      if (box) box.style.display = 'none';
      return;
    }
    if (req.type === 'input') {
      if (box) box.style.display = 'block';
      $('interact-qrcode').style.display = 'none';
      if (shownInputPrompt !== req.prompt) {
        shownInputPrompt = req.prompt;
        $('interact-input').style.display = 'block';
        $('interact-prompt').textContent = req.prompt || '请输入：';
        $('interact-value').value = '';
        $('interact-value').focus();
      }
    } else if (req.type === 'qrcode') {
      if (box) box.style.display = 'block';
      $('interact-input').style.display = 'none';
      $('interact-qrcode').style.display = 'block';
      if (shownQrUrl !== req.url) {
        shownQrUrl = req.url;
        const qr = await fetch('/api/login/qrcode?url=' + encodeURIComponent(req.url));
        const qd = await qr.json();
        if (qd.ok) $('interact-qr-img').src = qd.image;
      }
    }
  } catch (e) { /* ignore */ }
}

async function submitInteract() {
  const value = $('interact-value').value;
  await fetch('/api/login/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  });
  $('interact-value').value = '';
  shownInputPrompt = null;
}

document.querySelectorAll('[data-login]').forEach(btn => {
  btn.addEventListener('click', () => startLogin(btn.dataset.login));
});
$('interact-submit').addEventListener('click', submitInteract);
$('interact-value').addEventListener('keydown', e => { if (e.key === 'Enter') submitInteract(); });

setInterval(refreshLoginStatus, 2000);
setInterval(pollInteract, 1000);

// ==== 配置表格 ====
function fmtDefault(v) {
  if (v == null) return '';
  if (Array.isArray(v)) return JSON.stringify(v);
  return String(v);
}

function makeControl(f) {
  if (f.type === 'bool') {
    const sel = document.createElement('select');
    sel.className = 'cfg-input';
    const o1 = document.createElement('option');
    o1.value = 'true'; o1.textContent = 'true';
    const o2 = document.createElement('option');
    o2.value = 'false'; o2.textContent = 'false';
    sel.appendChild(o1);
    sel.appendChild(o2);
    sel.value = f.value ? 'true' : 'false';
    return sel;
  }
  const input = document.createElement('input');
  input.className = 'cfg-input';
  if (f.type === 'int' || f.type === 'float') {
    input.type = 'number';
    if (f.type === 'float') input.step = 'any';
    input.value = (f.value == null ? '' : f.value);
  } else if (f.type === 'array') {
    input.type = 'text';
    input.value = JSON.stringify(f.value);
    input.placeholder = '["A", "B"]';
  } else {
    input.type = 'text';
    input.value = (f.value == null ? '' : f.value);
  }
  return input;
}

function renderConfigTable(groups) {
  const container = $('config-table');
  container.innerHTML = '';
  const tabsContainer = $('config-group-tabs');
  if (tabsContainer) tabsContainer.innerHTML = '';

  // 聚合：group 名 " · " 前部分是 tab 名，同 tab 的多个 group 归入同一面板
  const tabs = [];
  const groupsByTab = {};
  (groups || []).forEach(g => {
    const tabName = g.group.split(' · ')[0];
    if (!groupsByTab[tabName]) {
      groupsByTab[tabName] = [];
      tabs.push(tabName);
    }
    groupsByTab[tabName].push(g);
  });

  tabs.forEach((tabName, idx) => {
    if (tabsContainer) {
      const tabBtn = document.createElement('button');
      tabBtn.className = 'tab-btn' + (idx === 0 ? ' active' : '');
      tabBtn.textContent = tabName;
      tabBtn.dataset.tab = tabName;
      tabBtn.addEventListener('click', () => switchConfigTab(tabName));
      tabsContainer.appendChild(tabBtn);
    }
    const tabPanel = document.createElement('div');
    tabPanel.className = 'config-tab-panel';
    tabPanel.dataset.tab = tabName;
    if (idx !== 0) tabPanel.style.display = 'none';
    groupsByTab[tabName].forEach(g => {
      const groupDiv = document.createElement('div');
      groupDiv.className = 'config-group';
      const h3 = document.createElement('h3');
      h3.textContent = g.group;
      groupDiv.appendChild(h3);
      const table = document.createElement('table');
      table.className = 'cfg-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr><th>参数</th><th>当前值</th><th>默认值</th><th>可填值</th></tr>';
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      g.fields.forEach(f => {
        const tr = document.createElement('tr');
        tr.dataset.key = f.key;
        tr.dataset.type = f.type;
        const tdName = document.createElement('td');
        tdName.className = 'cfg-name';
        const label = document.createElement('div');
        label.className = 'cfg-label';
        label.textContent = f.label;
        const help = document.createElement('div');
        help.className = 'cfg-help';
        help.textContent = f.help || '';
        tdName.appendChild(label);
        tdName.appendChild(help);
        const tdVal = document.createElement('td');
        tdVal.appendChild(makeControl(f));
        const tdDef = document.createElement('td');
        tdDef.className = 'cfg-default';
        tdDef.textContent = fmtDefault(f.default);
        const tdOpt = document.createElement('td');
        tdOpt.className = 'cfg-options';
        tdOpt.textContent = f.options || '';
        tr.appendChild(tdName);
        tr.appendChild(tdVal);
        tr.appendChild(tdDef);
        tr.appendChild(tdOpt);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      groupDiv.appendChild(table);
      tabPanel.appendChild(groupDiv);
    });
    container.appendChild(tabPanel);
  });
}

let currentConfigTab = null;

function switchConfigTab(tabName) {
  currentConfigTab = tabName;
  document.querySelectorAll('#config-group-tabs .tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tabName);
  });
  document.querySelectorAll('#config-table .config-tab-panel').forEach(p => {
    p.style.display = (p.dataset.tab === tabName ? 'block' : 'none');
  });
  document.querySelectorAll('.config-tab-panel-extra').forEach(p => {
    p.style.display = (p.dataset.tab === tabName ? 'block' : 'none');
  });
}

async function loadConfigTable() {
  try {
    const r = await fetch('/api/config/table');
    const d = await r.json();
    renderConfigTable(d.groups);
    if (d.groups && d.groups.length) {
      switchConfigTab(d.groups[0].group.split(' · ')[0]);
    }
  } catch (e) { /* ignore */ }
}

function collectTableValues() {
  const values = {};
  document.querySelectorAll('#config-table tbody tr').forEach(tr => {
    const key = tr.dataset.key;
    const type = tr.dataset.type;
    const control = tr.querySelector('.cfg-input');
    if (!control) return;
    if (type === 'bool') {
      values[key] = (control.value === 'true');
    } else {
      values[key] = control.value;
    }
  });
  return values;
}

async function saveTable() {
  const values = collectTableValues();
  const r = await fetch('/api/config/table/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
  const d = await r.json();
  showMsg('config-table-msg', d.msg, d.ok);
  if (d.ok) loadConfigTable();
}

function showConfigView(mode) {
  $('config-table-view').style.display = (mode === 'table' ? 'block' : 'none');
  $('config-text-view').style.display = (mode === 'text' ? 'block' : 'none');
  $('btn-view-table').classList.toggle('active', mode === 'table');
  $('btn-view-text').classList.toggle('active', mode === 'text');
  document.querySelectorAll('.config-tab-panel-extra').forEach(p => {
    p.style.display = (mode === 'table' && currentConfigTab === p.dataset.tab ? 'block' : 'none');
  });
  if (mode === 'table') loadConfigTable();
  if (mode === 'text') loadConfig();
}

$('btn-view-table').addEventListener('click', () => showConfigView('table'));
$('btn-view-text').addEventListener('click', () => showConfigView('text'));
$('btn-save-table').addEventListener('click', saveTable);

// ==== BUFF 库存 ====
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtNum(v) {
  return (v == null || v === '') ? '—' : v;
}

async function loadBuffInventory() {
  const msg = $('buff-inventory-msg');
  msg.textContent = '加载中…';
  msg.className = 'msg';
  try {
    const r = await fetch('/api/buff/inventory');
    const d = await r.json();
    if (!d.ok) {
      msg.textContent = d.msg || '查询失败';
      msg.className = 'msg err';
      $('buff-inventory-table').innerHTML = '';
      return;
    }
    msg.textContent = '';
    const bal = d.balance || {};
    const balEl = $('buff-balance');
    if (balEl) {
      const cash = bal.cash_amount != null ? bal.cash_amount : '';
      balEl.textContent = cash !== '' ? ('余额 ¥' + cash) : '';
    }
    renderBuffInventory(d.items);
  } catch (e) {
    msg.textContent = '查询失败';
    msg.className = 'msg err';
  }
}

function renderBuffInventory(items) {
  const container = $('buff-inventory-table');
  container.innerHTML = '';
  if (!items || !items.length) {
    container.innerHTML = '<p class="hint">库存为空</p>';
    return;
  }
  let html = '<table class="cfg-table"><thead><tr><th>商品</th><th>备注(购入价)</th><th>求购价</th><th>在售价(最低)</th><th>自己售价</th><th>最新成交价</th></tr></thead><tbody>';
  items.forEach(it => {
    const name = escapeHtml(it.name || it.market_hash_name || '');
    const onSaleText = it.on_sale ? ((it.sell_order_price != null) ? it.sell_order_price : '已上架') : '未上架';
    const dealText = (it.deal_price != null) ? it.deal_price : '—';
    html += '<tr>' +
      '<td class="cfg-name">' + name + '</td>' +
      '<td><input class="cfg-input buff-remark-input" data-assetid="' + it.assetid + '" value="' + escapeHtml(it.remark || '') + '" maxlength="40" placeholder="记录购入价"></td>' +
      '<td>' + fmtNum(it.buy_max_price) + '</td>' +
      '<td>' + fmtNum(it.sell_min_price) + '</td>' +
      '<td>' + escapeHtml(String(onSaleText)) + '</td>' +
      '<td>' + dealText + '</td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
  container.querySelectorAll('.buff-remark-input').forEach(input => {
    input.addEventListener('change', () => saveRemark(input));
  });
}

async function saveRemark(input) {
  const assetid = input.dataset.assetid;
  const remark = input.value;
  try {
    const r = await fetch('/api/buff/remark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assetid, remark }),
    });
    const d = await r.json();
    if (d.ok) {
      toast(d.msg);
    } else {
      toast(d.msg || '保存失败');
    }
  } catch (e) {
    toast('保存失败');
  }
}

$('btn-buff-inventory-refresh').addEventListener('click', loadBuffInventory);
loadBuffInventory();

// ==== BUFF 自动交易 ====
async function searchBuff() {
  const key = $('buff-search-key').value.trim();
  if (!key) { toast('请输入关键词'); return; }
  const source = $('buff-search-source').value;
  const r = await fetch('/api/buff/search?key=' + encodeURIComponent(key) + '&source=' + source);
  const d = await r.json();
  if (!d.ok) { toast(d.msg || '搜索失败'); return; }
  renderBuffSearchResult(d.items);
}

function renderBuffSearchResult(items) {
  const container = $('buff-search-result');
  container.innerHTML = '';
  if (!items || !items.length) {
    container.innerHTML = '<p class="hint">无搜索结果</p>';
    return;
  }
  let html = '<table class="cfg-table"><thead><tr><th>勾选</th><th>商品</th><th>goods_id</th><th>求购价</th><th>在售价(最低)</th><th>自己售价</th><th>最新成交价</th><th>最高购入价</th><th>最低售价</th><th>购入数量</th><th>售出数量</th></tr></thead><tbody>';
  items.forEach(it => {
    const gid = it.goods_id != null ? it.goods_id : '';
    const name = escapeHtml(it.name || it.market_hash_name || '');
    const ownPrice = (it.sell_order_price != null) ? it.sell_order_price : '-';
    html += '<tr>' +
      '<td><input type="checkbox" class="buff-trade-check" data-gid="' + gid + '" data-name="' + escapeHtml(it.name || '') + '" data-mhn="' + escapeHtml(it.market_hash_name || '') + '"></td>' +
      '<td class="cfg-name">' + name + '</td>' +
      '<td>' + gid + '</td>' +
      '<td>' + fmtNum(it.buy_max_price) + '</td>' +
      '<td>' + fmtNum(it.sell_min_price) + '</td>' +
      '<td>' + escapeHtml(String(ownPrice)) + '</td>' +
      '<td>' + fmtNum(it.deal_price) + '</td>' +
      '<td><input class="cfg-input buff-max-buy" type="number" step="0.01" placeholder="如 5.00"></td>' +
      '<td><input class="cfg-input buff-min-sell" type="number" step="0.01" placeholder="如 6.00"></td>' +
      '<td><input class="cfg-input buff-buy-count" type="number" step="1" placeholder="0"></td>' +
      '<td><input class="cfg-input buff-sell-count" type="number" step="1" placeholder="0"></td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

function configFieldsOnly(item) {
  return {
    goods_id: item.goods_id,
    name: item.name,
    market_hash_name: item.market_hash_name,
    max_buy_price: item.max_buy_price,
    min_sell_price: item.min_sell_price,
    buy_count: item.buy_count,
    sell_count: item.sell_count,
  };
}

async function saveConfig(config) {
  const r = await fetch('/api/buff/trade/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config }),
  });
  const d = await r.json();
  showMsg('buff-trade-msg', d.msg, d.ok);
  if (d.ok) loadTradeConfigList();
}

async function saveTradeConfig() {
  const newItems = [];
  document.querySelectorAll('#buff-search-result tbody tr').forEach(tr => {
    const check = tr.querySelector('.buff-trade-check');
    if (!check || !check.checked) return;
    newItems.push({
      goods_id: check.dataset.gid,
      name: check.dataset.name,
      market_hash_name: check.dataset.mhn,
      max_buy_price: tr.querySelector('.buff-max-buy').value,
      min_sell_price: tr.querySelector('.buff-min-sell').value,
      buy_count: tr.querySelector('.buff-buy-count').value,
      sell_count: tr.querySelector('.buff-sell-count').value,
    });
  });
  if (!newItems.length) { toast('未勾选任何饰品'); return; }
  // 追加：GET 已有配置，按 goods_id 合并（已存在则更新，新则追加）
  const r0 = await fetch('/api/buff/trade/config');
  const d0 = await r0.json();
  const map = {};
  (d0.config || []).forEach(c => { map[String(c.goods_id)] = configFieldsOnly(c); });
  newItems.forEach(c => { map[String(c.goods_id)] = c; });
  await saveConfig(Object.values(map));
}

async function loadTradeConfigList() {
  const r = await fetch('/api/buff/trade/config');
  const d = await r.json();
  if (!d.ok) return;
  renderTradeConfigList(d.config);
}

let tradeConfigCache = [];

function renderTradeConfigList(config) {
  tradeConfigCache = config || [];
  const container = $('buff-trade-config-list');
  if (!container) return;
  container.innerHTML = '';
  if (!config || !config.length) {
    container.innerHTML = '<p class="hint">暂无已配置的饰品</p>';
    return;
  }
  let html = '<table class="cfg-table"><thead><tr><th></th><th>商品</th><th>goods_id</th><th>求购价</th><th>在售价(最低)</th><th>自己售价</th><th>最新成交价</th><th>最高购入价</th><th>最低售价</th><th>购入数量</th><th>售出数量</th><th></th></tr></thead><tbody>';
  config.forEach(c => {
    const ownPrice = (c.sell_order_price != null) ? c.sell_order_price : '-';
    html += '<tr data-gid="' + c.goods_id + '">' +
      '<td><input type="checkbox" class="buff-config-check"></td>' +
      '<td class="cfg-name">' + escapeHtml(c.name || c.market_hash_name || '') + '</td>' +
      '<td>' + (c.goods_id != null ? c.goods_id : '') + '</td>' +
      '<td>' + fmtNum(c.buy_max_price) + '</td>' +
      '<td>' + fmtNum(c.sell_min_price) + '</td>' +
      '<td>' + escapeHtml(String(ownPrice)) + '</td>' +
      '<td>' + fmtNum(c.deal_price) + '</td>' +
      '<td><input class="cfg-input buff-cfg-max-buy" type="number" step="0.01" value="' + escapeHtml(String(c.max_buy_price || '')) + '"></td>' +
      '<td><input class="cfg-input buff-cfg-min-sell" type="number" step="0.01" value="' + escapeHtml(String(c.min_sell_price || '')) + '"></td>' +
      '<td><input class="cfg-input buff-cfg-buy-count" type="number" step="1" value="' + escapeHtml(String(c.buy_count != null ? c.buy_count : '')) + '"></td>' +
      '<td><input class="cfg-input buff-cfg-sell-count" type="number" step="1" value="' + escapeHtml(String(c.sell_count != null ? c.sell_count : '')) + '"></td>' +
      '<td><button class="btn-mini buff-del-btn" data-gid="' + c.goods_id + '">删除</button></td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
  container.querySelectorAll('.buff-cfg-max-buy, .buff-cfg-min-sell, .buff-cfg-buy-count, .buff-cfg-sell-count').forEach(input => {
    input.addEventListener('change', updateCounts);
  });
  container.querySelectorAll('.buff-del-btn').forEach(btn => {
    btn.addEventListener('click', () => deleteTradeItem(btn.dataset.gid));
  });
}

function updateCounts() {
  const config = [];
  document.querySelectorAll('#buff-trade-config-list tbody tr').forEach(tr => {
    const gid = tr.dataset.gid;
    const cached = tradeConfigCache.find(c => String(c.goods_id) === String(gid));
    if (!cached) return;
    config.push({
      goods_id: cached.goods_id,
      name: cached.name,
      market_hash_name: cached.market_hash_name,
      max_buy_price: tr.querySelector('.buff-cfg-max-buy').value,
      min_sell_price: tr.querySelector('.buff-cfg-min-sell').value,
      buy_count: tr.querySelector('.buff-cfg-buy-count').value,
      sell_count: tr.querySelector('.buff-cfg-sell-count').value,
    });
  });
  saveConfig(config);
}

async function deleteTradeItem(gid) {
  const config = tradeConfigCache
    .filter(c => String(c.goods_id) !== String(gid))
    .map(configFieldsOnly);
  await saveConfig(config);
}

async function deleteTradeChecked() {
  const checked = [];
  document.querySelectorAll('#buff-trade-config-list .buff-config-check:checked').forEach(chk => {
    checked.push(chk.closest('tr').dataset.gid);
  });
  if (!checked.length) { toast('未勾选任何饰品'); return; }
  const config = tradeConfigCache
    .filter(c => !checked.includes(String(c.goods_id)))
    .map(configFieldsOnly);
  await saveConfig(config);
}

async function clearTradeConfig() {
  if (!confirm('确定清空所有自动交易配置？')) return;
  await saveConfig([]);
}

async function scanTrade() {
  const dryRun = $('buff-trade-dryrun').checked;
  const r = await fetch('/api/buff/trade/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  const d = await r.json();
  if (!d.ok) { toast(d.msg || '扫描失败'); return; }
  renderTradeResult(d.results);
}

function renderTradeResult(results) {
  const container = $('buff-trade-result');
  container.innerHTML = '';
  if (!results || !results.length) {
    container.innerHTML = '<p class="hint">暂无配置的饰品，请先搜索勾选并保存</p>';
    return;
  }
  const decisionText = { buy: '买入', sell_to_bidder: '卖给求购者', list: '上架' };
  let html = '<table class="cfg-table"><thead><tr><th>商品</th><th>在售最低</th><th>求购最高</th><th>决策</th><th>操作价</th><th>说明</th><th>执行结果</th></tr></thead><tbody>';
  results.forEach(r => {
    const name = escapeHtml(r.name || '');
    const dec = r.decision ? (decisionText[r.decision] || r.decision) : '—';
    const execText = r.dry_run ? '（dry-run）' : (r.executed ? r.exec_msg : escapeHtml(r.exec_msg || '—'));
    html += '<tr>' +
      '<td class="cfg-name">' + name + '</td>' +
      '<td>' + fmtNum(r.sell_min_price) + '</td>' +
      '<td>' + fmtNum(r.buy_max_price) + '</td>' +
      '<td>' + dec + '</td>' +
      '<td>' + (r.action_price != null ? r.action_price : '—') + '</td>' +
      '<td class="cfg-help">' + escapeHtml(r.reason || '') + '</td>' +
      '<td>' + execText + '</td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

$('btn-buff-search').addEventListener('click', searchBuff);
$('btn-buff-trade-save').addEventListener('click', saveTradeConfig);
$('btn-buff-trade-scan').addEventListener('click', scanTrade);
$('btn-buff-trade-del-checked').addEventListener('click', deleteTradeChecked);
$('btn-buff-trade-clear').addEventListener('click', clearTradeConfig);
$('buff-search-key').addEventListener('keydown', e => { if (e.key === 'Enter') searchBuff(); });
loadTradeConfigList();

// ==== 扫描周期 ====
async function loadScanInterval() {
  const r = await fetch('/api/buff/trade/interval');
  const d = await r.json();
  if (d.ok) $('buff-scan-interval').value = d.interval || 0;
}
async function applyScanInterval() {
  const interval = $('buff-scan-interval').value || 0;
  const dryRun = $('buff-trade-dryrun').checked;
  const r = await fetch('/api/buff/trade/interval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interval: Number(interval), dry_run: dryRun }),
  });
  const d = await r.json();
  toast(d.msg);
}
$('btn-buff-scan-start').addEventListener('click', applyScanInterval);
loadScanInterval();

// 页面加载时自动检测缓存，刷新各平台登录状态
(async () => {
  try {
    await fetch('/api/login/refresh', { method: 'POST' });
    await refreshLoginStatus();
  } catch (e) { /* ignore */ }
})();

// ==== 账号信息：表单 ↔ 文本实时同步（不写文件） ====
let accSyncing = false;

function accFormToText() {
  if (accSyncing) return;
  accSyncing = true;
  const acc = {
    shared_secret: $('acc-shared').value,
    identity_secret: $('acc-identity').value,
    steam_username: $('acc-username').value,
    steam_password: $('acc-password').value,
  };
  $('account-editor').value = JSON.stringify(acc, null, 2);
  accSyncing = false;
}

function accTextToForm() {
  if (accSyncing) return true;
  try {
    const acc = JSON.parse($('account-editor').value);
    accSyncing = true;
    $('acc-shared').value = acc.shared_secret || '';
    $('acc-identity').value = acc.identity_secret || '';
    $('acc-username').value = acc.steam_username || '';
    $('acc-password').value = acc.steam_password || '';
    accSyncing = false;
    $('account-editor').classList.remove('editor-error');
    return true;
  } catch (e) {
    $('account-editor').classList.add('editor-error');
    return false;
  }
}

function showAccView(mode) {
  $('acc-form-view').style.display = (mode === 'form' ? 'block' : 'none');
  $('acc-text-view').style.display = (mode === 'text' ? 'block' : 'none');
  $('btn-acc-form').classList.toggle('active', mode === 'form');
  $('btn-acc-text').classList.toggle('active', mode === 'text');
  if (mode === 'text') accFormToText();
}

async function resetAccount() {
  if (!confirm('确定恢复为默认值？当前账号信息将被清空。')) return;
  const r = await fetch('/api/account/reset', { method: 'POST' });
  const d = await r.json();
  showMsg('account-msg', d.msg, d.ok);
  if (d.ok) loadConfig();
}

async function importAccount() {
  const file = $('acc-import-file').files[0];
  if (!file) return;
  const content = await file.text();
  const r = await fetch('/api/account/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  const d = await r.json();
  showMsg('account-msg', d.msg, d.ok);
  if (d.ok) loadConfig();
  $('acc-import-file').value = '';
}

async function exportAccount() {
  const r = await fetch('/api/account/export');
  const d = await r.json();
  if (!d.ok) { showMsg('account-msg', '导出失败', false); return; }
  const blob = new Blob([d.content], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = d.filename || 'steam_account_info.json5';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

['acc-username', 'acc-password', 'acc-shared', 'acc-identity'].forEach(id => {
  $(id).addEventListener('input', accFormToText);
});
$('account-editor').addEventListener('input', accTextToForm);
$('btn-acc-form').addEventListener('click', () => showAccView('form'));
$('btn-acc-text').addEventListener('click', () => showAccView('text'));
$('btn-acc-reset').addEventListener('click', resetAccount);
$('btn-acc-import').addEventListener('click', () => $('acc-import-file').click());
$('acc-import-file').addEventListener('change', importAccount);
$('btn-acc-export').addEventListener('click', exportAccount);

// ==== UU 库存 ====
async function loadUuInventory() {
  const msg = $('uu-inventory-msg');
  msg.textContent = '加载中…';
  msg.className = 'msg';
  try {
    const r = await fetch('/api/uu/inventory');
    const d = await r.json();
    if (!d.ok) {
      msg.textContent = d.msg || '查询失败';
      msg.className = 'msg err';
      $('uu-inventory-table').innerHTML = '';
      return;
    }
    msg.textContent = '';
    renderUuInventory(d.items);
  } catch (e) {
    msg.textContent = '查询失败';
    msg.className = 'msg err';
  }
}

function renderUuInventory(items) {
  const container = $('uu-inventory-table');
  container.innerHTML = '';
  if (!items || !items.length) {
    container.innerHTML = '<p class="hint">库存为空</p>';
    return;
  }
  let html = '<table class="cfg-table"><thead><tr><th>商品</th><th>template_id</th><th>购入价</th><th>参考价</th><th>求购价</th><th>在售价(最低)</th><th>状态</th></tr></thead><tbody>';
  items.forEach(it => {
    const name = escapeHtml(it.name || '');
    html += '<tr>' +
      '<td class="cfg-name">' + name + '</td>' +
      '<td>' + (it.template_id != null ? it.template_id : '') + '</td>' +
      '<td>' + fmtNum(it.buy_price) + '</td>' +
      '<td>' + fmtNum(it.mark_price) + '</td>' +
      '<td>' + fmtNum(it.buy_max_price) + '</td>' +
      '<td>' + fmtNum(it.sell_min_price) + '</td>' +
      '<td>' + (it.on_sale ? '已上架' : '未上架') + '</td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

$('btn-uu-inventory-refresh').addEventListener('click', loadUuInventory);

// ==== UU 自动交易 ====
async function searchUu() {
  const key = $('uu-search-key').value.trim();
  if (!key) { toast('请输入关键词'); return; }
  const r = await fetch('/api/uu/search?key=' + encodeURIComponent(key));
  const d = await r.json();
  if (!d.ok) { toast(d.msg || '搜索失败'); return; }
  renderUuSearchResult(d.items);
}

function renderUuSearchResult(items) {
  const container = $('uu-search-result');
  container.innerHTML = '';
  if (!items || !items.length) {
    container.innerHTML = '<p class="hint">无搜索结果</p>';
    return;
  }
  let html = '<table class="cfg-table"><thead><tr><th>勾选</th><th>商品</th><th>template_id</th><th>求购价</th><th>在售价(最低)</th><th>最高购入价</th><th>最低售价</th><th>购入数量</th><th>售出数量</th></tr></thead><tbody>';
  items.forEach(it => {
    const tid = it.template_id != null ? it.template_id : '';
    const name = escapeHtml(it.name || it.market_hash_name || '');
    html += '<tr>' +
      '<td><input type="checkbox" class="uu-trade-check" data-tid="' + tid + '" data-name="' + escapeHtml(it.name || '') + '" data-mhn="' + escapeHtml(it.market_hash_name || '') + '"></td>' +
      '<td class="cfg-name">' + name + '</td>' +
      '<td>' + tid + '</td>' +
      '<td>' + fmtNum(it.buy_max_price) + '</td>' +
      '<td>' + fmtNum(it.sell_min_price) + '</td>' +
      '<td><input class="cfg-input uu-max-buy" type="number" step="0.01" placeholder="如 5.00"></td>' +
      '<td><input class="cfg-input uu-min-sell" type="number" step="0.01" placeholder="如 6.00"></td>' +
      '<td><input class="cfg-input uu-buy-count" type="number" step="1" placeholder="0"></td>' +
      '<td><input class="cfg-input uu-sell-count" type="number" step="1" placeholder="0"></td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

function uuConfigFieldsOnly(item) {
  return {
    template_id: item.template_id,
    name: item.name,
    market_hash_name: item.market_hash_name,
    max_buy_price: item.max_buy_price,
    min_sell_price: item.min_sell_price,
    buy_count: item.buy_count,
    sell_count: item.sell_count,
  };
}

async function saveUuConfig(config) {
  const r = await fetch('/api/uu/trade/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config }),
  });
  const d = await r.json();
  showMsg('uu-trade-msg', d.msg, d.ok);
  if (d.ok) loadUuTradeConfigList();
}

async function saveUuTradeConfig() {
  const newItems = [];
  document.querySelectorAll('#uu-search-result tbody tr').forEach(tr => {
    const check = tr.querySelector('.uu-trade-check');
    if (!check || !check.checked) return;
    newItems.push({
      template_id: check.dataset.tid,
      name: check.dataset.name,
      market_hash_name: check.dataset.mhn,
      max_buy_price: tr.querySelector('.uu-max-buy').value,
      min_sell_price: tr.querySelector('.uu-min-sell').value,
      buy_count: tr.querySelector('.uu-buy-count').value,
      sell_count: tr.querySelector('.uu-sell-count').value,
    });
  });
  if (!newItems.length) { toast('未勾选任何饰品'); return; }
  const r0 = await fetch('/api/uu/trade/config');
  const d0 = await r0.json();
  const map = {};
  (d0.config || []).forEach(c => { map[String(c.template_id)] = uuConfigFieldsOnly(c); });
  newItems.forEach(c => { map[String(c.template_id)] = c; });
  await saveUuConfig(Object.values(map));
}

async function loadUuTradeConfigList() {
  const r = await fetch('/api/uu/trade/config');
  const d = await r.json();
  if (!d.ok) return;
  renderUuTradeConfigList(d.config);
}

let uuTradeConfigCache = [];

function renderUuTradeConfigList(config) {
  uuTradeConfigCache = config || [];
  const container = $('uu-trade-config-list');
  if (!container) return;
  container.innerHTML = '';
  if (!config || !config.length) {
    container.innerHTML = '<p class="hint">暂无已配置的饰品</p>';
    return;
  }
  let html = '<table class="cfg-table"><thead><tr><th></th><th>商品</th><th>template_id</th><th>求购价</th><th>在售价(最低)</th><th>最高购入价</th><th>最低售价</th><th>购入数量</th><th>售出数量</th><th></th></tr></thead><tbody>';
  config.forEach(c => {
    html += '<tr data-tid="' + c.template_id + '">' +
      '<td><input type="checkbox" class="uu-config-check"></td>' +
      '<td class="cfg-name">' + escapeHtml(c.name || c.market_hash_name || '') + '</td>' +
      '<td>' + (c.template_id != null ? c.template_id : '') + '</td>' +
      '<td>' + fmtNum(c.buy_max_price) + '</td>' +
      '<td>' + fmtNum(c.sell_min_price) + '</td>' +
      '<td><input class="cfg-input uu-cfg-max-buy" type="number" step="0.01" value="' + escapeHtml(String(c.max_buy_price || '')) + '"></td>' +
      '<td><input class="cfg-input uu-cfg-min-sell" type="number" step="0.01" value="' + escapeHtml(String(c.min_sell_price || '')) + '"></td>' +
      '<td><input class="cfg-input uu-cfg-buy-count" type="number" step="1" value="' + escapeHtml(String(c.buy_count != null ? c.buy_count : '')) + '"></td>' +
      '<td><input class="cfg-input uu-cfg-sell-count" type="number" step="1" value="' + escapeHtml(String(c.sell_count != null ? c.sell_count : '')) + '"></td>' +
      '<td><button class="btn-mini uu-del-btn" data-tid="' + c.template_id + '">删除</button></td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
  container.querySelectorAll('.uu-cfg-max-buy, .uu-cfg-min-sell, .uu-cfg-buy-count, .uu-cfg-sell-count').forEach(input => {
    input.addEventListener('change', updateUuCounts);
  });
  container.querySelectorAll('.uu-del-btn').forEach(btn => {
    btn.addEventListener('click', () => deleteUuTradeItem(btn.dataset.tid));
  });
}

function updateUuCounts() {
  const config = [];
  document.querySelectorAll('#uu-trade-config-list tbody tr').forEach(tr => {
    const tid = tr.dataset.tid;
    const cached = uuTradeConfigCache.find(c => String(c.template_id) === String(tid));
    if (!cached) return;
    config.push({
      template_id: cached.template_id,
      name: cached.name,
      market_hash_name: cached.market_hash_name,
      max_buy_price: tr.querySelector('.uu-cfg-max-buy').value,
      min_sell_price: tr.querySelector('.uu-cfg-min-sell').value,
      buy_count: tr.querySelector('.uu-cfg-buy-count').value,
      sell_count: tr.querySelector('.uu-cfg-sell-count').value,
    });
  });
  saveUuConfig(config);
}

async function deleteUuTradeItem(tid) {
  const config = uuTradeConfigCache
    .filter(c => String(c.template_id) !== String(tid))
    .map(uuConfigFieldsOnly);
  await saveUuConfig(config);
}

async function deleteUuTradeChecked() {
  const checked = [];
  document.querySelectorAll('#uu-trade-config-list .uu-config-check:checked').forEach(chk => {
    checked.push(chk.closest('tr').dataset.tid);
  });
  if (!checked.length) { toast('未勾选任何饰品'); return; }
  const config = uuTradeConfigCache
    .filter(c => !checked.includes(String(c.template_id)))
    .map(uuConfigFieldsOnly);
  await saveUuConfig(config);
}

async function clearUuTradeConfig() {
  if (!confirm('确定清空所有悠悠有品自动交易配置？')) return;
  await saveUuConfig([]);
}

async function scanUuTrade() {
  const dryRun = $('uu-trade-dryrun').checked;
  const r = await fetch('/api/uu/trade/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  const d = await r.json();
  if (!d.ok) { toast(d.msg || '扫描失败'); return; }
  renderUuTradeResult(d.results);
}

function renderUuTradeResult(results) {
  const container = $('uu-trade-result');
  container.innerHTML = '';
  if (!results || !results.length) {
    container.innerHTML = '<p class="hint">暂无配置的饰品，请先搜索勾选并保存</p>';
    return;
  }
  const decisionText = { buy: '发求购单', list_to_bidder: '上架(跟求购)', list: '上架(跟在售)' };
  let html = '<table class="cfg-table"><thead><tr><th>商品</th><th>在售最低</th><th>求购最高</th><th>决策</th><th>操作价</th><th>说明</th><th>执行结果</th></tr></thead><tbody>';
  results.forEach(r => {
    const name = escapeHtml(r.name || '');
    const dec = r.decision ? (decisionText[r.decision] || r.decision) : '—';
    const execText = r.dry_run ? '（dry-run）' : (r.executed ? r.exec_msg : escapeHtml(r.exec_msg || '—'));
    html += '<tr>' +
      '<td class="cfg-name">' + name + '</td>' +
      '<td>' + fmtNum(r.sell_min_price) + '</td>' +
      '<td>' + fmtNum(r.buy_max_price) + '</td>' +
      '<td>' + dec + '</td>' +
      '<td>' + (r.action_price != null ? r.action_price : '—') + '</td>' +
      '<td class="cfg-help">' + escapeHtml(r.reason || '') + '</td>' +
      '<td>' + execText + '</td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

$('btn-uu-search').addEventListener('click', searchUu);
$('btn-uu-trade-save').addEventListener('click', saveUuTradeConfig);
$('btn-uu-trade-scan').addEventListener('click', scanUuTrade);
$('btn-uu-trade-del-checked').addEventListener('click', deleteUuTradeChecked);
$('btn-uu-trade-clear').addEventListener('click', clearUuTradeConfig);
$('uu-search-key').addEventListener('keydown', e => { if (e.key === 'Enter') searchUu(); });
loadUuTradeConfigList();

// ==== UU 扫描周期 ====
async function loadUuScanInterval() {
  const r = await fetch('/api/uu/trade/interval');
  const d = await r.json();
  if (d.ok) $('uu-scan-interval').value = d.interval || 0;
}
async function applyUuScanInterval() {
  const interval = $('uu-scan-interval').value || 0;
  const dryRun = $('uu-trade-dryrun').checked;
  const r = await fetch('/api/uu/trade/interval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interval: Number(interval), dry_run: dryRun }),
  });
  const d = await r.json();
  toast(d.msg);
}
$('btn-uu-scan-start').addEventListener('click', applyUuScanInterval);
loadUuScanInterval();
