const q = selector => document.querySelector(selector);
const qa = selector => [...document.querySelectorAll(selector)];
function on(selector, eventName, handler, options) {
  const node = q(selector);
  if (!node) {
    console.warn(`[TimeCapsule] missing element for ${eventName}: ${selector}`);
    return null;
  }
  node.addEventListener(eventName, handler, options);
  return node;
}
const iconForKind = { photo: 'camera', music: 'music', study: 'study', game: 'game', chat: 'chat', file: 'file', location: 'map' };
const collectionLabels = { photo: '图片', music: '音乐', study: '自习', game: '游戏', chat: '聊天', file: '文件', location: '地点' };
let bridge = null;
let state = null;
let currentDay = localDateKey(new Date());
let saveTimer = null;
let currentMemoryDate = '';
let activePage = 'today';
const loadedPages = new Set();
const dirtyPages = new Set();
let timelinePage = 1;
let timelinePageSize = 15;
let timelineDates = [];
let timelineAuthor = 'all';
let timelineQuery = '';
let timelineStartDate = '';
let timelineEndDate = '';
let museumPage = 1;
let museumQuery = '';
let museumSort = 'favorite';
let treePage = 1;
let treeFilter = 'all';
let treeQuery = '';
let treeSort = 'newest';
let treeArchived = false;
let currentTreeNoteId = 0;
let currentDetailDate = '';
let currentDetailAuthor = '';
let currentDayData = null;
let lastPersistedUserContent = null;
let defaultMediaDirectory = '';
let contributionSignature = '';
let lightboxScale = 1;
let lightboxRotation = 0;
let lightboxOffset = { x: 0, y: 0 };
let lightboxDragging = false;
let lightboxPointer = { x: 0, y: 0 };

function reportFrontendError(error, context = '运行') {
  const message = error?.stack || error?.message || String(error || '未知错误');
  console.error(`[TimeCapsule] ${context}: ${message}`);
  const loading = q('#loading-state');
  if (loading && !loading.classList.contains('hidden')) {
    loading.innerHTML = '<strong>书页没有顺利展开</strong><span>已记录前端错误，请关闭后重新打开时间胶囊。</span>';
  } else {
    showToast?.('时间胶囊刚刚遇到一个界面错误，详情已写入日志。');
  }
  hideLoadingState();
}

function hideLoadingState() {
  q('#loading-state')?.classList.add('hidden');
}

function updateBadge(selector, value) {
  const badge = q(selector);
  if (!badge) return;
  const count = Math.max(0, Number(value || 0));
  badge.textContent = count > 99 ? '99+' : String(count);
  badge.classList.toggle('hidden', count <= 0);
}

function updateTreeBadges(data = state || {}) {
  updateBadge('#tree-badge', data.tree_unread_count);
  updateBadge('#tree-replies-badge', data.tree_reply_unread_count);
}

window.addEventListener('error', event => reportFrontendError(event.error || event.message, '脚本异常'));
window.addEventListener('unhandledrejection', event => reportFrontendError(event.reason, '异步异常'));

function localDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function parse(payload, fallback = {}) {
  try { return typeof payload === 'string' ? JSON.parse(payload) : payload; }
  catch (_) { return fallback; }
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function displayDate(value) {
  if (!value) return '';
  const [year, month, day] = value.split('-').map(Number);
  return `${year}年${month}月${day}日`;
}

function weekday(value) {
  const names = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
  return names[new Date(`${value}T12:00:00`).getDay()];
}

function excerpt(item, length = 90) {
  const text = String(item?.content || item?.user_content || item?.lianxin_content || '').replace(/\s+/g, ' ').trim();
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function showToast(message) {
  const toast = q('#toast');
  toast.textContent = message;
  toast.classList.remove('hidden');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add('hidden'), 2400);
}

function call(method, ...args) {
  return new Promise(resolve => {
    if (!bridge || typeof bridge[method] !== 'function') return resolve(null);
    bridge[method](...args, result => resolve(parse(result, result)));
  });
}

function switchPage(page) {
  activePage = page;
  qa('.page').forEach(section => section.classList.toggle('active', section.id === `page-${page}`));
  qa('.c-sidebar-item').forEach(item => item.classList.toggle('selected', item.dataset.page === page));
  const labels = { today: '今天', corridor: '时间长廊', tree: '树洞', museum: '收藏馆', settings: '设置' };
  q('#topbar-title').textContent = labels[page] || '时间胶囊';
  q('.main-content').scrollTop = 0;
  if (page === 'settings') {
    console.info('[TimeCapsule] settings page selected');
    openSettings();
  } else if (page !== 'today') {
    if (loadedPages.has(page) && !dirtyPages.has(page)) renderPage(page);
    else loadPage(page);
  }
}

function render(data) {
  state = { ...(state || {}), ...(data || {}) };
  if (!state) return;
  updateTreeBadges(state);
  renderToday(state.today || {});
  const companion = state.companion || {};
  q('#companion-message').textContent = companion.message || '我会在这里陪你慢慢看。';
  q('#user-page-name').textContent = state.user_name || '主人';
  if (q('#filter-user-label')) q('#filter-user-label').textContent = state.user_name || '主人';
  q('#footer-status').textContent = `已经收藏 ${state.timeline_count || 0} 个日子`;
  if (state.ui_settings) {
    timelinePageSize = Number(state.ui_settings.timeline_page_size || 15);
    document.body.classList.toggle('animations-off', state.ui_settings.animations_enabled === false);
    document.body.classList.toggle('low-power', Boolean(state.ui_settings.low_power_mode));
  }
  q('#loading-state')?.classList.add('hidden');
}

async function loadPage(page, force = false) {
  if (!bridge || (loadedPages.has(page) && !dirtyPages.has(page) && !force)) return;
  let data;
  if (page === 'corridor') data = await call(
    'get_corridor_page', timelinePage, timelinePageSize, timelineAuthor,
    timelineQuery, timelineStartDate, timelineEndDate
  );
  else if (page === 'museum') data = await call('get_museum_page', museumPage, 12, museumQuery, museumSort);
  else if (page === 'tree') data = await call('get_tree_page', treePage, 20, treeFilter, treeQuery, treeSort, treeArchived);
  else data = await call('get_page_state', page);
  if (data?.ok === false) { showToast(data.error || '页面暂时无法读取。'); return; }
  if (data) applyPageState(page, data);
}

function applyPageState(page, data) {
  state = { ...(state || {}), ...(data || {}) };
  updateTreeBadges(state);
  loadedPages.add(page);
  dirtyPages.delete(page);
  if (activePage === page) renderPage(page);
}

function renderPage(page) {
  if (page === 'corridor') {
    ensureTimelineDateControls();
    renderContribution(state.contribution || {});
    renderCollections(q('#featured-collections'), state.recent_collections || [], true);
    renderTimeline(state.timeline_page || { items: [] });
  } else if (page === 'tree') {
    renderTree(state.tree_page || { items: [] });
  } else if (page === 'museum') {
    renderMuseum(state.museum_page || { items: [] });
  }
}

function renderToday(day) {
  currentDay = day.date || currentDay;
  q('#today-date').textContent = displayDate(currentDay);
  q('#today-weekday').textContent = weekday(currentDay);
  const editor = q('#user-paper');
  if (document.activeElement !== editor && editor.textContent !== (day.user_content || '')) editor.textContent = day.user_content || '';
  lastPersistedUserContent = day.user_content || '';
  const lianxin = q('#lianxin-paper');
  lianxin.textContent = day.lianxin_content || '今天还没有留下故事。\n\n但书页已经准备好了。';
  lianxin.classList.toggle('empty-paper', !day.lianxin_content);
  q('#lianxin-weather').textContent = day.weather || '';
  q('#seal-day span').textContent = day.sealed ? '今天已经封存' : '封存今天';
  q('#seal-day').disabled = Boolean(day.sealed);
  q('#seal-note').textContent = day.sealed ? '这一天已经被好好收进时间长廊，仍然可以继续留下笔迹。' : '封存后仍然可以继续留下笔迹。';
  renderTraces(day.traces || []);
  renderCollections(q('#today-collections'), (day.collections || []).filter(item => item.kind === 'photo'));
}

function renderTraces(traces) {
  const root = q('#trace-list');
  root.innerHTML = '';
  if (!traces.length) root.innerHTML = '<div class="empty-state">这一页还没有后来的笔迹。<br>时间会继续向下生长。</div>';
  traces.forEach(trace => {
    const card = document.createElement('article');
    card.className = `trace-card ${trace.author === 'lianxin' ? 'lianxin' : 'user'}`;
    card.innerHTML = `<small>${trace.author === 'lianxin' ? '莲心' : (state?.user_name || '主人')} · ${formatTime(trace.created_at)}</small><div>${escapeHtml(trace.content).replace(/\n/g, '<br>')}</div>`;
    root.appendChild(card);
  });
}

function collectionCard(item) {
  const card = document.createElement('article');
  card.className = 'collection-card';
  const icon = iconForKind[item.kind] || 'file';
  const title = item.title || collectionLabels[item.kind] || '共同收藏';
  const previewUri = item?.metadata?.thumbnail_uri || item.uri;
  const preview = item.kind === 'photo' && item.uri
    ? `<img src="${escapeHtml(toFileUrl(previewUri))}" alt="${escapeHtml(title)}" loading="lazy" decoding="async">`
    : `<span class="collection-icon"><svg><use href="#i-${icon}"/></svg></span>`;
  card.innerHTML = `${preview}<strong>${escapeHtml(title)}</strong><small>${item.capsule_date || formatTime(item.created_at)}</small>`;
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', `查看共同收藏：${title}`);
  let clickTimer = null;
  card.onclick = () => {
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => openAttachment(item), 230);
  };
  card.ondblclick = event => {
    if (item.kind !== 'photo' || !item.uri) return;
    clearTimeout(clickTimer);
    event.preventDefault(); event.stopPropagation(); openImageLightbox(item.uri, title);
  };
  card.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openAttachment(item); } };
  return card;
}

