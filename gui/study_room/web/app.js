const STUDY_ROOM_UI_VERSION = '2026.07.26.16';
const RING_LENGTH = 860.8;
const q = (selector) => document.querySelector(selector);
const qa = (selector) => [...document.querySelectorAll(selector)];

let bridge = null;
let allTasks = [];
let taskFilter = 'all';
let selectedRange = 'today';
let statsMode = 'overview';
let reportAnchor = '';
let currentReport = null;
let currentView = 'home';
let editingTaskId = null;
let completionTaskId = null;
let spaceState = null;
let pendingSpaceWallpaper = null;
let lastRewindSignature = '';

function setTextIfChanged(selector, value) {
  const element = q(selector);
  if (element && element.textContent !== value) element.textContent = value;
}

function setStyleIfChanged(element, property, value) {
  if (element && element.style[property] !== value) element.style[property] = value;
}

function applySpaceVisuals(settings = {}) {
  const selected = (spaceState?.wallpapers || []).find(item => item.id === settings.wallpaper);
  const wallpaperOpacity = Math.max(0, Math.min(1, Number(settings.wallpaper_opacity ?? .42)));
  const maskOpacity = Math.max(.35, Math.min(1, Number(settings.content_mask_opacity ?? .82)));
  // 过去由 body 伪元素和 room-shell 各绘制一次壁纸，代价较高。
  // 合并后用等价的不透明度保留“壁纸透明度 × 内容遮罩”两项控制。
  const effectiveMask = 1 - wallpaperOpacity * (1 - maskOpacity);
  document.body.classList.toggle('has-space-wallpaper', Boolean(selected?.url));
  document.documentElement.style.setProperty('--space-wallpaper', selected?.url ? `url("${selected.url}")` : 'none');
  document.documentElement.style.setProperty('--space-wallpaper-opacity', String(wallpaperOpacity));
  document.documentElement.style.setProperty('--space-mask-opacity', String(maskOpacity));
  document.documentElement.style.setProperty('--space-effective-mask', String(effectiveMask));
  document.documentElement.style.setProperty('--space-wallpaper-size', settings.fit === 'contain' ? 'contain' : 'cover');
}

function eventText(item) {
  const map = { task_created: '创建了任务', task_updated: '调整了任务设置', task_completed: '完成了任务', task_reopened: '重新打开了任务', task_deleted: '移除了任务', focus_completed: '完成了一段专注', focus_interrupted: '中断了一段专注' };
  const duration = Number(item.details?.duration_seconds || 0);
  return `${map[item.event_type] || '更新了自习室记录'}${item.task_name ? `：${item.task_name}` : ''}${duration ? ` · ${formatDuration(duration)}` : ''}`;
}

function renderSpace(data) {
  if (!data) return;
  if (pendingSpaceWallpaper && data.settings && data.settings.wallpaper !== pendingSpaceWallpaper) {
    data = { ...data, settings: { ...data.settings, wallpaper: pendingSpaceWallpaper } };
  }
  spaceState = data;
  const settings = data.settings || {};
  applySpaceVisuals(settings);
  q('#space-wallpaper-opacity').value = Math.round(Number(settings.wallpaper_opacity ?? .42) * 100);
  q('#space-mask-opacity').value = Math.round(Number(settings.content_mask_opacity ?? .82) * 100);
  q('#space-fit').value = settings.fit || 'cover';
  q('#space-wallpaper-opacity-value').textContent = `${q('#space-wallpaper-opacity').value}%`;
  q('#space-mask-opacity-value').textContent = `${q('#space-mask-opacity').value}%`;
  const strip = q('#wallpaper-strip'); strip.innerHTML = '';
  (data.wallpapers || []).forEach(item => {
    const button = document.createElement('button');
    button.className = `wallpaper-option${item.id === settings.wallpaper ? ' active' : ''}`;
    button.dataset.wallpaper = item.id;
    button.innerHTML = `<img loading="lazy" decoding="async" alt=""><strong></strong>`;
    if (item.url) button.firstChild.src = item.url;
    button.querySelector('strong').textContent = item.name;
    button.onclick = () => {
      pendingSpaceWallpaper = item.id;
      spaceState.settings.wallpaper = item.id;
      applySpaceVisuals(spaceState.settings);
      qa('.wallpaper-option').forEach(option => option.classList.toggle('active', option === button));
    };
    strip.appendChild(button);
  });
  const notes = q('#space-notes'); notes.innerHTML = '';
  const noteItems = data.notes || [];
  if (!noteItems.length) notes.innerHTML = '<div class="space-empty">完成一次专注后，莲心会把第一张纸条留在这里。</div>';
  noteItems.forEach(note => { const card = document.createElement('article'); card.className = `note-paper${note.favorited ? ' favorited' : ''}`; card.innerHTML = `<small>${formatSessionTime(note.created_at)} · ${formatDuration(note.duration_seconds)}</small><p></p><div><button data-action="like">${note.liked ? '♥ 已点赞' : '♡ 点赞'}</button><button data-action="favorite">${note.favorited ? '★ 已收藏' : '☆ 收藏'}</button><button data-action="hide">收起</button></div>`; card.querySelector('p').textContent = note.content; card.querySelectorAll('button').forEach(btn => btn.onclick = () => { bridge?.update_note(note.id, btn.dataset.action); setTimeout(refreshSpace, 80); }); if (note.lianxin_liked) card.querySelector('div').insertAdjacentHTML('beforeend', '<em>莲心也收下了这份喜欢</em>'); notes.appendChild(card); });
  const events = q('#space-events'); events.innerHTML = '';
  const eventItems = data.week_events || [];
  if (!eventItems.length) events.innerHTML = '<div class="space-empty">这周还没有新的自习室足迹。</div>';
  eventItems.forEach(item => { const row = document.createElement('div'); row.className = 'space-event'; row.innerHTML = `<i></i><div><strong></strong><small>${formatSessionTime(item.occurred_at)}</small></div>`; row.querySelector('strong').textContent = eventText(item); events.appendChild(row); });
}

