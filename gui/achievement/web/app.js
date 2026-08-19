let bridge;
let state = {};
let page = 'coast';
let chapter = '全部';
let shellQuery = '';
let shellStatus = '全部';
let shellSort = 'recent';
let shellLayout = 'grid';
let weeklyRows = [];
let coastStatIndex = 0;
let musicSecondsForStat = 0;
const fmtMusicDuration = value => {
  const totalMinutes = Math.max(0, Math.floor(Number(value || 0) / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours}h${String(minutes).padStart(2, '0')}min` : `${minutes}min`;
};

const art = { shell: '◔', star: '✦', bottle: '◒', boat: '△', scope: '◉', anchor: '⚓', pearl: '○', music: '♫' };
const twoDigit = value => value < 10 ? `0${value}` : String(value);
const esc = value => String(value || '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const fmtSec = value => {
  const seconds = Math.max(0, Math.floor(value || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  if (hours) return `${hours}h ${minutes < 10 ? '0' : ''}${minutes}min`;
  return `${minutes}min`;
};
const stat = (label, value, sub = '') => {
  coastStatIndex += 1;
  if (coastStatIndex === 4) {
    return `<section class="card metric-card"><div class="label">耳机另一半</div><div class="number">${fmtMusicDuration(musicSecondsForStat)}</div><div class="label">陪你听歌的总时长</div></section>`;
  }
  return `<section class="card metric-card"><div class="label">${label}</div><div class="number">${value}</div><div class="label">${sub}</div></section>`;
};
function hero(title, copy, extra = '') { return `<section class="hero"><div><p class="eyebrow">莲心</p><h1>${title}</h1><p>${copy}</p></div>${extra}</section>`; }

function dateKey(value) { return String(value || '').slice(0, 10); }
function localDate(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? dateKey(value) : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }); }
function weeklyData() {
  const rows = state.daily_metrics || [];
  const lookup = {};
  rows.forEach(row => { lookup[row.local_date] = row; });
  const today = new Date();
  const mondayOffset = (today.getDay() + 6) % 7;
  const monday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - mondayOffset);
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + index);
    const key = `${date.getFullYear()}-${twoDigit(date.getMonth() + 1)}-${twoDigit(date.getDate())}`;
    const row = lookup[key] || {};
    return { date: key, label: `${date.getMonth() + 1}/${date.getDate()}`, interactions: Number(row.active_events || 0), presence: Number(row.presence_seconds || 0) };
  });
}
function recentEvents() {
  return (state.events || []).slice(0, 5).map(event => `<article class="event recent-event" data-journal-event="${esc(event.event_id || event.id || '')}"><time>${esc((event.occurred_at || '').slice(0, 16).replace('T', ' '))}</time><h3>${esc(event.title || '一段共同旅程')}</h3><p>${esc(event.summary || '这一刻被轻轻记录在数据潮汐里。')}</p></article>`).join('') || '<p class="empty-state">潮水刚刚开始，新的共同回忆会慢慢写在这里。</p>';
}
function coast() {
  coastStatIndex = 0;
  musicSecondsForStat = Number((state.metrics || {}).music_seconds || 0);
  const today = state.today || {};
  const metrics = state.metrics || {};
  const achievements = state.achievements || [];
  const next = achievements.find(item => !item.unlocked_at) || {};
  const recent = achievements.filter(item => item.unlocked_at).sort((a, b) => (b.unlocked_at || '').localeCompare(a.unlocked_at || ''))[0];
  weeklyRows = weeklyData();
  return hero('海岸概览', '时间不会说话，但它会留下每一次陪伴。', `<div class="hero-stats"><div class="glass"><b>${state.unlocked_count || 0}</b><span>已拾起的贝壳</span></div><div class="glass"><b>${metrics.days_since_meet || 0}</b><span>一起走过的天</span></div></div>`) +
    `<div class="stats metric-grid">${stat('今日陪伴', fmtSec(metrics.today_seconds), '前台可互动时长')}${stat('累计陪伴', fmtSec(metrics.seconds), '历史前台可互动时长')}${stat('活跃天数', `近 30 天 ${state.active_days_30 || 0} 天`, '发生过有效互动')}${stat('已拾贝壳', `${state.unlocked_count || 0} / ${state.total_count || 0} 枚`, '成就解锁进度')}</div>` +
    `<section class="card weekly-card"><div class="section-heading"><div><h2 class="section-title">本周陪伴与互动趋势</h2><p class="label">横轴为本周日期，纵轴为每日有效互动次数。</p></div><div class="chart-legend"><i></i><span>有效互动</span></div></div><div class="chart-wrap"><canvas id="weekly-chart" class="chart" aria-label="本周有效互动趋势图"></canvas><div id="weekly-tooltip" class="chart-tooltip" hidden></div></div><div class="chart-axis-note"><span>互动次数</span><span>本周</span></div></section>` +
    `<div class="coast-lower"><section class="card recent-events"><div class="section-heading"><h2 class="section-title">最近共同事件</h2><span class="label">最多显示 5 条</span></div>${recentEvents()}</section><aside class="coast-aside"><section class="card next-shell"><div class="label">下一枚贝壳</div><h2>${esc(next.title || '慢慢相遇')}</h2><div class="progress"><i style="width:${next.target ? Math.round(next.current / next.target * 100) : 0}%"></i></div><p class="label">${next.target ? `${next.current} / ${next.target} 枚` : '每一次相伴都会留下痕迹'}</p></section><section class="card recent-unlock"><div class="label">最近获得</div><h2>${recent ? esc(recent.title) : '还在海边等候'}</h2><p class="label">${recent ? esc(recent.description) : '第一枚贝壳会在相遇后出现。'}</p><button class="art-link" data-go="shells">查看贝壳收藏</button></section></aside></div>`;
}
function shells() {
  const all = state.achievements || [];
  const chapters = ['全部', ...new Set(all.map(item => item.chapter))];
  let items = all.filter(item => (chapter === '全部' || item.chapter === chapter) && (!shellQuery || item.title.includes(shellQuery) || item.description.includes(shellQuery)));
  if (shellStatus === '已解锁') items = items.filter(item => item.unlocked_at);
  if (shellStatus === '未解锁') items = items.filter(item => !item.unlocked_at);
  if (shellSort === 'recent') items.sort((a, b) => (b.unlocked_at || '').localeCompare(a.unlocked_at || ''));
  if (shellSort === 'progress') items.sort((a, b) => (b.current / Math.max(1, b.target)) - (a.current / Math.max(1, a.target)));
  const cards = items.map(item => `<article class="shell ${item.unlocked_at ? '' : 'locked'}"><div class="shell-art">${item.unlocked_at ? art[item.art] : '？'}</div><div class="tag">${esc(item.chapter)}</div><h3>${esc(item.title)}</h3><p>${item.unlocked_at ? esc(item.description) : `${item.current} / ${item.target} 枚 · ${esc(item.description)}`}</p>${item.unlocked_at ? `<p class="tag">${esc(item.unlocked_at.slice(0, 10))}</p>` : ''}</article>`).join('');
  return hero('贝壳收藏', '每一枚贝壳，都是我们一起走过的证明。', `<div class="hero-stats"><div class="glass"><b>${state.unlocked_count || 0} / ${state.total_count || 0}</b><span>已解锁</span></div></div>`) +
    `<div class="filters">${chapters.map(name => `<button class="${chapter === name ? 'active' : ''}" data-chapter="${esc(name)}">${esc(name)}</button>`).join('')}</div>` +
    `<div class="collection-toolbar"><input id="shell-search" value="${esc(shellQuery)}" placeholder="搜索贝壳名称或故事" aria-label="搜索贝壳"><select id="shell-status"><option>全部</option><option>已解锁</option><option>未解锁</option></select><select id="shell-sort"><option value="recent">最近获得</option><option value="progress">接近完成</option></select><button data-layout="grid" title="网格视图">▦</button><button data-layout="list" title="列表视图">☷</button></div><p class="label collection-count">显示 ${items.length} / ${all.length} 枚贝壳</p><section class="shell-grid ${shellLayout === 'list' ? 'list' : ''}">${cards || '<p class="empty-state">没有符合条件的贝壳。</p>'}</section>`;
}
function showAchievement(id) {
  const item = (state.achievements || []).find(entry => entry.id === id);
  if (!item) return;
  const unlocked = Boolean(item.unlocked_at);
  document.body.insertAdjacentHTML('beforeend', `<div class="drawer-backdrop" id="drawer-backdrop"><aside class="achievement-drawer" role="dialog"><button class="drawer-close" title="关闭">×</button><div class="drawer-art">${art[item.art] || '◔'}</div><p class="tag">${esc(item.chapter)}</p><h2>${esc(item.title)}</h2><p class="drawer-copy">${esc(item.description)}</p><div class="drawer-progress"><span>${item.current} / ${item.target} 枚</span><div class="progress"><i style="width:${item.target ? Math.round(item.current / item.target * 100) : 0}%"></i></div></div><p class="drawer-date">${unlocked ? `获得于 ${esc(item.unlocked_at.slice(0, 10))}` : `还差 ${Math.max(0, item.target - item.current)} 枚`}</p></aside></div>`);
  const overlay = document.querySelector('#drawer-backdrop');
  overlay.onclick = event => { if (event.target === overlay || event.target.closest('.drawer-close')) overlay.remove(); };
}
function showJourneyEvent(card) {
  const title = (card.querySelector('h3') || {}).textContent || '共同旅程';
  const summary = (card.querySelector('p') || {}).textContent || '这一刻被轻轻记录在数据潮汐里。';
  const time = (card.querySelector('time') || {}).textContent || '';
  document.body.insertAdjacentHTML('beforeend', `<div class="drawer-backdrop" id="event-drawer"><aside class="achievement-drawer" role="dialog"><button class="drawer-close" title="关闭">×</button><div class="drawer-art">⚓</div><p class="tag">最近共同事件</p><h2>${esc(title)}</h2><p class="drawer-copy">${esc(summary)}</p><p class="drawer-date">${esc(time)}</p></aside></div>`);
  const overlay = document.querySelector('#event-drawer');
  overlay.onclick = event => { if (event.target === overlay || event.target.closest('.drawer-close')) overlay.remove(); };
}
function drawWeeklyChart() {
  const canvas = document.querySelector('#weekly-chart');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, rect.width);
  const height = Math.max(250, rect.height);
  canvas.width = width * dpr; canvas.height = height * dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  const left = 48, right = 16, top = 18, bottom = 40;
  const chartWidth = width - left - right, chartHeight = height - top - bottom;
  const max = Math.max(4, ...weeklyRows.map(row => row.interactions));
  ctx.clearRect(0, 0, width, height);
  ctx.font = '16px Microsoft YaHei UI, Microsoft YaHei, sans-serif';
  ctx.strokeStyle = '#eadac5'; ctx.fillStyle = '#765f50'; ctx.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const y = top + chartHeight * index / 4;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillText(String(Math.round(max * (4 - index) / 4)), 8, y + 5);
  }
  const point = index => ({ x: left + chartWidth * index / 6, y: top + chartHeight - weeklyRows[index].interactions / max * chartHeight });
  ctx.beginPath(); weeklyRows.forEach((row, index) => { const p = point(index); index ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); }); ctx.lineTo(left + chartWidth, top + chartHeight); ctx.lineTo(left, top + chartHeight); ctx.closePath(); ctx.fillStyle = 'rgba(141,185,187,.24)'; ctx.fill();
  ctx.beginPath(); weeklyRows.forEach((row, index) => { const p = point(index); index ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); }); ctx.strokeStyle = '#6ea6aa'; ctx.lineWidth = 3; ctx.stroke();
  weeklyRows.forEach((row, index) => { const p = point(index); ctx.beginPath(); ctx.arc(p.x, p.y, 4, 0, Math.PI * 2); ctx.fillStyle = '#fffaf2'; ctx.fill(); ctx.strokeStyle = '#6ea6aa'; ctx.lineWidth = 2; ctx.stroke(); ctx.fillStyle = '#765f50'; ctx.fillText(row.label, p.x - 15, height - 12); });
  const tooltip = document.querySelector('#weekly-tooltip');
  if (tooltip) canvas.onmousemove = event => { const bounds = canvas.getBoundingClientRect(); const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left - left) / chartWidth)); const index = Math.round(ratio * 6); const row = weeklyRows[index]; tooltip.hidden = false; tooltip.style.left = `${Math.min(width - 180, Math.max(8, point(index).x))}px`; tooltip.style.top = `${Math.max(8, point(index).y - 78)}px`; tooltip.innerHTML = `<b>${esc(row.date)}</b><br>有效互动：${row.interactions} 次<br>陪伴时长：${fmtSec(row.presence)}`; };
  if (tooltip) canvas.onmouseleave = () => { tooltip.hidden = true; };
}
function toast(message) { const node = document.createElement('div'); node.className = 'unlock-toast'; node.innerHTML = `<div><b>${esc(message)}</b></div>`; document.body.appendChild(node); setTimeout(() => node.remove(), 4200); }
function showUnlocks() { const fresh = state.new_unlocks || []; if (!fresh.length || !bridge) return; const toastNode = document.createElement('div'); toastNode.className = 'unlock-toast'; toastNode.innerHTML = `<span>${art[fresh[0].art] || '◔'}</span><div><b>拾起一枚贝壳</b><p>${esc(fresh[0].title)}${fresh.length > 1 ? `，还有 ${fresh.length - 1} 枚新回忆` : ''}</p></div>`; document.body.appendChild(toastNode); setTimeout(() => toastNode.remove(), 5200); bridge.mark_unlocks_read(JSON.stringify(fresh.map(item => item.id))); state.new_unlocks = []; }
function mountAvatarEcho() {
  const weekly = document.querySelector('.weekly-card');
  if (!weekly || document.querySelector('.avatar-echo-card')) return;
  const source = state.avatar_summary || (state.metrics || {}).avatar_detail || {};
  const items = [
    ['user_taps', '拍莲心'], ['user_headpats', '摸莲心'],
    ['counter_taps', '反拍'], ['counter_headpats', '反摸'],
  ];
  const node = document.createElement('section');
  node.className = 'card avatar-echo-card';
  node.innerHTML = `<div class="section-heading"><div><h2 class="section-title">互动回声</h2><p class="label">每一次靠近，都留下了一点回应。</p></div><span class="label">累计互动</span></div><div class="avatar-echo-grid">${items.map(([key, label]) => `<div class="avatar-echo-item"><span>${label}</span><b>${Number(source[key] || 0)}</b></div>`).join('')}</div>`;
  weekly.parentNode.insertBefore(node, weekly);
}
function bindCoast() { document.querySelectorAll('[data-journal-event]').forEach(card => { card.onclick = () => showJourneyEvent(card); }); document.querySelectorAll('[data-export]').forEach(button => { button.onclick = () => bridge.export_metrics(button.dataset.export, raw => { const result = JSON.parse(raw); toast(result.ok ? `统计文件已导出：${result.path}` : `导出失败：${result.error || '未知错误'}`); }); }); mountAvatarEcho(); drawWeeklyChart(); }
function bindShells() { const search = document.querySelector('#shell-search'); if (!search) return; search.onchange = () => { shellQuery = search.value.trim(); render(); }; document.querySelector('#shell-status').value = shellStatus; document.querySelector('#shell-status').onchange = event => { shellStatus = event.target.value; render(); }; document.querySelector('#shell-sort').value = shellSort; document.querySelector('#shell-sort').onchange = event => { shellSort = event.target.value; render(); }; document.querySelectorAll('[data-layout]').forEach(button => { button.onclick = () => { shellLayout = button.dataset.layout; render(); }; }); }
function render() { const content = document.querySelector('#content'); content.innerHTML = page === 'shells' ? shells() : coast(); document.querySelectorAll('#nav button').forEach(button => button.classList.toggle('active', button.dataset.page === page)); content.querySelectorAll('[data-go]').forEach(button => { button.onclick = () => { page = button.dataset.go; render(); }; }); content.querySelectorAll('[data-chapter]').forEach(button => { button.onclick = () => { chapter = button.dataset.chapter; render(); }; }); if (page === 'shells') bindShells(); else bindCoast(); }
function setState(raw) { try { state = JSON.parse(raw); render(); } catch (error) { const content = document.querySelector('#content'); content.innerHTML = '<section class="empty-state error-state"><h2>数据潮汐暂时无法展开</h2><p>本地统计数据读取失败，请重试。</p><button id="retry-state">重新读取</button></section>'; const retry = document.querySelector('#retry-state'); if (retry) retry.onclick = () => bridge.get_initial_state(setState); } }
if (typeof QWebChannel === 'undefined') { document.querySelector('#content').innerHTML = '<section class="empty-state error-state"><h2>数据潮汐需要在莲心窗口中打开</h2><p>当前页面没有连接本地数据桥接。</p></section>'; } else { new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.achievementBridge; bridge.get_initial_state(setState); document.querySelector('#nav').onclick = event => { const button = event.target.closest('[data-page]'); if (button) { page = button.dataset.page; render(); } }; document.querySelector('#close').onclick = () => bridge.request_close(); document.querySelector('#min').onclick = () => bridge.request_minimize(); document.querySelector('#full').onclick = () => bridge.request_fullscreen(); setInterval(() => bridge.refresh(setState), 15000); }); }
window.addEventListener('resize', drawWeeklyChart);