function renderCollections(root, items, featured = false) {
  root.innerHTML = '';
  if (!items.length) {
    if (featured) root.innerHTML = '<div class="empty-state">最近还没有共同收藏。<br>下一件值得留下的东西正在等待你们。</div>';
    return;
  }
  items.forEach(item => root.appendChild(collectionCard(item)));
}

function renderContribution(data) {
  const signature = JSON.stringify(data);
  if (signature === contributionSignature) return;
  contributionSignature = signature;
  const grid = q('#contribution-grid');
  const months = q('#contribution-months');
  grid.innerHTML = '';
  months.innerHTML = '';
  const gridFragment = document.createDocumentFragment();
  const monthFragment = document.createDocumentFragment();
  const seenMonths = new Set();
  (data.days || []).forEach((item, index) => {
    const week = Math.floor(index / 7) + 1;
    const parsed = new Date(`${item.date}T12:00:00`);
    const monthKey = `${parsed.getFullYear()}-${parsed.getMonth()}`;
    if (!seenMonths.has(monthKey)) {
      seenMonths.add(monthKey);
      const marker = document.createElement('span');
      marker.style.left = `${7 + (week - 1) * 21}px`;
      marker.textContent = parsed.getMonth() === 0 ? `${parsed.getFullYear()}年1月` : `${parsed.getMonth() + 1}月`;
      monthFragment.appendChild(marker);
    }
    const cell = document.createElement('button');
    cell.type = 'button';
    const level = item.value <= 0 ? 0 : Math.min(4, item.value);
    cell.className = `heat-cell level-${level}${item.future ? ' future' : ''}${item.date === localDateKey(new Date()) ? ' today' : ''}`;
    cell.style.gridColumn = String(week);
    cell.style.gridRow = String(index % 7 + 1);
    cell.dataset.date = item.date;
    cell.dataset.value = item.value;
    cell.dataset.future = item.future ? '1' : '0';
    const detail = item.future ? '未来的书页' : item.value ? `留下了 ${item.value} 道足迹` : '这一天还很安静';
    cell.setAttribute('aria-label', `${displayDate(item.date)}，${detail}`);
    gridFragment.appendChild(cell);
  });
  months.appendChild(monthFragment);
  grid.appendChild(gridFragment);
  q('#active-days').textContent = data.active_days || 0;
  q('#longest-streak').textContent = `${data.longest_streak || 0}天`;
  q('#current-streak').textContent = `${data.current_streak || 0}天`;
}

function showHeatTooltip(event, item) {
  const tooltip = q('#heat-tooltip');
  tooltip.innerHTML = `<strong>${displayDate(item.date)}</strong><br><span>${item.future ? '未来的书页' : item.value ? `留下了 ${item.value} 道足迹` : '这一天还很安静'}</span>`;
  tooltip.classList.remove('hidden');
  requestAnimationFrame(() => moveHeatTooltip(event));
}
function moveHeatTooltip(event) {
  const tooltip = q('#heat-tooltip');
  if (!tooltip || !event) return;
  // CSS pixels at the usual 96 DPI: approximately 1 cm from the pointer.
  // This makes the tooltip sit directly above the pointer instead of beside it.
  const gap = Math.round(96 / 2.54);
  const width = tooltip.offsetWidth || 220;
  const height = tooltip.offsetHeight || 72;
  const cell = event.target?.closest?.('.heat-cell');
  const rect = cell?.getBoundingClientRect?.();
  const x = Number.isFinite(Number(event.clientX))
    ? Number(event.clientX) : (rect?.left || 0);
  const y = Number.isFinite(Number(event.clientY))
    ? Number(event.clientY) : (rect?.top || 0);
  // Anchor to the actual pointer, not the cell.  The default position is
  // Center the tooltip horizontally on the pointer and place its bottom edge
  // above the pointer by the tooltip height plus the requested gap.
  let left = x - width / 2;
  let top = y - height - gap;
  if (top < 8) top = y + gap;
  tooltip.style.left = `${Math.max(8, Math.min(left, window.innerWidth - width - 8))}px`;
  tooltip.style.top = `${Math.max(8, Math.min(top, window.innerHeight - height - 8))}px`;
}
function hideHeatTooltip() { q('#heat-tooltip').classList.add('hidden'); }