function refreshSpace() {
  bridge?.get_space(payload => {
    const data = JSON.parse(payload);
    if (pendingSpaceWallpaper && data.settings?.wallpaper === pendingSpaceWallpaper) pendingSpaceWallpaper = null;
    renderSpace(data);
  });
}
let toastTimer = null;
let roomSettings = {
  focus_minutes: 25,
  break_minutes: 5,
  auto_break: true,
  auto_fullscreen: true,
  show_completion: true,
  animations: true,
};
let timerState = {
  remaining: 1500,
  total: 1500,
  phase: 'idle',
  task_name: '',
  active: false,
  paused: false,
  preview: false,
};

function formatDuration(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (hours) return minutes ? `${hours}小时${minutes}分钟` : `${hours}小时`;
  return `${minutes}分钟`;
}

function showToast(message, duration = 2600) {
  const toast = q('#toast');
  toast.textContent = message;
  toast.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add('hidden'), duration);
}

function setCompanion(text) {
  q('#companion-text').textContent = text;
  if (currentView === 'focus' && text) q('#focus-companion').textContent = text;
}

function renderClock(clock) {
  if (!clock) return;
  q('#sidebar-time').textContent = clock.time || '--:--';
  q('#sidebar-date').textContent = clock.date || '日期不可用';
  q('#sidebar-weekday').textContent = clock.weekday || '';
  q('#sidebar-lunar').textContent = clock.lunar || '农历日期不可用';
}

function phaseLabel() {
  if (timerState.paused) return '已暂停';
  if (timerState.phase === 'break') return '休息时间';
  if (timerState.phase === 'focus') return '专注进行中';
  return '准备开始';
}