function ensureTimelineDateControls() {
  if (q('#timeline-date-controls')) return;
  const heading = q('.timeline-heading');
  if (!heading) return;
  const controls = document.createElement('div');
  controls.id = 'timeline-date-controls';
  controls.className = 'timeline-date-controls';
  controls.innerHTML = `
    <label class="timeline-date-label" for="timeline-date-preset">按时间查找</label>
    <select id="timeline-date-preset" class="c-input" aria-label="时间范围">
      <option value="all">全部时间</option>
      <option value="today">今天</option>
      <option value="7d">最近7天</option>
      <option value="30d">最近30天</option>
      <option value="3m">最近3个月</option>
      <option value="custom">自定义日期</option>
    </select>
    <input id="timeline-start-date" class="c-input" type="date" aria-label="开始日期">
    <span class="timeline-date-separator">至</span>
    <input id="timeline-end-date" class="c-input" type="date" aria-label="结束日期">
    <button id="timeline-date-apply" class="c-button primary" type="button">查找日记</button>
    <button id="timeline-date-clear" class="c-button ghost" type="button">清除</button>`;
  heading.insertAdjacentElement('afterend', controls);

  const preset = q('#timeline-date-preset');
  const start = q('#timeline-start-date');
  const end = q('#timeline-end-date');
  const today = () => localDateKey(new Date());
  const shiftDays = days => {
    const value = new Date(`${today()}T12:00:00`);
    value.setDate(value.getDate() - days);
    return localDateKey(value);
  };
  preset.addEventListener('change', () => {
    const value = preset.value;
    if (value === 'all') { start.value = ''; end.value = ''; }
    else if (value === 'today') { start.value = today(); end.value = today(); }
    else if (value === '7d') { start.value = shiftDays(6); end.value = today(); }
    else if (value === '30d') { start.value = shiftDays(29); end.value = today(); }
    else if (value === '3m') {
      const date = new Date(`${today()}T12:00:00`);
      date.setMonth(date.getMonth() - 3);
      start.value = localDateKey(date); end.value = today();
    }
    start.disabled = value !== 'custom';
    end.disabled = value !== 'custom';
  });
  on('#timeline-date-apply', 'click', applyTimelineFilters);
  on('#timeline-date-clear', 'click', () => {
    timelineQuery = ''; timelineStartDate = ''; timelineEndDate = '';
    q('#global-search').value = '';
    q('#search-results')?.classList.add('hidden');
    preset.value = 'all'; start.value = ''; end.value = '';
    start.disabled = true; end.disabled = true;
    timelinePage = 1; dirtyPages.add('corridor'); loadPage('corridor', true);
  });
  preset.dispatchEvent(new Event('change'));
}

function syncTimelineDateControls() {
  const start = q('#timeline-start-date');
  const end = q('#timeline-end-date');
  const preset = q('#timeline-date-preset');
  if (!start || !end || !preset) return;
  start.value = timelineStartDate || '';
  end.value = timelineEndDate || '';
  preset.value = timelineStartDate || timelineEndDate ? 'custom' : 'all';
  start.disabled = preset.value !== 'custom';
  end.disabled = preset.value !== 'custom';
  if (q('#global-search')) q('#global-search').value = timelineQuery;
}

function applyTimelineFilters() {
  timelineQuery = q('#global-search')?.value.trim() || '';
  timelineStartDate = q('#timeline-start-date')?.value || '';
  timelineEndDate = q('#timeline-end-date')?.value || '';
  timelinePage = 1;
  dirtyPages.add('corridor');
  loadPage('corridor', true);
}

function renderTimeline(payload) {
  const items = payload.items || [];
  timelinePage = payload.page || 1;
  timelinePageSize = payload.page_size || timelinePageSize;
  timelineAuthor = payload.author || timelineAuthor;
  timelineQuery = payload.query ?? timelineQuery;
  timelineStartDate = payload.start_date || '';
  timelineEndDate = payload.end_date || '';
  syncTimelineDateControls();
  timelineDates = [...new Set(items.map(item => item.date))];
  qa('#timeline-author-filters button').forEach(button => button.classList.toggle('selected', button.dataset.author === timelineAuthor));
  const root = q('#timeline');
  root.innerHTML = '';
  if (!items.length) {
    root.innerHTML = '<div class="empty-state">时间长廊还没有故事。<br>第一张书页正等待被写下。</div>';
    renderTimelinePagination(payload);
    return;
  }
  const groups = new Map();
  items.forEach(item => {
    const key = item.date.slice(0, 7);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  groups.forEach((days, key) => {
    const section = document.createElement('section');
    section.className = 'month-group';
    const [year, month] = key.split('-');
    section.innerHTML = `<h3 class="month-title"><span>${year}年${Number(month)}月</span></h3>`;
    days.forEach(item => {
      const row = document.createElement('article');
      row.className = `diary-row ${item.author === 'lianxin' ? 'lianxin-diary' : 'user-diary'}`;
      row.dataset.date = item.date;
      row.dataset.author = item.author;
      const authorName = item.author === 'lianxin' ? '莲心' : (state?.user_name || '主人');
      row.innerHTML = `<button class="diary-card" type="button"><time>${Number(item.date.slice(8))}日 · ${weekday(item.date).replace('星期','周')}</time><div><strong>${escapeHtml(authorName)}的日记</strong><p>${escapeHtml(excerpt(item) || '这一天被安静地写下了。')}</p></div><span>${item.sealed ? '已封存' : '仍在书写'}</span></button><button class="diary-favorite ${item.favorite ? 'selected' : ''}" type="button" aria-label="${item.favorite ? '取消收藏' : '收藏这一天'}">${item.favorite ? '★' : '☆'}</button>`;
      section.appendChild(row);
    });
    root.appendChild(section);
  });
  renderTimelinePagination(payload);
}

function renderTimelinePagination(payload) {
  const root = q('#timeline-pagination');
  root.innerHTML = '';
  const totalPages = Math.max(1, payload.total_pages || 1);
  const current = Math.max(1, payload.page || 1);
  if (totalPages <= 1) return;
  const add = (label, page, disabled = false, selected = false) => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = label; button.disabled = disabled;
    button.className = selected ? 'selected' : '';
    button.onclick = () => goTimelinePage(page);
    root.appendChild(button);
  };
  add('首页', 1, current === 1); add('上一页', current - 1, current === 1);
  const pages = new Set([1, totalPages]);
  for (let page = Math.max(1, current - 2); page <= Math.min(totalPages, current + 2); page++) pages.add(page);
  let previous = 0;
  [...pages].sort((a, b) => a - b).forEach(page => {
    if (previous && page - previous > 1) { const gap = document.createElement('span'); gap.textContent = '…'; root.appendChild(gap); }
    add(String(page), page, false, page === current); previous = page;
  });
  add('下一页', current + 1, current === totalPages); add('最后一页', totalPages, current === totalPages);
}

async function goTimelinePage(page) {
  timelinePage = Math.max(1, page);
  dirtyPages.add('corridor');
  await loadPage('corridor', true);
  q('.timeline-section')?.scrollIntoView({ behavior: document.body.classList.contains('low-power') ? 'auto' : 'smooth', block: 'start' });
}

async function setTimelineAuthor(author) {
  timelineAuthor = ['user', 'lianxin'].includes(author) ? author : 'all';
  timelinePage = 1;
  dirtyPages.add('corridor');
  await loadPage('corridor', true);
}

async function toggleDiaryFavorite(day) {
  const result = await call('toggle_day_favorite', day);
  if (!result?.ok) { showToast(result?.error || '收藏状态没有保存成功。'); return null; }
  const favorite = Boolean(result.day?.favorite);
  qa(`.diary-row[data-date="${day}"], .memory-card[data-date="${day}"]`).forEach(row => {
    const star = row.querySelector('.diary-favorite, .memory-favorite');
    if (!star) return;
    star.classList.toggle('selected', favorite);
    star.textContent = favorite ? '★' : '☆';
  });
  if (currentDetailDate === day) updateDrawerFavorite(favorite);
  showToast(favorite ? '已经收进收藏馆。' : '已经从收藏馆取下。');
  return result.day;
}

function renderTree(payload) {
  const items = payload.items || [];
  treePage = payload.page || 1;
  treeFilter = payload.filter || treeFilter;
  treeQuery = payload.query ?? treeQuery;
  treeSort = payload.sort || treeSort;
  treeArchived = Boolean(payload.archived);
  q('#paper-box-count').textContent = payload.total_items || 0;
  updateTreeBadges(payload);
  q('#paper-box-search').value = treeQuery;
  q('#paper-box-sort').value = treeSort;
  q('#paper-box-archived').checked = treeArchived;
  qa('#paper-box-filters button').forEach(button => button.classList.toggle('selected', button.dataset.filter === treeFilter));
  const list = q('#paper-box-list');
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div class="empty-state">纸匣子里暂时没有符合条件的纸条。</div>';
    q('#tree-notes').innerHTML = '<div class="empty-state">今晚，<br><br>这里还很安静。</div>';
    renderCompactPagination(q('#paper-box-pagination'), payload, goTreePage);
    return;
  }
  const selected = items.find(item => item.id === currentTreeNoteId) || items[0];
  currentTreeNoteId = selected.id;
  items.forEach(item => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `paper-box-item${item.id === currentTreeNoteId ? ' selected' : ''}`;
    button.dataset.noteId = item.id;
    button.innerHTML = `<span>${item.favorite ? '★' : '☆'}</span><div><strong>${item.author === 'lianxin' ? '莲心的纸条' : '我的纸条'}</strong><p>${escapeHtml(String(item.content || '').replace(/\s+/g, ' ').slice(0, 58))}</p><small>${formatTime(item.created_at)}${item.reply ? ' · 莲心已回应' : ''}</small></div>`;
    list.appendChild(button);
  });
  renderTreeNote(selected);
  renderCompactPagination(q('#paper-box-pagination'), payload, goTreePage);
}

function renderTreeNote(item) {
  const root = q('#tree-notes');
  const flipped = root.querySelector(`.tree-note-shell[data-note-id="${item.id}"].flipped`);
  root.innerHTML = '';
  const shell = document.createElement('article');
  shell.className = 'tree-note-shell featured-tree-note';
  if (flipped) shell.classList.add('flipped');
  shell.dataset.noteId = item.id;
  const isUserNote = item.author !== 'lianxin';
  const reply = item.reply?.content || (isUserNote ? '' : '这张纸条本身，就是莲心悄悄留给你的回响。');
  shell.innerHTML = `<div class="tree-note-inner"><section class="tree-note tree-note-front"><p>${escapeHtml(item.content).replace(/\n/g,'<br>')}</p><small>${isUserNote ? '你放进树洞的纸条' : '莲心留下的纸条'} · ${formatTime(item.created_at)}</small><div class="tree-note-actions"><button class="tree-favorite">${item.favorite ? '★ 已收藏' : '☆ 收藏'}</button><button class="tree-archive">${item.archived ? '恢复纸条' : '收起归档'}</button></div><i>右键翻到背面</i></section><section class="tree-note tree-note-back"><p class="tree-reply">${reply ? escapeHtml(reply).replace(/\n/g,'<br>') : '莲心正在纸条背面写些什么……'}</p><small>莲心的回响</small><button class="tree-retry ${reply || !isUserNote ? 'hidden' : ''}">重新请求回应</button><i>再次右键返回</i></section></div>`;
  shell.oncontextmenu = async event => {
    event.preventDefault();
    shell.classList.toggle('flipped');
    if (shell.classList.contains('flipped')) {
      const unread = await call('mark_tree_thread_read', item.id);
      if (unread) { state = { ...(state || {}), ...unread }; updateTreeBadges(state); }
      if (isUserNote && !item.reply) await requestTreeReply(item.id);
    }
  };
  shell.querySelector('.tree-favorite').onclick = async event => { event.stopPropagation(); await call('toggle_tree_favorite', item.id); };
  shell.querySelector('.tree-archive').onclick = async event => { event.stopPropagation(); await call('toggle_tree_archive', item.id); };
  shell.querySelector('.tree-retry').onclick = async event => {
    event.stopPropagation(); shell.querySelector('.tree-reply').textContent = '莲心正在重新落笔……'; await requestTreeReply(item.id);
  };
  root.appendChild(shell);
}

async function requestTreeReply(noteId) {
  const result = await call('request_tree_reply', noteId);
  if (!result || result.ok === false) showToast(result?.error || '暂时无法请求树洞回应，请稍后重试。');
  return result;
}

function renderCompactPagination(root, payload, handler) {
  root.innerHTML = '';
  const total = Math.max(1, payload.total_pages || 1);
  const current = Math.max(1, payload.page || 1);
  if (total <= 1) return;
  [['‹', current - 1, current === 1], [`${current}/${total}`, current, true], ['›', current + 1, current === total]].forEach(([label, page, disabled]) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.disabled = disabled; button.onclick = () => handler(page); root.appendChild(button);
  });
}

async function goTreePage(page) {
  treePage = Math.max(1, page);
  dirtyPages.add('tree');
  await loadPage('tree', true);
}

function renderMuseum(payload) {
  const items = payload.items || [];
  museumPage = payload.page || 1;
  museumQuery = payload.query ?? museumQuery;
  museumSort = payload.sort || museumSort;
  q('#museum-search').value = museumQuery;
  q('#museum-sort').value = museumSort;
  const root = q('#memory-grid');
  root.innerHTML = '';
  if (!items.length) {
    root.innerHTML = '<div class="empty-state">收藏馆目前还是空的。<br><br>去时间长廊点亮一颗星星吧。</div>';
    renderMuseumPagination(payload);
    return;
  }
  items.forEach(item => {
    const card = document.createElement('article');
    card.className = 'memory-card';
    card.dataset.date = item.source_date;
    card.innerHTML = `<button class="memory-open" type="button"><div class="memory-cover"><svg><use href="#i-star"/></svg></div><footer><h3>${escapeHtml(item.title)}</h3><p>${displayDate(item.source_date)}<br>${escapeHtml(item.description || '').slice(0, 72)}</p><small>${item.user_content && item.lianxin_content ? '共同书页' : item.lianxin_content ? '莲心的书页' : (state?.user_name || '主人') + '的书页'}</small></footer></button><button class="memory-favorite selected" type="button" aria-label="取消收藏">★</button>`;
    root.appendChild(card);
  });
  renderMuseumPagination(payload);
}

function renderMuseumPagination(payload) {
  const root = q('#museum-pagination');
  root.innerHTML = '';
  const total = Math.max(1, payload.total_pages || 1);
  const current = Math.max(1, payload.page || 1);
  if (total <= 1) return;
  const add = (label, page, disabled = false) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.disabled = disabled; button.onclick = () => goMuseumPage(page); root.appendChild(button);
  };
  add('首页', 1, current === 1); add('上一页', current - 1, current === 1);
  const marker = document.createElement('span'); marker.textContent = `${current} / ${total}`; root.appendChild(marker);
  add('下一页', current + 1, current === total); add('最后一页', total, current === total);
}

async function goMuseumPage(page) {
  museumPage = Math.max(1, page);
  dirtyPages.add('museum');
  await loadPage('museum', true);
}

function daySections(item) {
  const sections = [];
  if (item.user_content) sections.push({ id: 'user', label: '主人留下的书页', author: 'user', content: item.user_content });
  if (item.lianxin_content) sections.push({ id: 'lianxin', label: '莲心留下的书页', author: 'lianxin', content: item.lianxin_content });
  (item.traces || []).forEach(trace => sections.push({ id: `trace-${trace.id}`, label: `${trace.author === 'lianxin' ? '莲心' : '主人'}后来的笔迹`, author: trace.author, content: trace.content, created_at: trace.created_at }));
  return sections;
}