function renderTimer() {
  // 倒计时只更新当前可见的那套圆环。旧实现每秒同时修改首页和全屏页，
  // 即使其中一页 display:none，也会触发两套 SVG 样式失效与重绘。
  const focusVisible = currentView === 'focus';
  if (!focusVisible && currentView !== 'home') return;
  const remaining = Math.max(0, Number(timerState.remaining) || 0);
  const total = Math.max(1, Number(timerState.total) || 1);
  const display = `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;
  const progress = Math.max(0, Math.min(1, remaining / total));
  const dashOffset = RING_LENGTH * (1 - progress);
  const isBreak = timerState.phase === 'break';
  const status = timerState.paused ? '计时已暂停，准备好再继续' : (isBreak ? '让眼睛和思绪都休息一下' : (timerState.active ? '保持自己的节奏' : '选择一个任务或常用时长'));
  const timerSelector = focusVisible ? '#focus-timer-value' : '#timer-value';
  const phaseSelector = focusVisible ? '#focus-phase-value' : '#phase-value';
  const statusSelector = focusVisible ? '#focus-status' : '#timer-status';
  const ring = q(focusVisible ? '#focus-ring-progress' : '#ring-progress');
  const wrap = ring?.closest('.timer-wrap');
  setTextIfChanged(timerSelector, display);
  setTextIfChanged(phaseSelector, phaseLabel());
  setTextIfChanged(statusSelector, status);
  setStyleIfChanged(ring, 'strokeDashoffset', String(dashOffset));
  wrap?.classList.toggle('is-break', isBreak);
  wrap?.querySelectorAll('.ring-gradient-start').forEach(stop => {
    const color = isBreak ? '#a9d0bb' : '#f3cf72';
    if (stop.getAttribute('stop-color') !== color) stop.setAttribute('stop-color', color);
  });
  wrap?.querySelectorAll('.ring-gradient-end').forEach(stop => {
    const color = isBreak ? '#5b927d' : '#a9633c';
    if (stop.getAttribute('stop-color') !== color) stop.setAttribute('stop-color', color);
  });

  if (focusVisible) {
    const selectedText = q('#task-select').selectedOptions[0]?.textContent;
    setTextIfChanged('#focus-task-name', timerState.task_name || (selectedText && q('#task-select').value !== '-1' ? selectedText : '未绑定任务'));
    setTextIfChanged('#focus-badge', timerState.paused ? '莲心在等你回来' : (isBreak ? '莲心陪你休息' : '莲心陪伴中'));
    setTextIfChanged('#focus-pause', timerState.paused ? '继续' : '暂停');
  } else {
    setTextIfChanged('#pause-focus', timerState.paused ? '继续' : '暂停');
  }
}

function updateGoal(tasks = allTasks) {
  const selectedId = Number(q('#task-select').value);
  const selected = tasks.find(item => item.id === selectedId) || tasks.find(item => !item.completed);
  q('#goal-title').textContent = selected ? selected.title : '还没有选择任务';
  q('#goal-detail').textContent = selected
    ? `专注 ${selected.estimate_minutes} 分钟 · 休息 ${selected.break_minutes ?? 5} 分钟${selected.repeat_enabled ? ' · 循环执行' : ''}`
    : '前往任务清单写下一件想完成的事，或直接使用常用时长。';
}

function renderTasks(tasks) {
  allTasks = tasks || [];
  const list = q('#task-list');
  const query = (q('#task-search').value || '').trim().toLowerCase();
  const visible = allTasks
    .filter(task => task.title.toLowerCase().includes(query))
    .filter(task => taskFilter === 'all' || (taskFilter === 'completed' ? task.completed : !task.completed));
  list.innerHTML = '';

  if (!visible.length) {
    const title = query ? '没有找到匹配的任务' : (taskFilter === 'completed' ? '还没有已完成任务' : '任务清单还是空的');
    const detail = query ? '换一个关键词试试。' : '从上方写下一件具体、可以开始的事情。';
    list.innerHTML = `<div class="empty-state"><strong>${title}</strong><span>${detail}</span></div>`;
    return;
  }

  visible.forEach(task => {
    const row = document.createElement('div');
    row.className = `task-row${task.completed ? ' completed' : ''}`;
    row.dataset.taskId = String(task.id);
    row.innerHTML = `
      <button class="task-check" aria-label="${task.completed ? '恢复任务' : '完成任务'}">${task.completed ? '✓' : ''}</button>
      <span class="task-title"></span>
      <span class="task-estimate">专注 ${task.estimate_minutes}分 · 休息 ${task.break_minutes ?? 5}分${task.repeat_enabled ? ' · 循环' : ''}</span>
      <button class="task-start" ${task.completed ? 'disabled' : ''}>开始</button>
      <button class="task-delete" aria-label="删除任务">删除</button>`;
    row.querySelector('.task-title').textContent = task.title;
    row.querySelector('.task-check').onclick = () => bridge?.toggle_task(task.id);
    row.querySelector('.task-start').onclick = () => {
      q('#task-select').value = String(task.id);
      previewSelectedTask();
      startSelectedFocus();
    };
    row.querySelector('.task-delete').onclick = () => bridge?.delete_task(task.id);
    row.addEventListener('dblclick', () => openTaskEditor(task));
    row.addEventListener('contextmenu', event => {
      event.preventDefault();
      openTaskEditor(task);
    });
    list.appendChild(row);
  });
}

function openTaskEditor(task) {
  editingTaskId = task.id;
  q('#edit-task-title').value = task.title;
  q('#edit-task-estimate').value = task.estimate_minutes;
  q('#edit-task-break').value = task.break_minutes ?? 5;
  q('#edit-task-repeat').checked = Boolean(task.repeat_enabled);
  q('#task-modal').classList.remove('hidden');
  q('#edit-task-title').focus();
}

function closeTaskEditor() {
  editingTaskId = null;
  q('#task-modal').classList.add('hidden');
}

function validMinutes(value, minimum, maximum) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum;
}

function saveTaskEditor() {
  if (editingTaskId === null) return;
  const title = q('#edit-task-title').value.trim();
  const focus = Number(q('#edit-task-estimate').value);
  const rest = Number(q('#edit-task-break').value);
  if (!title) return showToast('请先填写任务名称。');
  if (!validMinutes(focus, 1, 600) || !validMinutes(rest, 0, 60)) return showToast('请检查专注和休息时长。');
  bridge?.update_task(editingTaskId, title, focus, rest, q('#edit-task-repeat').checked);
  closeTaskEditor();
  showToast('任务设置已保存。');
}

function renderTaskSelect(tasks) {
  const select = q('#task-select');
  const selected = select.value;
  select.innerHTML = '<option value="-1">选择今天要做的任务（可选）</option>';
  tasks.filter(task => !task.completed).forEach(task => {
    const option = document.createElement('option');
    option.value = String(task.id);
    option.textContent = task.title;
    option.dataset.estimate = String(task.estimate_minutes);
    option.dataset.break = String(task.break_minutes ?? 5);
    option.dataset.repeat = task.repeat_enabled ? '1' : '0';
    select.appendChild(option);
  });
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
  renderTaskPicker();
}

function renderTaskPicker() {
  const select = q('#task-select');
  const button = q('#task-picker-button');
  const menu = q('#task-picker-menu');
  if (!select || !button || !menu) return;
  button.textContent = select.selectedOptions[0]?.textContent || '选择今天要做的任务（可选）';
  menu.innerHTML = '';
  [...select.options].forEach(option => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `task-picker-option${option.value === select.value ? ' selected' : ''}`;
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', option.value === select.value ? 'true' : 'false');
    item.textContent = option.textContent;
    item.onclick = () => {
      select.value = option.value;
      menu.classList.add('hidden');
      button.setAttribute('aria-expanded', 'false');
      renderTaskPicker();
      previewSelectedTask();
    };
    menu.appendChild(item);
  });
}

function comparisonText(stats) {
  const current = Number(stats.focus_seconds || 0);
  const previous = Number(stats.previous_focus_seconds || 0);
  const label = stats.previous_label || '上一周期';
  if (!current && !previous) return `${label}也还没有记录`;
  const delta = current - previous;
  if (!delta) return `与${label}持平`;
  return `${delta > 0 ? '比' : '较'}${label}${delta > 0 ? '多' : '少'} ${formatDuration(Math.abs(delta))}`;
}

function positionChartTooltip(bar, event) {
  const plot = q('.chart-plot');
  const tooltip = q('#chart-tooltip');
  const plotRect = plot.getBoundingClientRect();
  const barRect = bar.getBoundingClientRect();
  const x = event?.clientX ? event.clientX - plotRect.left : barRect.left - plotRect.left + barRect.width / 2;
  const y = barRect.top - plotRect.top;
  tooltip.style.left = `${Math.max(50, Math.min(plotRect.width - 50, x))}px`;
  tooltip.style.top = `${Math.max(32, y)}px`;
}

function formatRewindDate(value) {
  const date = new Date(`${value}T12:00:00`);
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${weekdays[date.getDay()]}`;
}

function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function rewindLevel(seconds) {
  if (!seconds) return 'zero';
  if (seconds < 25 * 60) return 'level-1';
  if (seconds < 60 * 60) return 'level-2';
  if (seconds < 120 * 60) return 'level-3';
  return 'level-4';
}