async function openDayPicker(day) {
  const item = await call('get_day', day);
  if (!item || item.ok === false) { showToast(item?.error || '无法读取这一天的日记。'); return; }
  currentDayData = item;
  q('#day-picker-date').textContent = `${displayDate(day)} · ${weekday(day)}`;
  const root = q('#day-picker-options'); root.innerHTML = '';
  const sections = daySections(item);
  sections.forEach(section => {
    const label = document.createElement('label');
    label.innerHTML = `<input type="checkbox" value="${section.id}" ${section.revision ? '' : 'checked'}><span><strong>${escapeHtml(section.label)}</strong><small>${escapeHtml(String(section.content || '').replace(/\s+/g, ' ').slice(0, 80))}</small></span>`;
    root.appendChild(label);
  });
  if (item.collections?.length) {
    const label = document.createElement('label');
    label.innerHTML = `<input type="checkbox" value="collections" checked><span><strong>当天的共同收藏</strong><small>${item.collections.length} 件附件或收藏</small></span>`;
    root.appendChild(label);
  }
  q('#day-picker-panel').classList.remove('hidden');
}

async function openDay(day, preferredAuthor = '', selectedIds = null) {
  if (Array.isArray(preferredAuthor)) { selectedIds = preferredAuthor; preferredAuthor = ''; }
  const item = await call('get_day', day);
  if (!item || item.ok === false) { showToast(item?.error || '无法读取这一天的日记。'); return; }
  currentDayData = item;
  currentMemoryDate = day;
  currentDetailDate = day;
  currentDetailAuthor = preferredAuthor || (item.user_content && item.lianxin_content ? 'all' : item.lianxin_content ? 'lianxin' : 'user');
  call('visit_diary', day);
  q('#detail-date').textContent = `${displayDate(day)} · ${weekday(day)}`;
  const preferredContent = preferredAuthor === 'lianxin' ? item.lianxin_content : preferredAuthor === 'user' ? item.user_content : '';
  q('#detail-title').textContent = firstLine(preferredContent || item.user_content || item.lianxin_content) || '这一天的故事';
  const sourceLabel = currentDetailAuthor === 'lianxin' ? '莲心的日记' : currentDetailAuthor === 'user' ? `${state?.user_name || '主人'}的日记` : '共同书页';
  q('#detail-focus-author').textContent = `正在查看：${sourceLabel}`;
  q('#detail-invite-lianxin span').textContent = `邀请莲心看看${sourceLabel}`;
  const allowed = selectedIds ? new Set(selectedIds) : null;
  const root = q('#detail-sections'); root.innerHTML = '';
  const sections = daySections(item).filter(section => !allowed || allowed.has(section.id));
  sections.sort((a, b) => Number(b.author === preferredAuthor) - Number(a.author === preferredAuthor));
  sections.forEach(section => {
    const article = document.createElement('article');
    article.className = `detail-entry ${section.author === 'lianxin' ? 'lianxin-ink' : 'user-ink'}${section.revision ? ' revision-entry' : ''}${section.author === preferredAuthor ? ' preferred-entry' : ''}`;
    article.innerHTML = `<header><strong>${escapeHtml(section.label)}</strong><small>${section.created_at ? formatTime(section.created_at) : ''}</small></header><div class="detail-content">${escapeHtml(section.content).replace(/\n/g, '<br>')}</div>`;
    root.appendChild(article);
  });
  const showCollections = !allowed || allowed.has('collections');
  const collections = showCollections ? (item.collections || []) : [];
  renderCollections(q('#detail-collections'), collections.filter(entry => entry.kind === 'photo'));
  renderLegacyCollections(collections.filter(entry => entry.kind !== 'photo'));
  updateDrawerFavorite(Boolean(item.favorite));
  const index = timelineDates.indexOf(day);
  q('#detail-prev').disabled = index < 0 || index >= timelineDates.length - 1;
  q('#detail-next').disabled = index <= 0;
  q('#detail-panel').classList.remove('hidden');
  q('#day-picker-panel').classList.add('hidden');
  qa('.memory-card').forEach(card => card.classList.toggle('selected', card.dataset.date === day));
  q('#invite-lianxin').disabled = false;
  q('#invite-lianxin span').textContent = `邀请莲心看看 ${displayDate(day)}`;
}