function renderTimeRewind(data) {
  const grid = q('#rewind-grid');
  const months = q('#rewind-months');
  const tooltip = q('#rewind-tooltip');
  if (!grid || !months || !data) return;
  const days = Array.isArray(data.days) ? data.days : [];
  setTextIfChanged('#rewind-completed', `${Number(data.completed_sessions || 0)}次`);
  setTextIfChanged('#rewind-longest-streak', `${Number(data.longest_streak || 0)}日`);
  setTextIfChanged('#rewind-current-streak', `${Number(data.current_streak || 0)}日`);
  const rewindSignature = JSON.stringify({
    first: days[0]?.date || '',
    last: days[days.length - 1]?.date || '',
    values: days.filter(item => Number(item.focus_seconds || 0) > 0)
      .map(item => [item.date, item.focus_seconds, item.sessions, item.completed_sessions]),
  });
  if (rewindSignature === lastRewindSignature && grid.childElementCount === days.length) return;
  lastRewindSignature = rewindSignature;
  grid.innerHTML = '';
  months.innerHTML = '';
  tooltip.classList.add('hidden');
  const seenMonths = new Set();
  days.forEach((item, index) => {
    const week = Math.floor(index / 7) + 1;
    const date = new Date(`${item.date}T12:00:00`);
    const monthKey = `${date.getFullYear()}-${date.getMonth()}`;
    if (!seenMonths.has(monthKey)) {
      seenMonths.add(monthKey);
      const marker = document.createElement('span');
      // 月份标签不能参与 CSS Grid 自动排版：同一周内跨月时会被挤到隐藏行。
      marker.style.left = `${7 + (week - 1) * 21}px`;
      marker.textContent = date.getMonth() === 0 ? `${date.getFullYear()}年1月` : `${date.getMonth() + 1}月`;
      months.appendChild(marker);
    }
    const seconds = Number(item.focus_seconds || 0);
    const completed = Number(item.completed_sessions || 0);
    const interrupted = Math.max(0, Number(item.sessions || 0) - completed);
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = `heat-cell ${item.future ? 'future' : rewindLevel(seconds)}`;
    if (item.date === localDateKey()) cell.classList.add('today');
    cell.style.gridColumn = String(week);
    cell.style.gridRow = String(index % 7 + 1);
    const details = seconds
      ? `${formatRewindDate(item.date)}\n专注时长：${formatDuration(seconds)}\n完成专注：${completed}次 · 中断专注：${interrupted}次`
      : `${formatRewindDate(item.date)}\n当日还没有专注记录`;
    // 不使用 title：Qt WebEngine 会额外绘制原生深色提示，和咖啡色悬停卡片重叠，
    // 还会在两种提示之间切换时造成闪烁。
    // Qt 5 自带 Chromium 的版本较旧，并不支持 String.prototype.replaceAll；
    // 这里若抛错会让整张年度热力图在第一个日期格停止渲染。
    cell.setAttribute('aria-label', details.split('\n').join('，'));
    if (!item.future) {
      cell.dataset.dateLabel = formatRewindDate(item.date);
      cell.dataset.durationLabel = seconds ? `专注时长：${formatDuration(seconds)}` : '当日还没有专注记录';
      cell.dataset.sessionLabel = seconds ? `完成专注：${completed}次 · 中断专注：${interrupted}次` : '';
    }
    grid.appendChild(cell);
  });
}

function showRewindTooltip(target) {
  const cell = target?.closest?.('.heat-cell:not(.future)');
  const tooltip = q('#rewind-tooltip');
  if (!cell || !tooltip || !q('#rewind-grid').contains(cell)) return;
  tooltip.innerHTML = `<strong>${cell.dataset.dateLabel}</strong><span>${cell.dataset.durationLabel}</span>${cell.dataset.sessionLabel ? `<small>${cell.dataset.sessionLabel}</small>` : ''}`;
  const rect = cell.getBoundingClientRect();
  tooltip.style.left = `${rect.left + rect.width / 2}px`;
  tooltip.style.top = `${rect.top}px`;
  tooltip.classList.remove('hidden');
}

function bindRewindEvents() {
  const grid = q('#rewind-grid');
  const tooltip = q('#rewind-tooltip');
  if (!grid || !tooltip) return;
  grid.addEventListener('mouseover', event => showRewindTooltip(event.target));
  grid.addEventListener('focusin', event => showRewindTooltip(event.target));
  grid.addEventListener('mouseout', () => tooltip.classList.add('hidden'));
  grid.addEventListener('focusout', () => tooltip.classList.add('hidden'));
}

function formatSessionTime(value) {
  const date = new Date(String(value || '').replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return '时间未知';
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function renderRecentFocus(items) {
  const list = q('#recent-focus-list');
  if (!list) return;
  list.innerHTML = '';
  const sessions = Array.isArray(items) ? items : [];
  if (!sessions.length) {
    list.innerHTML = '<div class="recent-focus-empty">完成第一段专注后，这里会留下你的第一条记录。</div>';
    return;
  }
  sessions.forEach(item => {
    const row = document.createElement('div');
    const completed = Boolean(item.completed);
    row.className = `recent-focus-row ${completed ? 'completed' : 'interrupted'}`;
    const state = completed ? '已完成' : '中断保存';
    row.innerHTML = `<span class="recent-focus-dot" aria-hidden="true"></span><div><strong></strong><small>${formatSessionTime(item.ended_at || item.started_at)} · ${formatDuration(item.duration_seconds)}</small></div><em>${state}</em>`;
    row.querySelector('strong').textContent = item.task_name || '未命名专注';
    list.appendChild(row);
  });
}

function renderStats(stats) {
  q('#stat-focus').textContent = formatDuration(stats.focus_seconds);
  q('#stat-streak').textContent = `${stats.streak || 0}天`;
  q('#stat-completed').textContent = `${stats.completed_sessions || 0}次`;
  q('#stat-interrupted').textContent = `${stats.interrupted_sessions || 0}次`;
  q('#stat-room').textContent = formatDuration(stats.room_seconds);
  q('#stat-focus-compare').textContent = comparisonText(stats);
  const focusLabels = { today: '今日专注', week: '本周专注', month: '本月专注', year: '本年专注' };
  const roomLabels = { today: '今日打开时长', week: '本周打开时长', month: '本月打开时长', year: '本年打开时长' };
  q('#metric-focus-title').textContent = focusLabels[stats.period] || '今日专注';
  q('#metric-room-title').textContent = roomLabels[stats.period] || '今日打开时长';

  const trend = (stats.trend || []).slice(0, 7);
  const total = trend.reduce((sum, row) => sum + Number(row.focus_seconds || 0), 0);
  q('#week-total').textContent = `累计 ${formatDuration(total)}`;
  q('#chart-empty').classList.toggle('hidden', total > 0);
  const peakSeconds = Math.max(0, ...trend.map(row => Number(row.focus_seconds || 0)));
  const minuteMode = peakSeconds <= 3600;
  const axisMax = minuteMode ? 60 : Math.max(1, Math.ceil(peakSeconds / 3600));
  const axisStep = minuteMode ? 15 : 1;

  const axis = q('#chart-y-axis');
  axis.innerHTML = '';
  for (let value = axisMax; value >= 0; value -= axisStep) {
    const label = document.createElement('span');
    label.textContent = minuteMode ? `${value}分钟` : `${value}小时`;
    axis.appendChild(label);
  }
  const grid = q('#chart-grid');
  grid.innerHTML = '';
  for (let value = 0; value <= axisMax; value += axisStep) {
    const line = document.createElement('i');
    line.style.bottom = `${value / axisMax * 100}%`;
    grid.appendChild(line);
  }

  const chart = q('#chart');
  const xAxis = q('#chart-x-axis');
  const tooltip = q('#chart-tooltip');
  chart.innerHTML = '';
  xAxis.innerHTML = '';
  tooltip.classList.add('hidden');
  trend.forEach(row => {
    const seconds = Number(row.focus_seconds || 0);
    const col = document.createElement('div');
    const bar = document.createElement('div');
    const date = document.createElement('span');
    const parsed = new Date(`${row.date}T00:00:00`);
    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    const month = Number(row.date.slice(5, 7));
    const day = Number(row.date.slice(8, 10));
    const description = `${weekdays[parsed.getDay()]} ${month}月${day}日 · ${formatDuration(seconds)}`;
    col.className = 'bar-col';
    bar.className = 'bar';
    bar.tabIndex = 0;
    bar.setAttribute('aria-label', description);
    bar.style.height = `${Math.min(100, Math.max(seconds ? 2 : 0, seconds / (axisMax * (minuteMode ? 60 : 3600)) * 100))}%`;
    date.textContent = `${weekdays[parsed.getDay()]}\n${month}月${day}日`;
    const show = event => {
      tooltip.textContent = description;
      tooltip.classList.remove('hidden');
      positionChartTooltip(bar, event);
    };
    bar.addEventListener('mouseenter', show);
    bar.addEventListener('mousemove', event => positionChartTooltip(bar, event));
    bar.addEventListener('mouseleave', () => tooltip.classList.add('hidden'));
    bar.addEventListener('focus', show);
    bar.addEventListener('blur', () => tooltip.classList.add('hidden'));
    col.appendChild(bar);
    chart.appendChild(col);
    xAxis.appendChild(date);
  });

  const completed = Number(stats.completed_sessions || 0);
  const interrupted = Number(stats.interrupted_sessions || 0);
  let note = '莲心记录：先从一个短短的专注开始吧。';
  if (Number(stats.focus_seconds || 0) > 0) {
    note = `莲心记录：${focusLabels[stats.period] || '这段时间'}累计 ${formatDuration(stats.focus_seconds)}，完整完成 ${completed} 次。`;
    if (interrupted > completed && interrupted > 1) note += ' 中断次数稍多，可以把下一次目标调短一些。';
    else if (Number(stats.streak || 0) >= 3) note += ` 已连续坚持 ${stats.streak} 天，节奏正在稳定下来。`;
    else note += ` ${comparisonText(stats)}。`;
  }
  q('#growth-note').textContent = note;
  renderTimeRewind(stats.time_rewind);
  renderRecentFocus(stats.recent_focus);
}

function switchView(view) {
  currentView = view;
  document.body.classList.toggle('focus-mode', view === 'focus');
  qa('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  qa('.view').forEach(section => section.classList.toggle('active', section.id === `view-${view}`));
  const labels = { home: '开始专注', tasks: '任务清单', focus: '沉浸专注', stats: '成长记录', space: '我的空间', settings: '设置' };
  q('#page-kicker').textContent = labels[view] || '';
  bridge?.set_statistics_active(view === 'stats');
  if (view === 'stats') refreshStats();
  if (view === 'space') refreshSpace();
  if (view === 'home' || view === 'focus') renderTimer();
}

function enterFocusView() {
  switchView('focus');
  if (roomSettings.auto_fullscreen) bridge?.set_focus_fullscreen(true);
}

function leaveFocusView() {
  switchView('home');
  if (roomSettings.auto_fullscreen) bridge?.set_focus_fullscreen(false);
}

function refreshStats() {
  if (!bridge || currentView !== 'stats') return;
  if (statsMode !== 'overview') return refreshReport();
  const requestedRange = selectedRange;
  bridge.get_statistics(requestedRange, payload => {
    if (currentView === 'stats' && selectedRange === requestedRange) renderStats(JSON.parse(payload));
  });
}

function reportDateLabel(value, compact = false) {
  if (!value) return '';
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return compact ? `${parsed.getMonth() + 1}/${parsed.getDate()}` : `${parsed.getMonth() + 1}月${parsed.getDate()}日`;
}

function renderReport(report) {
  currentReport = report;
  const metrics = report.metrics || {};
  const narrative = report.narrative || {};
  q('#report-type-label').textContent = report.type === 'month' ? '月报' : '周报';
  q('#report-period-label').textContent = report.label || '';
  q('#report-period-status').textContent = report.is_current ? `进行中 · 已记录 ${report.elapsed_days || 0} 天` : '本周期已结束';
  q('#report-title').textContent = narrative.title || '这一段时间的回顾';
  q('#report-body').textContent = narrative.body || '';
  q('#report-suggestion').textContent = narrative.suggestion || '';
  q('#report-focus').textContent = formatDuration(metrics.focus_seconds || 0);
  q('#report-focus-detail').textContent = `完整专注 ${formatDuration(metrics.completed_focus_seconds || 0)}`;
  q('#report-active-days').textContent = `${metrics.active_days || 0}天`;
  q('#report-streak').textContent = `最长连续 ${metrics.longest_streak || 0} 天`;
  q('#report-completed').textContent = `${metrics.completed_sessions || 0}次`;
  q('#report-completion-rate').textContent = `完成率 ${metrics.completion_rate || 0}%`;
  q('#report-tasks').textContent = `${metrics.completed_tasks || 0}项`;
  q('#report-room').textContent = `自习室 ${formatDuration(metrics.room_seconds || 0)}`;
  const comparison = report.comparison || {};
  const delta = Number(comparison.focus_delta_seconds || 0);
  q('#report-compare').textContent = comparison.has_previous_data
    ? `相比${comparison.previous_label || '上一周期'}，专注时长${delta >= 0 ? '多' : '少'} ${formatDuration(Math.abs(delta))}`
    : '暂无可比较的上一周期记录，先把这一段时间好好留住。';
  q('#report-next').disabled = Boolean(report.is_current);

  const trend = report.type === 'month' ? (report.weekly_trend || []) : (report.daily_trend || []);
  const bars = q('#report-bars');
  bars.innerHTML = '';
  bars.style.gridTemplateColumns = `repeat(${Math.max(1, trend.length)}, minmax(0, 1fr))`;
  const peak = Math.max(0, ...trend.map(item => Number(item.focus_seconds || 0)));
  trend.forEach(item => {
    const seconds = Number(item.focus_seconds || 0);
    const column = document.createElement('div');
    const bar = document.createElement('div');
    const label = document.createElement('small');
    column.className = 'report-bar-column';
    bar.className = 'report-bar';
    bar.style.height = `${peak ? Math.max(seconds ? 4 : 0, seconds / peak * 100) : 0}%`;
    bar.setAttribute('aria-label', `专注 ${formatDuration(seconds)}`);
    const date = item.week_start || item.date || '';
    label.textContent = report.type === 'month' ? `周${reportDateLabel(date, true)}` : reportDateLabel(date, true);
    column.append(bar, label);
    bars.appendChild(column);
  });
  q('#report-trend-title').textContent = report.type === 'month' ? '本月每周节奏' : '本周每天节奏';
  const highlights = report.highlights || {};
  q('#report-highlights').textContent = highlights.most_common_time_band
    ? `最常开始专注：${highlights.most_common_time_band}` : '记录会慢慢显出你的节奏';
  const taskList = q('#report-tasks-list');
  taskList.innerHTML = '';
  const tasks = report.top_tasks || [];
  if (!tasks.length) {
    taskList.innerHTML = '<div class="report-empty">完成第一段专注后，这里会留下任务足迹。</div>';
  } else {
    tasks.forEach(task => {
      const row = document.createElement('div');
      row.className = 'report-task-row';
      row.innerHTML = `<div><strong></strong><small>${task.sessions || 0} 次专注 · 完成 ${task.completed_sessions || 0} 次</small></div><span>${formatDuration(task.seconds || 0)}</span>`;
      row.querySelector('strong').textContent = task.task_name || '未绑定任务';
      taskList.appendChild(row);
    });
  }
}

function refreshReport() {
  if (!bridge || currentView !== 'stats' || statsMode === 'overview') return;
  const requestedMode = statsMode;
  const requestedAnchor = reportAnchor;
  bridge.get_report(requestedMode, requestedAnchor, payload => {
    if (currentView === 'stats' && statsMode === requestedMode && reportAnchor === requestedAnchor) renderReport(JSON.parse(payload));
  });
}

function moveReportPeriod(direction) {
  const type = statsMode;
  const anchor = currentReport?.anchor || reportAnchor || new Date().toISOString().slice(0, 10);
  const parsed = new Date(`${anchor}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return;
  if (type === 'month') parsed.setMonth(parsed.getMonth() + direction);
  else parsed.setDate(parsed.getDate() + direction * 7);
  reportAnchor = parsed.toISOString().slice(0, 10);
  refreshReport();
}

function previewSelectedTask() {
  if (timerState.active) return;
  const option = q('#task-select').selectedOptions[0];
  const activePreset = q('.preset.active');
  const minutes = Number(option?.dataset.estimate || activePreset?.dataset.focus || roomSettings.focus_minutes || 25);
  timerState = { ...timerState, remaining: minutes * 60, total: minutes * 60, phase: 'idle', active: false, paused: false, preview: true };
  updateGoal();
  renderTimer();
}

function startSelectedFocus() {
  if (!bridge) return showToast('自习室后端尚未连接，请稍后重试。');
  if (timerState.active) return enterFocusView();
  const preset = q('.preset.active');
  const selected = q('#task-select').selectedOptions[0];
  const taskEstimate = Number(selected?.dataset.estimate || 0);
  const focus = taskEstimate > 0 ? taskEstimate : Number(preset?.dataset.focus || roomSettings.focus_minutes || 25);
  const rest = roomSettings.auto_break ? Number(selected?.dataset.break || preset?.dataset.break || roomSettings.break_minutes || 5) : 0;
  timerState = { ...timerState, remaining: focus * 60, total: focus * 60, phase: 'focus', active: true, paused: false, preview: false, task_name: selected?.dataset.estimate ? selected.textContent : '' };
  renderTimer();
  bridge.start_focus(focus, rest, Number(q('#task-select').value));
  enterFocusView();
}

function loadSettings(settings) {
  roomSettings = { ...roomSettings, ...(settings || {}) };
  q('#setting-focus').value = roomSettings.focus_minutes;
  q('#setting-break').value = roomSettings.break_minutes;
  q('#setting-auto').checked = Boolean(roomSettings.auto_break);
  q('#setting-fullscreen').checked = Boolean(roomSettings.auto_fullscreen);
  q('#setting-completion').checked = Boolean(roomSettings.show_completion);
  q('#setting-animation').checked = Boolean(roomSettings.animations);
  document.body.classList.toggle('no-motion', !roomSettings.animations);
  const preset = qa('.preset').find(item => Number(item.dataset.focus) === Number(roomSettings.focus_minutes));
  if (preset) {
    qa('.preset').forEach(item => item.classList.remove('active'));
    preset.classList.add('active');
  }
}

function saveSettings() {
  const focus = Number(q('#setting-focus').value);
  const rest = Number(q('#setting-break').value);
  if (!validMinutes(focus, 1, 180) || !validMinutes(rest, 0, 60)) return showToast('请检查默认专注和休息时长。');
  roomSettings = {
    focus_minutes: focus,
    break_minutes: rest,
    auto_break: q('#setting-auto').checked,
    auto_fullscreen: q('#setting-fullscreen').checked,
    show_completion: q('#setting-completion').checked,
    animations: q('#setting-animation').checked,
  };
  document.body.classList.toggle('no-motion', !roomSettings.animations);
  bridge?.save_settings(focus, rest, roomSettings.auto_break, roomSettings.auto_fullscreen, roomSettings.show_completion, roomSettings.animations);
  q('#settings-status').textContent = '已保存';
  setTimeout(() => { q('#settings-status').textContent = ''; }, 2400);
  showToast('自习室设置已保存。');
}

function resetSettings() {
  loadSettings({ focus_minutes: 25, break_minutes: 5, auto_break: true, auto_fullscreen: true, show_completion: true, animations: true });
  saveSettings();
}

function connectBridge() {
  if (typeof QWebChannel === 'undefined' || typeof qt === 'undefined') {
    showToast('无法连接自习室后端，请重新打开窗口。', 5000);
    console.error('[自习室Web] QWebChannel 不可用');
    return;
  }
  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.studyBridge;
    bridge.get_initial_state(payload => {
      const initial = JSON.parse(payload);
      renderTasks(initial.tasks);
      renderTaskSelect(initial.tasks);
      timerState = { ...timerState, ...initial.timer };
      loadSettings(initial.settings);
      renderClock(initial.clock);
      // 首页只应用当前背景，不提前构建隐藏的壁纸列表和 371 个年度热力格。
      // 对应页面首次打开时再按需渲染，降低启动时的图片解码和 DOM 压力。
      spaceState = initial.space;
      applySpaceVisuals(initial.space?.settings || {});
      const hour = new Date().getHours();
      const greeting = hour < 12 ? '早上好' : (hour < 18 ? '下午好' : '晚上好');
      q('#time-greeting').textContent = `${greeting}，${initial.user_name || '朋友'}`;
      updateGoal(initial.tasks);
      renderTimer();
      if (timerState.active) enterFocusView();
    });

    bridge.timer_tick.connect(payload => {
      const next = JSON.parse(payload);
      const keepPreview = next.phase === 'idle' && timerState.preview;
      timerState = {
        ...timerState,
        ...next,
        remaining: keepPreview ? timerState.remaining : next.remaining,
        total: keepPreview ? timerState.total : next.total,
        active: next.phase !== 'idle',
        paused: Boolean(next.paused),
        preview: keepPreview,
      };
      renderTimer();
    });
    bridge.clock_changed.connect(payload => renderClock(JSON.parse(payload)));
    bridge.phase_changed.connect(phase => {
      if (phase === 'paused') {
        timerState.paused = true;
      } else {
        timerState.paused = false;
        timerState.phase = phase;
        timerState.active = phase !== 'idle';
      }
      q('#timer-controls').classList.toggle('hidden', !timerState.active);
      q('#start-focus').classList.toggle('hidden', timerState.active);
      q('#focus-pause').disabled = !timerState.active;
      q('#focus-stop').disabled = !timerState.active;
      if (phase === 'paused') setCompanion('我会在这里安静等你，准备好后再继续。');
      if (phase === 'break') q('#focus-companion').textContent = '这一段已经完成了。站起来走走，喝口水，再回来也不迟。';
      if (phase === 'idle') {
        timerState.preview = false;
        timerState.remaining = 0;
        timerState.total = 0;
        if (currentView === 'focus') leaveFocusView();
        q('#focus-companion').textContent = '这一段专注已经结束，可以回到自习室看看今天的记录。';
      }
      renderTimer();
    });
    bridge.tasks_changed.connect(payload => {
      const tasks = JSON.parse(payload);
      renderTasks(tasks);
      renderTaskSelect(tasks);
      updateGoal(tasks);
    });
    bridge.statistics_changed.connect(payload => {
      // 完整专注/中断保存后，直接使用后端已写入数据库的最新快照，避免页面停留
      // 在成长记录时又发起一次异步请求而短暂显示旧的 0 数据。
      if (currentView !== 'stats') return;
      const latest = JSON.parse(payload);
      if (statsMode !== 'overview') refreshReport();
      else if (selectedRange === 'today') renderStats(latest);
      else refreshStats();
    });
    bridge.companion_message.connect(setCompanion);
    bridge.statistics_changed.connect(() => { if (currentView === 'space') refreshSpace(); });
    bridge.focus_completed.connect(payload => {
      const data = JSON.parse(payload);
      if (roomSettings.show_completion) {
        q('#completion-note').textContent = `本次专注已完成 · ${formatDuration(data.duration)}${data.task_name ? ` · 任务：${data.task_name}` : ''}`;
        q('#completion-note').classList.remove('hidden');
      }
      const taskCard = q('#completion-task-card');
      completionTaskId = Number(data.task_id) || null;
      const canCompleteTask = completionTaskId && !data.repeat_enabled;
      taskCard.classList.toggle('hidden', !canCompleteTask);
      if (canCompleteTask) q('#completion-task-hint').textContent = `“${data.task_name || '当前任务'}”还要继续吗？`;
    });
  });
}

function bindEvents() {
  bindRewindEvents();
  qa('.nav-item').forEach(button => button.onclick = () => switchView(button.dataset.view));
  qa('.preset').forEach(button => button.onclick = () => {
    if (timerState.active) return showToast('当前计时进行中，结束后才能更换时长。');
    qa('.preset').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    q('#task-select').value = '-1';
    previewSelectedTask();
  });
  q('#start-focus').onclick = startSelectedFocus;
  q('#task-select').onchange = previewSelectedTask;
  q('#task-picker-button').onclick = event => {
    event.stopPropagation();
    const menu = q('#task-picker-menu');
    const nowHidden = menu.classList.toggle('hidden');
    q('#task-picker-button').setAttribute('aria-expanded', nowHidden ? 'false' : 'true');
  };
  document.addEventListener('click', event => {
    if (!q('#task-picker').contains(event.target)) {
      q('#task-picker-menu').classList.add('hidden');
      q('#task-picker-button').setAttribute('aria-expanded', 'false');
    }
  });
  q('#reenter-focus').onclick = enterFocusView;
  q('#pause-focus').onclick = () => bridge?.toggle_pause();
  q('#stop-focus').onclick = () => bridge?.stop_focus();
  q('#completion-mark-task').onclick = () => {
    if (!completionTaskId) return;
    bridge?.complete_task(completionTaskId);
    q('#completion-task-card').classList.add('hidden');
    showToast('任务已标记完成。');
    completionTaskId = null;
  };
  q('#completion-keep-task').onclick = () => {
    q('#completion-task-card').classList.add('hidden');
    completionTaskId = null;
    showToast('任务会保留在清单里，下一轮继续。');
  };
  q('#space-wallpaper-opacity').oninput = () => { if (!spaceState) return; spaceState.settings.wallpaper_opacity = Number(q('#space-wallpaper-opacity').value) / 100; q('#space-wallpaper-opacity-value').textContent = `${q('#space-wallpaper-opacity').value}%`; applySpaceVisuals(spaceState.settings); };
  q('#space-mask-opacity').oninput = () => { if (!spaceState) return; spaceState.settings.content_mask_opacity = Number(q('#space-mask-opacity').value) / 100; q('#space-mask-opacity-value').textContent = `${q('#space-mask-opacity').value}%`; applySpaceVisuals(spaceState.settings); };
  q('#space-fit').onchange = () => { if (!spaceState) return; spaceState.settings.fit = q('#space-fit').value; applySpaceVisuals(spaceState.settings); };
  q('#space-save').onclick = () => {
    if (!spaceState) return;
    const s = spaceState.settings;
    pendingSpaceWallpaper = s.wallpaper;
    bridge?.save_space_settings(s.wallpaper, s.wallpaper_opacity, s.content_mask_opacity, s.fit, payload => {
      const data = JSON.parse(payload);
      if (data.settings?.wallpaper === pendingSpaceWallpaper) pendingSpaceWallpaper = null;
      renderSpace(data);
    });
    showToast('自习室布置已保存。');
  };
  q('#space-custom-wallpaper').onclick = () => bridge?.choose_custom_wallpaper(path => {
    if (!path || !spaceState) return;
    const s = spaceState.settings;
    pendingSpaceWallpaper = path;
    bridge?.save_space_settings(path, s.wallpaper_opacity, s.content_mask_opacity, s.fit, payload => {
      const data = JSON.parse(payload);
      if (data.settings?.wallpaper === pendingSpaceWallpaper) pendingSpaceWallpaper = null;
      renderSpace(data);
    });
  });
  q('#focus-pause').onclick = () => bridge?.toggle_pause();
  q('#focus-stop').onclick = () => bridge?.stop_focus();
  q('#focus-back').onclick = leaveFocusView;
  q('#add-task').onclick = () => {
    const input = q('#new-task');
    const focus = Number(q('#task-estimate').value);
    const rest = Number(q('#task-break').value);
    if (!input.value.trim()) return showToast('请先填写任务名称。');
    if (!validMinutes(focus, 1, 600) || !validMinutes(rest, 0, 60)) return showToast('请检查专注和休息时长。');
    bridge?.add_task(input.value.trim(), focus, rest, q('#task-repeat').checked);
    input.value = '';
    q('#task-repeat').checked = false;
    showToast('任务已加入清单。');
  };
  q('#new-task').addEventListener('keydown', event => { if (event.key === 'Enter') q('#add-task').click(); });
  q('#task-search').oninput = () => renderTasks(allTasks);
  q('#task-modal-close').onclick = closeTaskEditor;
  q('#task-modal-cancel').onclick = closeTaskEditor;
  q('#task-modal-save').onclick = saveTaskEditor;
  q('#task-modal').onclick = event => { if (event.target === q('#task-modal')) closeTaskEditor(); };
  qa('.task-filter').forEach(button => button.onclick = () => {
    taskFilter = button.dataset.filter;
    qa('.task-filter').forEach(item => item.classList.toggle('active', item === button));
    renderTasks(allTasks);
  });
  qa('.range-tab').forEach(button => button.onclick = () => {
    selectedRange = button.dataset.range;
    qa('.range-tab').forEach(item => item.classList.toggle('active', item === button));
    refreshStats();
  });
  qa('.report-mode-tab').forEach(button => button.onclick = () => {
    statsMode = button.dataset.reportMode || 'overview';
    reportAnchor = '';
    currentReport = null;
    qa('.report-mode-tab').forEach(item => item.classList.toggle('active', item === button));
    q('#stats-overview').classList.toggle('hidden', statsMode !== 'overview');
    q('#report-panel').classList.toggle('hidden', statsMode === 'overview');
    refreshStats();
  });
  q('#report-previous').onclick = () => moveReportPeriod(-1);
  q('#report-next').onclick = () => { if (!currentReport?.is_current) moveReportPeriod(1); };
  q('#save-settings').onclick = saveSettings;
  q('#reset-settings').onclick = resetSettings;
  ['#minimize', '#focus-minimize'].forEach(selector => { const node = q(selector); if (node) node.onclick = () => bridge?.minimize_window(); });
  ['#fullscreen', '#focus-fullscreen'].forEach(selector => { const node = q(selector); if (node) node.onclick = () => bridge?.toggle_fullscreen(); });
  ['#close', '#focus-close'].forEach(selector => { const node = q(selector); if (node) node.onclick = () => bridge?.close_window(); });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (!q('#task-modal').classList.contains('hidden')) closeTaskEditor();
    else if (currentView === 'focus') leaveFocusView();
  });
}

function runDiagnostics() {
  const required = ['#view-home', '#view-focus', '#view-tasks', '#view-stats', '#view-settings', '#timer-value', '#task-list', '#chart'];
  const missing = required.filter(selector => !q(selector));
  window.studyRoomDiagnostics = {
    version: STUDY_ROOM_UI_VERSION,
    missingElements: missing,
    loadedAt: new Date().toISOString(),
  };
  if (missing.length) console.error(`[自习室Web] 缺少关键界面元素: ${missing.join(', ')}`);
  else console.info(`[自习室Web] 界面版本 ${STUDY_ROOM_UI_VERSION} 已加载，结构检查通过`);
}

window.addEventListener('error', event => console.error(`[自习室Web] 未捕获错误: ${event.message}`));
runDiagnostics();
bindEvents();
connectBridge();