function firstLine(value = '') { return String(value).split(/\n/).find(line => line.trim())?.replace(/^#+\s*/, '').slice(0, 32) || ''; }
function openMemory(item) { openDay(item.source_date || item.date); }
function closeDetail() { q('#detail-panel').classList.add('hidden'); }

function updateDrawerFavorite(favorite) {
  const button = q('#detail-favorite');
  button.classList.toggle('selected', favorite);
  button.querySelector('span').textContent = favorite ? '★ 已收藏这一天' : '☆ 收藏这一天';
}

function renderLegacyCollections(items) {
  const root = q('#detail-legacy-collections');
  root.innerHTML = '';
  if (!items.length) return;
  const title = document.createElement('small');
  title.textContent = '旧版共同收藏';
  root.appendChild(title);
  items.forEach(item => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${collectionLabels[item.kind] || '附件'} · ${item.title || '未命名'}`;
    button.onclick = () => openAttachment(item);
    root.appendChild(button);
  });
}

async function inviteSelectedDiary() {
  if (!currentDetailDate) { showToast('请先打开一篇想和莲心一起看的日记。'); return; }
  const sourceLabel = currentDetailAuthor === 'lianxin' ? '莲心的日记' : currentDetailAuthor === 'user' ? `${state?.user_name || '主人'}的日记` : '共同书页';
  q('#companion-source').textContent = `莲心正在读：${displayDate(currentDetailDate)} · ${sourceLabel}`;
  q('#companion-reading').textContent = '我正在认真重读这一页……';
  q('#companion-panel').classList.remove('hidden');
  const result = await call('invite_lianxin', currentDetailDate, currentDetailAuthor || 'all');
  if (!result?.ok) {
    q('#companion-reading').textContent = result?.error || '暂时无法邀请莲心，请稍后重试。';
    showToast(result?.error || '暂时无法邀请莲心。');
  }
}

function toFileUrl(value = '') {
  const path = String(value || '');
  if (!path) return '';
  if (/^file:/i.test(path)) return path;
  return `file:///${encodeURI(path.replace(/\\/g, '/'))}`;
}

function openAttachment(item) {
  const title = item?.title || collectionLabels[item?.kind] || '共同收藏';
  q('#attachment-kind').textContent = collectionLabels[item?.kind] || '共同收藏';
  q('#attachment-title').textContent = title;
  const preview = q('#attachment-preview');
  preview.innerHTML = '';
  const uri = item?.uri || '';
  if (item?.kind === 'photo' && uri) {
    const image = document.createElement('img');
    image.src = toFileUrl(uri); image.alt = title; image.className = 'attachment-image';
    image.setAttribute('aria-label', '点击放大查看');
    image.onclick = event => { event.preventDefault(); openImageLightbox(uri, title); };
    preview.appendChild(image);
  } else if (item?.kind === 'music' && uri) {
    const audio = document.createElement('audio');
    audio.src = toFileUrl(uri); audio.controls = true; audio.preload = 'metadata'; audio.className = 'attachment-audio';
    preview.appendChild(audio);
  } else {
    preview.innerHTML = `<div class="attachment-file"><svg><use href="#i-${iconForKind[item?.kind] || 'file'}"/></svg><p>${uri ? '附件已经保存到时间胶囊媒体库。' : '这份共同收藏没有关联本地文件。'}</p></div>`;
  }
  const open = q('#attachment-open');
  open.classList.toggle('hidden', !uri);
  open.onclick = async () => {
    const result = await call('open_collection', uri);
    if (!result?.ok) showToast(result?.error || '暂时无法打开这个附件。');
  };
  q('#attachment-panel').classList.remove('hidden');
}

function closeAttachment() { q('#attachment-panel').classList.add('hidden'); }

function openImageLightbox(uri, title = '附件大图') {
  q('#lightbox-title').textContent = title;
  q('#lightbox-image').src = toFileUrl(uri);
  lightboxScale = 1; lightboxRotation = 0; lightboxOffset = { x: 0, y: 0 };
  updateLightboxTransform();
  q('#image-lightbox').classList.remove('hidden');
}

function closeImageLightbox() { q('#image-lightbox').classList.add('hidden'); }
function updateLightboxTransform() {
  q('#lightbox-image').style.transform = `translate(${lightboxOffset.x}px, ${lightboxOffset.y}px) scale(${lightboxScale}) rotate(${lightboxRotation}deg)`;
}
function resetLightbox() { lightboxScale = 1; lightboxRotation = 0; lightboxOffset = { x: 0, y: 0 }; updateLightboxTransform(); }

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  return `${date.getMonth()+1}月${date.getDate()}日 ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}

function bindEvents() {
  document.addEventListener('click', event => {
    const item = event.target?.closest?.('.c-sidebar-item');
    if (!item) return;
    if (item.dataset.page) switchPage(item.dataset.page);
  });
  on('#minimize', 'click', () => bridge?.request_minimize());
  on('#fullscreen', 'click', () => bridge?.request_fullscreen());
  on('#close', 'click', () => bridge?.request_close());
  on('#save-settings', 'click', saveSettings);
  on('#choose-media-directory', 'click', async () => {
    const path = await call('choose_media_directory');
    if (path) q('#media-directory').value = path;
  });
  on('#open-media-directory', 'click', async () => {
    const result = await call('open_media_directory', q('#media-directory').value);
    if (!result?.ok) showToast(result?.error || '无法打开附件目录。');
  });
  on('#reset-media-directory', 'click', () => { q('#media-directory').value = defaultMediaDirectory; });
  on('.detail-close', 'click', closeDetail);
  on('#detail-collapse', 'click', closeDetail);
  on('#detail-favorite', 'click', async () => { if (currentDetailDate) await toggleDiaryFavorite(currentDetailDate); });
  on('#detail-invite-lianxin', 'click', inviteSelectedDiary);
  on('.day-picker-close', 'click', () => q('#day-picker-panel').classList.add('hidden'));
  on('.day-picker-backdrop', 'click', () => q('#day-picker-panel').classList.add('hidden'));
  on('#open-selected-day', 'click', () => {
    const selected = qa('#day-picker-options input:checked').map(input => input.value);
    if (!selected.length) { showToast('请至少选择一项内容。'); return; }
    openDay(currentDayData?.date || currentDay, selected);
  });
  on('#detail-prev', 'click', () => {
    const index = timelineDates.indexOf(currentDetailDate);
    if (index >= 0 && index < timelineDates.length - 1) openDay(timelineDates[index + 1], currentDetailAuthor);
  });
  on('#detail-next', 'click', () => {
    const index = timelineDates.indexOf(currentDetailDate);
    if (index > 0) openDay(timelineDates[index - 1], currentDetailAuthor);
  });
  const timeline = q('#timeline');
  timeline?.addEventListener('click', async event => {
    const row = event.target?.closest?.('.diary-row');
    if (!row) return;
    if (event.target.closest('.diary-favorite')) { event.stopPropagation(); await toggleDiaryFavorite(row.dataset.date); return; }
    openDay(row.dataset.date, row.dataset.author);
  });
  timeline?.addEventListener('contextmenu', event => {
    const row = event.target?.closest?.('.diary-row');
    if (!row) return;
    event.preventDefault(); openDay(row.dataset.date, row.dataset.author);
  });
  on('#timeline-author-filters', 'click', event => {
    const button = event.target?.closest?.('button[data-author]');
    if (button) setTimelineAuthor(button.dataset.author);
  });
  const memoryGrid = q('#memory-grid');
  memoryGrid?.addEventListener('click', async event => {
    const card = event.target?.closest?.('.memory-card');
    if (!card) return;
    if (event.target.closest('.memory-favorite')) { event.stopPropagation(); await toggleDiaryFavorite(card.dataset.date); return; }
    openDay(card.dataset.date);
  });
  memoryGrid?.addEventListener('contextmenu', event => {
    const card = event.target?.closest?.('.memory-card');
    if (!card) return;
    event.preventDefault(); openDay(card.dataset.date);
  });
  on('#museum-search', 'input', debounce(event => { museumQuery = event.target.value.trim(); museumPage = 1; loadPage('museum', true); }, 260));
  on('#museum-sort', 'change', event => { museumSort = event.target.value; museumPage = 1; loadPage('museum', true); });
  on('#paper-box-list', 'click', event => {
    const button = event.target?.closest?.('.paper-box-item');
    if (!button) return;
    currentTreeNoteId = Number(button.dataset.noteId);
    call('mark_tree_thread_read', currentTreeNoteId).then(result => {
      if (result) { state = { ...(state || {}), ...result }; updateTreeBadges(state); }
    });
    renderTree(state.tree_page || { items: [] });
  });
  on('#paper-box-filters', 'click', event => {
    const button = event.target?.closest?.('button[data-filter]');
    if (!button) return;
    treeFilter = button.dataset.filter; treePage = 1;
    loadPage('tree', true);
  });
  on('#paper-box-search', 'input', debounce(event => { treeQuery = event.target.value.trim(); treePage = 1; loadPage('tree', true); }, 260));
  on('#paper-box-sort', 'change', event => { treeSort = event.target.value; treePage = 1; loadPage('tree', true); });
  on('#paper-box-archived', 'change', event => { treeArchived = event.target.checked; treePage = 1; loadPage('tree', true); });
  on('.attachment-close', 'click', closeAttachment);
  on('.attachment-backdrop', 'click', closeAttachment);
  on('#lightbox-close', 'click', closeImageLightbox);
  on('.image-lightbox-backdrop', 'click', closeImageLightbox);
  on('#lightbox-reset', 'click', resetLightbox);
  on('#lightbox-rotate', 'click', () => { lightboxRotation = (lightboxRotation + 90) % 360; updateLightboxTransform(); });
  const heatGrid = q('#contribution-grid');
  if (heatGrid) {
    const heatItem = target => {
      const cell = target?.closest?.('.heat-cell:not(.future)');
      if (!cell || !heatGrid.contains(cell)) return null;
      return { cell, item: { date: cell.dataset.date, value: Number(cell.dataset.value || 0), future: false } };
    };
    heatGrid.addEventListener('mouseover', event => {
      const found = heatItem(event.target);
      if (found) showHeatTooltip(event, found.item);
    });
    heatGrid.addEventListener('focusin', event => {
      const found = heatItem(event.target);
      if (found) showHeatTooltip(event, found.item);
    });
    heatGrid.addEventListener('mouseout', hideHeatTooltip);
    heatGrid.addEventListener('focusout', hideHeatTooltip);
    heatGrid.addEventListener('click', event => {
      const found = heatItem(event.target);
      if (!found) return;
      if (event.detail > 1) return;
      event.preventDefault();
      openDayPicker(found.item.date);
    });
  }
  on('#lightbox-stage', 'dblclick', resetLightbox);
  on('#lightbox-stage', 'wheel', event => {
    event.preventDefault(); lightboxScale = Math.max(.25, Math.min(6, lightboxScale * (event.deltaY < 0 ? 1.12 : .89))); updateLightboxTransform();
  }, { passive: false });
  on('#lightbox-stage', 'mousedown', event => { lightboxDragging = true; lightboxPointer = { x: event.clientX, y: event.clientY }; });
  window.addEventListener('mousemove', event => {
    if (!lightboxDragging) return;
    lightboxOffset.x += event.clientX - lightboxPointer.x; lightboxOffset.y += event.clientY - lightboxPointer.y;
    lightboxPointer = { x: event.clientX, y: event.clientY }; updateLightboxTransform();
  });
  window.addEventListener('mouseup', () => { lightboxDragging = false; });
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (!q('#image-lightbox').classList.contains('hidden')) closeImageLightbox();
    else if (!q('#day-picker-panel').classList.contains('hidden')) q('#day-picker-panel').classList.add('hidden');
    else if (!q('#detail-panel').classList.contains('hidden')) closeDetail();
    else if (activePage === 'settings') switchPage('today');
  });
  on('.companion-close', 'click', () => q('#companion-panel').classList.add('hidden'));
  const editor = q('#user-paper');
  let composing = false;
  const savePage = (immediate = false) => {
    const snapshot = editor.textContent;
    if (snapshot === lastPersistedUserContent) {
      q('#save-status').textContent = '已保存';
      return;
    }
    q('#save-status').textContent = '正在留住笔迹…';
    clearTimeout(saveTimer);
    const persist = async () => {
      const result = await call('save_user_content', currentDay, snapshot);
      if (result?.ok === false) {
        q('#save-status').textContent = '保存失败，请稍后重试';
        return;
      }
      lastPersistedUserContent = snapshot;
      if (editor.textContent === snapshot) q('#save-status').textContent = '已保存';
    };
    if (immediate) persist();
    else saveTimer = setTimeout(persist, 1800);
  };
  editor?.addEventListener('compositionstart', () => { composing = true; });
  editor?.addEventListener('compositionend', () => { composing = false; savePage(); });
  editor?.addEventListener('input', () => { if (!composing) savePage(); });
  editor?.addEventListener('blur', () => { if (!composing) savePage(true); });
  editor?.addEventListener('dragover', event => event.preventDefault());
  editor?.addEventListener('drop', async event => {
    event.preventDefault();
    try { await importDroppedPhotos(event.dataTransfer.files); }
    catch (error) { showToast(error?.message || '照片暂时没有保存成功。'); }
  });
  const albumSection = q('.memory-album-section');
  albumSection?.addEventListener('dragover', event => event.preventDefault());
  albumSection?.addEventListener('drop', async event => {
    event.preventDefault();
    try { await importDroppedPhotos(event.dataTransfer.files); }
    catch (error) { showToast(error?.message || '照片暂时没有保存成功。'); }
  });
  on('#add-photos', 'click', async () => {
    const result = await call('import_photos', currentDay);
    if (!result?.ok) { if (!result?.cancelled) showToast(result?.error || '照片暂时没有保存成功。'); return; }
    showToast(`已经把 ${result.count || 1} 张照片放进记忆相簿。`);
  });
  on('#add-trace', 'click', async () => {
    const input = q('#trace-input'); const value = input.value.trim(); if (!value) return;
    await call('add_trace', currentDay, 'user', value); input.value = ''; showToast('这道笔迹已经留在书页旁边。');
  });
  on('#seal-day', 'click', async () => {
    const book = q('#book-layout'); book.classList.add('closing');
    await call('seal_day', currentDay, q('#user-paper').textContent);
    setTimeout(() => { book.classList.remove('closing'); switchPage('corridor'); showToast('今天已经被好好封存。'); }, 800);
  });
  on('#request-diary', 'click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.querySelector('span').textContent = '正在整理今天的日记';
    const result = await call('request_diary_generation', currentDay);
    if (!result || result.ok === false) {
      button.disabled = false;
      button.querySelector('span').textContent = '请莲心写日记';
      showToast(result?.error || '暂时无法生成日记');
      return;
    }
    showToast('莲心已经开始整理今天的记录');
  });
  on('#send-tree', 'click', async () => {
    const input = q('#tree-input'); const value = input.value.trim(); if (!value) return;
    const result = await call('add_tree_note', value);
    if (result?.ok) {
      input.value = '';
      currentTreeNoteId = result.id;
      treePage = 1;
      treeFilter = 'all';
      treeQuery = '';
      treeSort = 'newest';
      treeArchived = false;
      await loadPage('tree', true);
      showToast('纸条已经飞进树洞。');
    } else showToast(result?.error || '纸条没有保存成功，请稍后重试。');
  });
  on('#invite-lianxin', 'click', inviteSelectedDiary);
  on('#global-search', 'input', debounce(async event => {
    const value = event.target.value.trim(); const box = q('#search-results');
    if (!value) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    const results = await call('search', value); box.innerHTML = '';
    (results || []).forEach(item => { const button = document.createElement('button'); button.className = 'search-result'; button.innerHTML = `<strong>${displayDate(item.date)}</strong><small>${escapeHtml(excerpt(item))}</small>`; button.onclick = () => { box.classList.add('hidden'); openDay(item.date); }; box.appendChild(button); });
    if (!(results || []).length) box.innerHTML = '<div class="empty-state">没有找到这段回忆。</div>';
    box.classList.remove('hidden');
  }, 240));
}

async function openSettings() {
  const fields = q('#settings-fields');
  const status = q('#settings-status');
  if (!fields || !status) {
    reportFrontendError(new Error('settings page missing'), '打开设置');
    return;
  }
  console.info('[TimeCapsule] settings page shown');
  if (!q('#reference-chat')) {
    q('#save-settings')?.insertAdjacentHTML('beforebegin', `
      <label class="setting-toggle"><span><strong>参考主界面对话</strong><small>整理今天在主界面发生的真实会话。</small></span><input id="reference-chat" type="checkbox"></label>
      <label class="setting-toggle"><span><strong>参考树洞纸条</strong><small>仅引用当天真实存在的纸条和回应。</small></span><input id="reference-tree-hole" type="checkbox"></label>
      <label class="setting-toggle"><span><strong>参考自习室记录</strong><small>把当天完成的专注和任务作为经历来源。</small></span><input id="reference-study-room" type="checkbox"></label>
      <label class="setting-toggle"><span><strong>参考时间胶囊互动</strong><small>包括书页、笔迹、收藏和共同阅读。</small></span><input id="reference-time-capsule" type="checkbox"></label>
      <label class="setting-toggle"><span><strong>参考附件描述</strong><small>只使用已有附件信息，不猜测图片内容。</small></span><input id="reference-attachments" type="checkbox"></label>
      <label class="setting-toggle"><span><strong>重要事件详细记录</strong><small>普通事情略写，重要经历保留更多细节。</small></span><input id="important-detail" type="checkbox"></label>
      <label><span><strong>莲心日记最大字数</strong><small>控制自动生成书页的大致篇幅。</small></span><input id="diary-max-chars" class="c-input" type="number" min="400" max="5000" step="100"></label>`);
  }
  fields?.classList.add('settings-loading');
  if (status) status.textContent = '正在读取设置……';
  try {
    const settings = await call('get_settings') || { ok: false, error: '桥接尚未准备好' };
    if (settings.ok === false) {
      if (status) status.textContent = settings.error || '设置读取失败，请稍后重试。';
      return;
    }
    q('#scheduled-enabled').checked = Boolean(settings.scheduled_enabled);
    q('#scheduled-time').value = settings.scheduled_time || '23:55';
    q('#max-messages').value = settings.max_messages || 30;
    q('#message-direction').value = settings.direction || 'latest';
    q('#media-directory').value = settings.media_directory || '';
    q('#timeline-page-size').value = String(settings.timeline_page_size || 15);
    q('#animations-enabled').checked = settings.animations_enabled !== false;
    q('#low-power-mode').checked = Boolean(settings.low_power_mode);
    q('#reference-chat').checked = settings.reference_chat !== false;
    q('#reference-tree-hole').checked = settings.reference_tree_hole !== false;
    q('#reference-study-room').checked = settings.reference_study_room !== false;
    q('#reference-time-capsule').checked = settings.reference_time_capsule !== false;
    q('#reference-attachments').checked = Boolean(settings.reference_attachments);
    q('#important-detail').checked = settings.important_detail !== false;
    q('#diary-max-chars').value = Number(settings.max_chars || 1600);
    defaultMediaDirectory = settings.default_media_directory || settings.media_directory || '';
    if (status) status.textContent = '设置已经载入';
  } catch (error) {
    reportFrontendError(error, '读取设置');
    if (status) status.textContent = '设置读取失败，请稍后重试。';
  } finally {
    fields?.classList.remove('settings-loading');
  }
}

async function saveSettings() {
  q('#settings-status').textContent = '正在保存……';
  const payload = {
    scheduled_enabled: q('#scheduled-enabled').checked,
    scheduled_time: q('#scheduled-time').value || '23:55',
    max_messages: Number(q('#max-messages').value || 30),
    direction: q('#message-direction').value,
    media_directory: q('#media-directory').value,
    timeline_page_size: Number(q('#timeline-page-size').value || 15),
    animations_enabled: q('#animations-enabled').checked,
    low_power_mode: q('#low-power-mode').checked,
    reference_chat: q('#reference-chat').checked,
    reference_tree_hole: q('#reference-tree-hole').checked,
    reference_study_room: q('#reference-study-room').checked,
    reference_time_capsule: q('#reference-time-capsule').checked,
    reference_attachments: q('#reference-attachments').checked,
    important_detail: q('#important-detail').checked,
    max_chars: Number(q('#diary-max-chars').value || 1600),
  };
  const result = await call('save_settings_payload', JSON.stringify(payload));
  if (!result?.ok) {
    showToast(result?.error || '设置没有传到后端，请重新打开时间胶囊后再试。');
    q('#settings-status').textContent = result?.error || '设置保存失败：后端桥接没有返回结果。';
    return;
  }
  timelinePageSize = Number(result.timeline_page_size || 15);
  document.body.classList.toggle('animations-off', result.animations_enabled === false);
  document.body.classList.toggle('low-power', Boolean(result.low_power_mode));
  dirtyPages.add('corridor'); loadedPages.delete('corridor');
  showToast('书页设置已经保存。');
}

function renderCollectionActions() {
  // 新版只保留照片入口，按钮由静态界面提供，避免重复创建事件节点。
}

function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('无法读取拖入的附件'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(file);
  });
}

async function importDroppedPhotos(fileList) {
  const files = [...(fileList || [])].filter(file => file.type?.startsWith('image/'));
  if (!files.length) { showToast('记忆相簿只接收图片。'); return; }
  let saved = 0;
  for (const file of files) {
    const result = file.path
      ? await call('import_collection_path', currentDay, 'photo', file.path)
      : await call('import_collection_data', currentDay, 'photo', file.name, await fileAsDataUrl(file));
    if (result?.ok) saved += 1;
  }
  showToast(saved ? `已经把 ${saved} 张照片放进记忆相簿。` : '照片暂时没有保存成功。');
}

function debounce(fn, delay) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; }

function initialize() {
  try {
    const version = document.querySelector('meta[name="time-capsule-version"]')?.content || 'unknown';
    console.info(`[TimeCapsule] DOM ready version=${version} href=${location.href}`);
    const mediaLabel = q('.media-directory-setting > span');
    if (mediaLabel) mediaLabel.innerHTML = '照片保存位置<small>新添加到记忆相簿的照片将复制到这里</small>';
    bindEvents();
    console.info('[TimeCapsule] event binding complete');
    renderCollectionActions();
  } catch (error) {
    reportFrontendError(error, '初始化事件');
    return;
  }
  if (typeof QWebChannel === 'undefined' || !window.qt?.webChannelTransport) {
    q('#footer-status').textContent = '桥接加载失败，请重新打开时间胶囊';
    q('#loading-state').innerHTML = '<strong>书页没有顺利展开</strong><span>请关闭后重新打开时间胶囊。</span>';
    hideLoadingState();
    return;
  }
  new QWebChannel(qt.webChannelTransport, channel => {
    try {
      bridge = channel.objects.capsuleBridge;
      if (!bridge) throw new Error('capsuleBridge unavailable');
      console.info('[TimeCapsule] QWebChannel connected');
      bridge.state_changed.connect(payload => render(parse(payload)));
      bridge.generation_completed.connect((day, success, error) => {
        if (day !== currentDay) return;
        const button = q('#request-diary');
        if (button) {
          button.disabled = false;
          button.querySelector('span').textContent = success ? '重新整理今天的日记' : '请莲心写日记';
        }
        if (!success && error) showToast(error);
        if (success) showToast('莲心的日记已经写好了。');
      });
      bridge.page_state_changed.connect((page, payload) => applyPageState(page, parse(payload)));
      bridge.page_invalidated.connect(page => {
        dirtyPages.add(page);
        if (activePage === page) loadPage(page, true);
      });
      bridge.tree_reply_ready.connect((noteId, payload) => {
      const result = parse(payload);
      const shell = q(`.tree-note-shell[data-note-id="${noteId}"]`);
      if (!shell) return;
      const reply = shell.querySelector('.tree-reply');
      const retry = shell.querySelector('.tree-retry');
      if (result?.ok && result.reply?.content) {
        reply.innerHTML = escapeHtml(result.reply.content).replace(/\n/g, '<br>');
        retry.classList.add('hidden');
      } else {
        reply.textContent = result?.error || '莲心暂时没有写完回应。';
        retry.classList.remove('hidden');
      }
      });
      bridge.companion_ready.connect(payload => {
      const result = parse(payload);
      q('#companion-source').textContent = result?.date
        ? `莲心正在读：${displayDate(result.date)} · ${result.author_label || '共同书页'}${result.title ? `《${result.title}》` : ''}`
        : '莲心的陪伴';
      q('#companion-reading').textContent = result?.message || '我在这里。';
      q('#companion-panel').classList.remove('hidden');
      });
      call('get_initial_state')
        .then(data => {
          if (!data || data.ok === false) throw new Error(data?.error || '初始状态为空');
          render(data);
        })
        .catch(error => reportFrontendError(error, '读取初始状态'))
        .finally(hideLoadingState);
    } catch (error) {
      reportFrontendError(error, '连接界面桥接');
      hideLoadingState();
    }
  });
}

document.addEventListener('DOMContentLoaded', initialize);
