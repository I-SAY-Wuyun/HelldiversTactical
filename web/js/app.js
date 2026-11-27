/* Helldivers Tactical Command — UI controller. */
(function () {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const num = new Intl.NumberFormat('en-US');
  const COLORS = {
    humans: '#4cc2ff', terminids: '#d7a52e', automaton: '#e04a3b', illuminate: '#a27bde', unknown: '#8b8a71'
  };
  const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
  const APP = {
    state: null,
    settings: {},
    history: [],
    autostart: false,
    version: '1.0.0',
    booted: false,
    activeView: 'command',
    map: null,
    miniClock: null,
    grandClock: null,
    widgetClock: null,
    widgetTab: 'clock',
    widgetAlert: null,
    mode: 'widget',
    alertQueue: [],
    alertOpen: false,
    seenAlertIds: new Set(),
    lastRenderedFetch: null,
    selectedPlanet: null,
    pendingPayloads: [],
    refreshBusy: false
  };

  /* ── utilities ──────────────────────────────────────────────────────── */
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function compact(value) {
    const n = Number(value || 0);
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
    return num.format(Math.round(n));
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function duration(seconds) {
    let s = Math.max(0, Math.floor(Number(seconds) || 0));
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60); s %= 60;
    if (d > 0) return `${d}D ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  function remaining(expiresAt, fallbackSeconds) {
    if (expiresAt) {
      const t = Date.parse(expiresAt);
      if (Number.isFinite(t)) return Math.max(0, (t - Date.now()) / 1000);
    }
    return Number(fallbackSeconds) || 0;
  }
  function localTime(iso, withDate = false) {
    if (!iso) return '--:--:--';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '--:--:--';
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    if (!withDate) return `${hh}:${mm}:${ss}`;
    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')} ${hh}:${mm}`;
  }
  function dateLabel(d) {
    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
  }
  function timezoneLabel() {
    const offset = -new Date().getTimezoneOffset();
    const sign = offset >= 0 ? '+' : '-';
    const hh = String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0');
    const mm = String(Math.abs(offset) % 60).padStart(2, '0');
    return `UTC${sign}${hh}:${mm}`;
  }
  function toast(message, type = '') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    $('#toast-layer').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 350); }, 3600);
  }
  function apiReady() {
    return !!(window.pywebview && window.pywebview.api);
  }
  async function callApi(name, ...args) {
    if (!apiReady()) throw new Error('bridge not ready');
    return window.pywebview.api[name](...args);
  }
  function ownerColor(planet) {
    return COLORS[(planet && planet.owner && planet.owner.key) || 'unknown'] || COLORS.unknown;
  }

  /* ── boot ───────────────────────────────────────────────────────────── */
  function boot() {
    if (APP.booted) return;
    APP.booted = true;
    initClocks();
    APP.map = new window.GalaxyMap({
      stage: $('#map-stage'), canvas: $('#galaxy-map'), tooltip: $('#map-tooltip'), readout: $('#map-readout'),
      onSelect: (planet) => { APP.selectedPlanet = planet; renderDossier(planet); }
    });
    bindEvents();
    tickClock();
    setInterval(tickClock, 250);
    setInterval(tickCountdowns, 1000);
    setInterval(pollState, 15000);
    bootData();
  }

  async function bootData() {
    let payload = null;
    for (let attempt = 0; attempt < 12; attempt += 1) {
      try {
        if (!apiReady()) throw new Error('bridge not ready');
        payload = await callApi('bootstrap');
        break;
      } catch (err) {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
    if (!payload) {
      finishBoot();
      toast('数据链桥接失败，请查看日志。', 'error');
      return;
    }
    APP.settings = payload.settings || {};
    APP.autostart = !!payload.autostart;
    APP.version = payload.version || APP.version;
    APP.mode = payload.mode === 'expanded' ? 'expanded' : 'widget';
    APP.history = payload.history || [];
    applySettings();
    if (payload.state) {
      APP.state = payload.state;
      renderAll();
    }
    finishBoot();
    if (payload.error) toast('数据源暂时不可用，正在使用缓存。', 'error');
    APP.pendingPayloads.forEach((p) => receive(p));
    APP.pendingPayloads = [];
  }

  function finishBoot() {
    renderClockMeta();
    try { setWidgetTab(localStorage.getItem('hd2.widgetTab') || 'clock', false); } catch (err) { setWidgetTab('clock', false); }
    setTimeout(() => {
      $('#boot').classList.add('is-done');
      applyMode(APP.mode);
      setTimeout(() => {
        $('#boot').style.display = 'none';
        if (APP.state && APP.mode === 'expanded') toast('战术指挥终端已上线 // UPLINK ESTABLISHED');
      }, 650);
    }, 250);
  }

  function applyMode(mode) {
    APP.mode = mode === 'expanded' ? 'expanded' : 'widget';
    const expanded = APP.mode === 'expanded';
    document.body.classList.toggle('mode-expanded', expanded);
    document.body.classList.toggle('mode-widget', !expanded);
    $('#widget').classList.toggle('is-hidden', expanded);
    $('#app').classList.toggle('is-hidden', !expanded);
    if (expanded && APP.map) {
      setTimeout(() => APP.map.setVisible(APP.activeView === 'map'), 80);
    }
    renderWidget();
  }

  async function expandApp() {
    try {
      await callApi('expand');
    } catch (err) { /* backend may already be resizing */ }
    applyMode('expanded');
  }

  async function collapseApp() {
    try {
      await callApi('collapse');
    } catch (err) { /* noop */ }
    applyMode('widget');
  }

  function startDrag(event) {
    if (event) event.preventDefault();
    if (!apiReady()) return;
    callApi('start_drag').catch(() => {});
  }

  /* ── clocks ─────────────────────────────────────────────────────────── */
  function initClocks() {
    APP.miniClock = window.Nixie.create($('#mini-nixie'), 4, { flicker: true });
    APP.grandClock = window.Nixie.create($('#grand-nixie'), 6, { flicker: true });
    APP.widgetClock = window.Nixie.create($('#widget-nixie'), 6, { flicker: false });
    $('#clock-zone').textContent = timezoneLabel();
    $('#grand-zone').textContent = timezoneLabel();
  }

  function tickClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    if (APP.miniClock) APP.miniClock.update(`${hh}${mm}`);
    if (APP.grandClock) APP.grandClock.update(`${hh}${mm}${ss}`);
    if (APP.widgetClock) APP.widgetClock.update(`${hh}${mm}${ss}`);
    $('#top-clock').textContent = `${hh}:${mm}`;
    $('#clock-date').textContent = dateLabel(now);
    $('#clock-weekday').textContent = WEEKDAYS[now.getDay()];
    $('#grand-date').textContent = dateLabel(now);
    $('#grand-week').textContent = WEEKDAYS[now.getDay()];
    const wDate = $('#w-date');
    if (wDate) {
      wDate.textContent = dateLabel(now);
      $('#w-week').textContent = WEEKDAYS[now.getDay()];
    }
    const uptime = Math.floor((Date.now() - (window.__HD2_BOOT_TS || Date.now())) / 1000);
    $('#grand-uptime').textContent = duration(uptime);
  }

  function renderClockMeta() {
    $('#version-label').textContent = `V${APP.version}`;
    $('#grand-war').textContent = (APP.state && APP.state.war && APP.state.war.id) || '801';
    $('#clock-zone').textContent = timezoneLabel();
    $('#grand-zone').textContent = timezoneLabel();
  }

  /* ── events ─────────────────────────────────────────────────────────── */
  function bindEvents() {
    $$('.tab').forEach((tab) => tab.addEventListener('click', () => showView(tab.dataset.view)));
    $$('[data-go-view]').forEach((btn) => btn.addEventListener('click', () => showView(btn.dataset.goView)));
    $('#refresh-btn').addEventListener('click', refreshData);
    $('#shortcut-btn').addEventListener('click', createShortcut);
    $('#map-zoom-in').addEventListener('click', () => APP.map && APP.map.zoomBy(1.25));
    $('#map-zoom-out').addEventListener('click', () => APP.map && APP.map.zoomBy(0.8));
    $('#map-fit').addEventListener('click', () => APP.map && APP.map.fit());
    $('#autostart-switch').addEventListener('click', () => toggleAutostart());
    $('#alerts-switch').addEventListener('click', () => toggleSetting('alerts_enabled', !APP.settings.alerts_enabled, '事件告警'));
    $('#focus-switch').addEventListener('click', () => toggleSetting('alert_focus', !APP.settings.alert_focus, '告警置顶'));
    $('#collapse-btn').addEventListener('click', collapseApp);
    $('#hide-btn').addEventListener('click', () => callApi('hide_to_tray').catch(() => {}));
    $('#settings-btn').addEventListener('click', openSettings);
    $('#widget-refresh').addEventListener('click', (event) => { event.stopPropagation(); refreshData(); });
    $('#widget-settings').addEventListener('click', (event) => { event.stopPropagation(); openSettings(); });
    $('#widget-expand').addEventListener('click', (event) => { event.stopPropagation(); expandApp(); });
    $('#widget-hide').addEventListener('click', (event) => { event.stopPropagation(); callApi('hide_to_tray').catch(() => {}); });
    $('#widget-refresh').addEventListener('dblclick', (event) => event.stopPropagation());
    $$('.widget-tab').forEach((tab) => tab.addEventListener('click', (event) => {
      event.stopPropagation();
      setWidgetTab(tab.dataset.wtab, true);
    }));
    bindSettings();
    $('#widget-grip').addEventListener('pointerdown', (event) => {
      event.stopPropagation();
      APP._suppressClickUntil = Date.now() + 1200;
    });
    window.addEventListener('pointerup', () => { APP._suppressClickUntil = Date.now() + 350; });
    $('#widget').addEventListener('click', (event) => {
      if (Date.now() < (APP._suppressClickUntil || 0)) return;
      if (event.target.closest('button') || event.target.closest('#widget-grip')) return;
      expandApp();
    });
    document.addEventListener('keydown', (event) => {
      if (event.target && /INPUT|TEXTAREA/.test(event.target.tagName)) return;
      const keys = { '1': 'command', '2': 'map', '3': 'intel', '4': 'clock' };
      if (keys[event.key]) showView(keys[event.key]);
      if (event.key.toLowerCase() === 'r') refreshData();
      if (event.key === 'Escape') {
        if ($('#settings-modal').classList.contains('is-open')) closeSettings();
        else closeAlert();
      }
    });
  }

  function bindSettings() {
    $('#settings-close').addEventListener('click', closeSettings);
    $('#settings-done').addEventListener('click', closeSettings);
    $('#settings-scrim').addEventListener('click', closeSettings);
    $$('#setting-zorder .seg').forEach((btn) => {
      btn.addEventListener('click', () => setZorder(btn.dataset.zorder));
    });
    const range = $('#opacity-range');
    let timer = 0;
    range.addEventListener('input', () => {
      const value = clamp(Number(range.value) || 100, 35, 100);
      APP.settings.window_opacity = value;
      $('#opacity-value').textContent = `${value}%`;
      clearTimeout(timer);
      timer = setTimeout(() => {
        callApi('set_setting', 'window_opacity', value).catch(() => toast('透明度设置失败。', 'error'));
      }, 60);
    });
  }

  function openSettings() {
    applySettings();
    $('#settings-modal').classList.add('is-open');
    $('#settings-modal').setAttribute('aria-hidden', 'false');
    setTimeout(() => $('#settings-close').focus(), 30);
  }

  function closeSettings() {
    $('#settings-modal').classList.remove('is-open');
    $('#settings-modal').setAttribute('aria-hidden', 'true');
  }

  function showView(name) {
    APP.activeView = name;
    $$('.tab').forEach((tab) => tab.classList.toggle('is-active', tab.dataset.view === name));
    $$('[data-view-panel]').forEach((panel) => panel.classList.toggle('is-active', panel.dataset.viewPanel === name));
    if (name === 'map' && APP.map) {
      setTimeout(() => APP.map.setVisible(true), 50);
    } else if (APP.map) {
      APP.map.setVisible(false);
    }
  }

  /* ── data flow ──────────────────────────────────────────────────────── */
  window.HD2 = {
    receive(payload) {
      if (!APP.booted) { APP.pendingPayloads.push(payload); return; }
      if (!payload) return;
      if (payload.type === 'state' && payload.state) {
        const changed = !APP.state || payload.state.fetched_at !== APP.state.fetched_at;
        APP.state = payload.state;
        if (payload.history) APP.history = payload.history;
        renderAll();
        if (changed && payload.error) toast('数据链中断，显示最近缓存。', 'error');
      } else if (payload.type === 'alerts') {
        if (payload.history) APP.history = payload.history;
        renderIntel();
        enqueueAlerts(payload.alerts || []);
      }
    },
    setMode(mode) {
      applyMode(mode);
    },
    trayAction(action) {
      if (action === 'expand') expandApp();
      else if (action === 'collapse') collapseApp();
      else if (action === 'refresh') refreshData();
      else if (action === 'quit') callApi('quit').catch(() => {});
      else if (typeof action === 'string' && action.indexOf('zorder:') === 0) setZorder(action.slice(7));
    },
    expand() { expandApp(); },
    collapse() { collapseApp(); },
    refresh() { refreshData(); },
    quit() { callApi('quit').catch(() => {}); }
  };

  async function pollState() {
    if (!apiReady()) return;
    try {
      const payload = await callApi('get_state');
      if (!payload) return;
      if (payload.state && (!APP.state || payload.state.fetched_at !== APP.state.fetched_at)) {
        APP.state = payload.state;
        renderAll();
      }
      if (payload.history) APP.history = payload.history;
      if (payload.error !== undefined) APP.lastError = payload.error;
    } catch (err) { /* bridge can be transiently busy */ }
  }

  async function refreshData() {
    if (!apiReady() || APP.refreshBusy) return;
    APP.refreshBusy = true;
    const btn = $('#refresh-btn');
    btn.disabled = true;
    btn.textContent = '↻ 同步中…';
    try {
      await callApi('refresh');
      toast('正在请求最高指挥部数据……');
      setTimeout(async () => {
        await pollState();
        APP.refreshBusy = false;
        btn.disabled = false;
        btn.innerHTML = '<span>↻</span> 刷新数据';
      }, 2600);
    } catch (err) {
      APP.refreshBusy = false;
      btn.disabled = false;
      btn.innerHTML = '<span>↻</span> 刷新数据';
      toast('刷新请求失败。', 'error');
    }
  }

  /* ── render ─────────────────────────────────────────────────────────── */
  function renderAll() {
    if (!APP.state) return;
    renderShell();
    renderRail();
    renderMajorOrder();
    renderEvents();
    renderDispatches();
    renderIntel();
    renderTicker();
    renderClockMeta();
    renderWidget();
    if (APP.map) APP.map.setData(APP.state);
    if (APP.selectedPlanet) {
      const fresh = (APP.state.planets || []).find((p) => p.index === APP.selectedPlanet.index);
      if (fresh) { APP.selectedPlanet = fresh; renderDossier(fresh); }
    } else {
      renderDossierOverview();
    }
    updateCountdowns();
  }

  function renderShell() {
    const state = APP.state;
    const source = state.source || {};
    const status = source.status || 'OFFLINE';
    const dot = $('#uplink-dot');
    dot.className = 'dot ' + (status === 'LIVE' ? 'is-live' : (status === 'CACHED' || status === 'ARCHIVE') ? 'is-warn' : 'is-off');
    $('#source-name').textContent = (source.name || 'OFFLINE').replace(/\s*\/\/\s*CACHE$/, '');
    $('#last-sync').textContent = localTime(state.fetched_at);
    const endpoint = source.endpoint || '';
    $('#uplink').title = [
      `数据源: ${source.name || 'UNKNOWN'}`,
      endpoint ? `主端点: ${endpoint}` : '',
      source.secondary ? `辅助源: ${source.secondary}` : '',
      source.official_endpoint ? `官方端点: ${source.official_endpoint}` : '',
      source.latency_ms ? `延迟: ${source.latency_ms} ms` : '',
      source.error ? `错误: ${source.error}` : ''
    ].filter(Boolean).join('\n');
    const error = source.error || APP.lastError;
    $('#error-label').textContent = error ? 'UPLINK ERROR' : '';
    $('#error-label').title = error || '';
  }

  function renderRail() {
    const war = APP.state.war || {};
    const stats = APP.state.stats || {};
    $('#stat-players').textContent = compact(war.players || 0);
    $('#stat-planets').textContent = `${war.human_planets || 0}/${war.total_planets || 0}`;
    $('#stat-events').textContent = String(war.active_events || 0).padStart(2, '0');
    $('#stat-kills').textContent = compact(stats.kills || war.kills || 0);
    $('#stat-missions').textContent = compact(stats.missions_won || war.missions_won || 0);
    const integrity = war.total_planets ? (war.human_planets / war.total_planets) * 100 : 0;
    $('#stat-integrity').textContent = `${integrity.toFixed(1)}%`;
    $('#stat-integrity-bar').style.width = `${integrity.toFixed(1)}%`;
    $('#stat-war').textContent = war.id || APP.state.source.war_id || '801';
    $('#stat-accuracy').textContent = `${stats.accuracy || war.accuracy || 0}%`;
    $('#stat-impact').textContent = Number(stats.impact || war.impact || 0).toFixed(3);
    $('#poll-label').textContent = `AUTO REFRESH ${APP.settings.poll_seconds || 60}S`;
    $('#version-label').textContent = `V${APP.version}`;
  }

  function renderWatchStatus() {
    if (!APP.state) return;
    const source = APP.state.source || {};
    const war = APP.state.war || {};
    const status = source.status || 'OFFLINE';
    const dot = $('#w-dot');
    if (dot) dot.className = 'dot ' + (status === 'LIVE' ? 'is-live' : (status === 'CACHED' || status === 'ARCHIVE') ? 'is-warn' : 'is-off');
    const sourceEl = $('#w-source');
    if (sourceEl) sourceEl.textContent = status;
    const players = $('#w-players');
    if (players) players.textContent = compact(war.players || 0);
  }

  function setWidgetTab(name, persist) {
    const tab = name === 'mo' ? 'mo' : 'clock';
    APP.widgetTab = tab;
    $$('.widget-tab').forEach((el) => el.classList.toggle('is-active', el.dataset.wtab === tab));
    $$('.widget-pane').forEach((el) => el.classList.toggle('is-active', el.dataset.wpane === tab));
    if (persist) {
      try { localStorage.setItem('hd2.widgetTab', tab); } catch (err) { /* storage may be unavailable */ }
    }
  }

  function renderWidget() {
    if (!APP.state) return;
    renderWatchStatus();
    const mo = APP.state.major_order;
    const title = $('#w-mo-title');
    if (!mo) {
      title.textContent = '等待最高指挥部指令';
      $('#w-mo-time').textContent = '--:--:--';
      $('#w-mo-bar').style.width = '0%';
      $('#w-mo-objective').textContent = 'NO ACTIVE ORDER';
      $('#w-mo-progress').textContent = '0%';
    } else {
      title.textContent = mo.title || 'MAJOR ORDER';
      $('#w-mo-time').textContent = duration(remaining(mo.expires_at, mo.remaining_seconds));
      $('#w-mo-bar').style.width = `${clamp(mo.progress || 0, 0, 100)}%`;
      const pending = (mo.objectives || []).find((o) => !o.done);
      $('#w-mo-objective').textContent = pending ? pending.label : 'ALL OBJECTIVES COMPLETE';
      $('#w-mo-progress').textContent = `${Math.round(mo.progress || 0)}%`;
    }
    const eventCountEl = $('#w-events');
    if (eventCountEl) {
      const events = APP.state.events || [];
      const first = events[0];
      eventCountEl.textContent = first
        ? `${String(events.length).padStart(2, '0')} ACTIVE EVENTS // ${first.planet_name || ''} ${Number(first.progress || 0).toFixed(0)}%`
        : '00 ACTIVE EVENTS // FRONT QUIET';
    }
  }

  function showWidgetAlert(alert) {
    if (!alert) return;
    APP.widgetAlert = alert;
    const title = $('#w-alert-title');
    if (title) title.textContent = alert.title || 'PRIORITY ALERT';
    document.body.classList.add('has-widget-alert');
    clearTimeout(APP._widgetAlertTimer);
    APP._widgetAlertTimer = setTimeout(clearWidgetAlert, 14000);
  }

  function clearWidgetAlert() {
    APP.widgetAlert = null;
    document.body.classList.remove('has-widget-alert');
  }

  function renderMajorOrder() {
    const mo = APP.state.major_order;
    const card = $('#mo-card');
    if (!mo) {
      $('#mo-title').textContent = '等待最高指挥部指令';
      $('#mo-id').textContent = 'MO-000000';
      $('#mo-reward').textContent = '--';
      $('#mo-deadline').textContent = '--:--:--';
      $('#mo-status').textContent = 'UPLINK PENDING';
      $('#mo-status').className = 'mo-status is-offline';
      $('#mo-brief').textContent = '正在建立与超级地球最高指挥部的数据链。请稍候。';
      $('#mo-objectives').innerHTML = '<div class="empty-state">NO ACTIVE MAJOR ORDER</div>';
      $('#mo-progress').textContent = '0%';
      $('#mo-ring').style.setProperty('--p', 0);
      $('#mo-countdown').textContent = '--:--:--';
      card.classList.remove('is-critical');
      return;
    }
    const exp = mo.expires_at;
    const seconds = remaining(exp, mo.remaining_seconds);
    const title = mo.title || 'MAJOR ORDER';
    $('#mo-title').textContent = title;
    $('#mo-id').textContent = `MO-${String(mo.id || 0).slice(-10)}`;
    $('#mo-reward').textContent = `${mo.reward && mo.reward.amount ? mo.reward.amount : 0} ${(mo.reward && mo.reward.type) || 'MEDALS'}`;
    $('#mo-deadline').textContent = exp ? localTime(exp, true) : '--';
    $('#mo-status').textContent = seconds > 0 ? 'ORDER ACTIVE' : 'ORDER EXPIRED';
    $('#mo-status').className = 'mo-status ' + (seconds > 0 ? 'is-live' : 'is-offline');
    $('#mo-brief').textContent = mo.briefing || '最高指挥部未提供额外简报。执行你的命令。';
    $('#mo-progress').textContent = `${Math.round(mo.progress || 0)}%`;
    $('#mo-ring').style.setProperty('--p', clamp(mo.progress || 0, 0, 100).toFixed(1));
    const objectives = mo.objectives || [];
    $('#mo-objectives').innerHTML = objectives.length ? objectives.map((o) => `
      <div class="objective ${o.done ? 'is-done' : ''}">
        <div class="objective-top"><span>${esc(o.type_en || 'OBJECTIVE')}</span><span>${o.done ? 'COMPLETE' : 'PENDING'}</span></div>
        <div class="objective-name" title="${esc(o.label)}">${esc(o.label || '战术目标')}</div>
        <div class="objective-sub">${o.planet_name ? esc(o.planet_name) : 'GALACTIC OBJECTIVE'} // ${o.current}/${o.goal}</div>
      </div>
    `).join('') : '<div class="empty-state">NO OBJECTIVE DATA</div>';
    card.classList.toggle('is-critical', seconds > 0 && seconds < 12 * 3600);
    $('#mo-countdown').dataset.expires = exp || '';
    $('#mo-countdown').dataset.fallback = mo.remaining_seconds || 0;
    $('#mo-countdown').textContent = duration(seconds);
  }

  function renderEvents() {
    const events = (APP.state.events || []).slice().sort((a, b) => remaining(a.expires_at, a.remaining_seconds) - remaining(b.expires_at, b.remaining_seconds));
    $('#event-count').textContent = events.length;
    const list = $('#event-list');
    if (!events.length) {
      list.innerHTML = '<div class="empty-state">NO ACTIVE PLANETARY EVENTS // 银河战线暂时平静</div>';
      return;
    }
    list.innerHTML = events.map((e) => {
      const planet = (APP.state.planets || []).find((p) => p.index === e.planet_index) || {};
      const color = (e.faction && e.faction.key === 'humans') ? COLORS.humans : COLORS[(e.faction && e.faction.key) || 'unknown'];
      const name = e.planet_name || planet.name || `PLANET ${e.planet_index}`;
      const zh = e.planet_name_zh || planet.name_zh || '';
      return `
        <article class="event-card" data-planet="${e.planet_index}" style="--ev-color:${color}">
          <div class="event-top">
            <div>
              <div class="event-name">${esc(name)}<small>${esc(zh || planet.sector || '')} // ${esc((e.faction && e.faction.en) || 'HOSTILE')}</small></div>
            </div>
            <div class="event-tag ${e.is_defense ? 'defense' : 'liberation'}">${e.is_defense ? 'DEFENSE' : 'LIBERATION'}</div>
          </div>
          <div class="event-progress">
            <div class="meter"><i style="width:${clamp(e.progress || 0, 0, 100)}%"></i></div>
            <b>${Number(e.progress || 0).toFixed(0)}%</b>
          </div>
          <div class="event-stats">
            <span>HELLDIVERS <b>${num.format(e.players || planet.players || 0)}</b></span>
            <span>EVENT HP <b>${compact(e.health || 0)} / ${compact(e.max_health || 0)}</b></span>
            <span class="event-timer" data-expires="${esc(e.expires_at || '')}">${esc(e.remaining || '--')}</span>
          </div>
        </article>`;
    }).join('');
    $$('.event-card', list).forEach((card) => card.addEventListener('click', () => {
      const index = Number(card.dataset.planet);
      showView('map');
      setTimeout(() => {
        if (APP.map) {
          APP.map.select(index, false);
          const planet = (APP.state.planets || []).find((p) => p.index === index);
          if (planet) { APP.selectedPlanet = planet; renderDossier(planet); }
          APP.map.fit();
        }
      }, 80);
    }));
  }

  function renderDispatches() {
    const news = (APP.state.news || []).slice(0, 5);
    const list = $('#dispatch-list');
    if (!news.length) {
      list.innerHTML = '<div class="empty-state">NO TRANSMISSIONS RECEIVED</div>';
      return;
    }
    list.innerHTML = news.map((n, index) => `
      <article class="dispatch">
        <div class="dispatch-meta"><span>TRANSMISSION ${String(n.id || 0).padStart(4, '0')}</span><span>${esc(localTime(n.published_at, true))}</span></div>
        <div class="dispatch-text">${esc(n.message || '')}</div>
      </article>`).join('');
  }

  function renderTicker() {
    const mo = APP.state.major_order || {};
    const ge = (APP.state.global_events || []).find((g) => g.flag === 1) || (APP.state.global_events || [])[0] || {};
    const parts = [];
    if (mo.title) parts.push(`MAJOR ORDER // ${mo.title}`);
    if (ge.title && ge.title !== mo.title) parts.push(`GALACTIC EVENT // ${ge.title}`);
    if (APP.state.war && APP.state.war.players) parts.push(`${num.format(APP.state.war.players)} HELLDIVERS DEPLOYED`);
    if (APP.state.events && APP.state.events.length) parts.push(`${APP.state.events.length} ACTIVE PLANETARY EVENTS`);
    parts.push('MINISTRY OF DEFENSE ADVISES: TRUST THE UPLINK. REPORT ALL ANOMALIES.');
    $('#ticker-track').textContent = parts.join('   ///   ');
    $('#ticker-code').textContent = `SE-NET / ${String(APP.state.war && APP.state.war.id || 801).slice(-3)}`;
  }

  function renderIntel() {
    const news = APP.state ? (APP.state.news || []) : [];
    $('#news-count').textContent = `${news.length} RECORDS`;
    $('#news-list').innerHTML = news.map((n) => `
      <article class="news-item">
        <div class="news-meta"><b>TRANSMISSION ${String(n.id || 0).padStart(4, '0')}</b><span>${esc(localTime(n.published_at, true))}</span></div>
        <div class="news-text">${esc(n.message || '')}</div>
      </article>
    `).join('') || '<div class="empty-state">ARCHIVE EMPTY</div>';

    const history = (APP.history || []).slice(0, 30);
    $('#alert-count').textContent = history.length;
    $('#alert-log').innerHTML = history.map((a) => `
      <div class="alert-log-item sev-${esc(a.severity || 'medium')}">
        <div class="al-top"><span>${esc(a.kind || 'ALERT')}</span><span>${esc(localTime(a.timestamp, true))}</span></div>
        <div class="al-title">${esc(a.title || '')}</div>
        <div class="al-body">${esc(a.body || '')}</div>
      </div>
    `).join('') || '<div class="empty-state">NO ALERTS LOGGED</div>';

    const globals = (APP.state && APP.state.global_events) || [];
    $('#global-list').innerHTML = globals.map((g) => `
      <article class="global-item">
        <div class="gi-title">${esc(g.title || 'GALACTIC EVENT')}</div>
        <div class="gi-body">${esc(g.message || '')}</div>
        <div class="gi-meta"><span>${g.flag === 1 ? 'GRAND OFFENSIVE' : 'GALACTIC DEVELOPMENT'}</span><span>${esc(g.remaining || '--')}</span></div>
      </article>
    `).join('') || '<div class="empty-state">NO GLOBAL DEVELOPMENTS</div>';
  }

  function renderDossierOverview() {
    const box = $('#planet-dossier');
    const state = APP.state || {};
    const planets = state.planets || [];
    const war = state.war || {};
    const counts = { humans: 0, terminids: 0, automaton: 0, illuminate: 0, unknown: 0 };
    const players = { humans: 0, terminids: 0, automaton: 0, illuminate: 0, unknown: 0 };
    planets.forEach((p) => {
      const key = (p.owner && p.owner.key) || 'unknown';
      counts[key] = (counts[key] || 0) + 1;
      players[key] = (players[key] || 0) + Number(p.players || 0);
    });
    const dss = state.dss || {};
    const events = (state.events || []).slice(0, 3);
    const total = planets.length || 1;
    const factionRows = [
      ['humans', '超级地球', counts.humans, players.humans],
      ['terminids', '终结族', counts.terminids, players.terminids],
      ['automaton', '机器人', counts.automaton, players.automaton],
      ['illuminate', '光能者', counts.illuminate, players.illuminate]
    ].map(([key, label, count, online]) => `
      <div class="overview-faction">
        <div class="of-line"><span><i class="owner-dot" style="background:${COLORS[key]};color:${COLORS[key]}"></i>${label}</span><b>${count} <small>PLANETS</small></b></div>
        <div class="dossier-bar"><i style="width:${(count / total * 100).toFixed(1)}%;background:${COLORS[key]}"></i></div>
        <div class="of-sub">${num.format(online)} HELLDIVERS</div>
      </div>`).join('');
    const threatRows = events.length ? events.map((e) => `
      <div class="overview-threat">
        <span class="ot-name">${esc(e.planet_name || '')}</span>
        <span class="ot-pct" style="color:${e.is_defense ? '#ff6a3d' : '#ffe81a'}">${Number(e.progress || 0).toFixed(0)}%</span>
        <span class="ot-tag">${e.is_defense ? 'DEFENSE' : 'LIBERATION'}</span>
      </div>`).join('') : '<div class="overview-hint">NO ACTIVE PLANETARY EVENTS</div>';
    box.innerHTML = `
      <div class="dossier-head">
        <div class="dossier-kicker"><span>GRAND MAP // 银河总览</span><b>SE-NET</b></div>
        <div class="dossier-name">银河战争态势</div>
        <div class="dossier-zh">GALACTIC WAR OVERVIEW</div>
      </div>
      <div class="dossier-body">
        <div class="overview-numbers">
          <div><b>${war.human_planets || 0}<small>/${war.total_planets || total}</small></b><span>PLANETS HELD</span></div>
          <div><b class="warn">${war.active_events || 0}</b><span>ACTIVE EVENTS</span></div>
          <div><b>${compact(war.players || 0)}</b><span>DEPLOYED</span></div>
        </div>
        <div class="dossier-section">
          <div class="dossier-section-title">FACTION CONTROL</div>
          ${factionRows}
        </div>
        <div class="dossier-section">
          <div class="dossier-section-title">THREAT BOARD // 活跃战线</div>
          ${threatRows}
        </div>
        ${dss.planet_name ? `<div class="dossier-section"><div class="dossier-section-title">DEMOCRACY SPACE STATION</div><div class="overview-dss">ORBITING <b>${esc(dss.planet_name)}</b></div></div>` : ''}
        <div class="overview-hint">点击任意星球查看战术档案 / SELECT A PLANET FOR DOSSIER</div>
      </div>`;
  }

  function renderDossier(planet) {
    const box = $('#planet-dossier');
    if (!planet) {
      renderDossierOverview();
      return;
    }
    const color = ownerColor(planet);
    const owner = planet.owner || {};
    const event = planet.event;
    const links = (planet.waypoints || []).slice(0, 7).map((i) => {
      const p = (APP.state.planets || []).find((x) => x.index === i);
      return p ? `<button class="dossier-link" data-select="${p.index}">${esc(p.name)}</button>` : '';
    }).join('');
    box.innerHTML = `
      <button class="dossier-close" id="dossier-close" title="关闭">×</button>
      <div class="dossier-head">
        <div class="dossier-kicker"><span>SECTOR ${esc(planet.sector || 'UNKNOWN')}</span><b>#${String(planet.index).padStart(3, '0')}</b></div>
        <div class="dossier-name">${esc(planet.name)}</div>
        <div class="dossier-zh">${esc(planet.name_zh || '')} ${planet.has_dss ? ' // DSS ORBIT' : ''}</div>
      </div>
      <div class="dossier-body">
        <div class="dossier-owner"><i class="owner-dot" style="background:${color};color:${color}"></i>${esc(owner.zh || '未知')} <span class="dim">// ${esc(owner.en || 'UNKNOWN')}</span></div>
        <div class="dossier-row"><span>BIOME</span><b>${esc(planet.biome || 'UNKNOWN')}</b></div>
        <div class="dossier-row"><span>HELLDIVERS</span><b>${num.format(planet.players || 0)}</b></div>
        <div class="dossier-row"><span>${event ? (event.is_defense ? 'DEFENSE' : 'LIBERATION') : 'CONTROL'}</span><b>${Number(planet.liberation || 0).toFixed(1)}%</b></div>
        <div class="dossier-row"><span>PLANET HP</span><b>${compact(planet.health || 0)} / ${compact(planet.max_health || 0)}</b></div>
        <div class="dossier-row"><span>REGEN / SEC</span><b>${Number(planet.regen || 0).toFixed(2)}</b></div>
        ${event ? `
          <div class="dossier-event">
            <div class="de-title">${event.is_defense ? 'PLANETARY DEFENSE' : 'LIBERATION ASSAULT'}</div>
            <div class="de-sub">${esc((event.faction && event.faction.zh) || '')} // ${esc(event.remaining || '--')} REMAINING<br>EVENT HP ${compact(event.health || 0)} / ${compact(event.max_health || 0)}</div>
            <div class="dossier-bar" style="margin-top:7px"><i style="width:${clamp(event.progress || 0, 0, 100)}%"></i></div>
          </div>` : ''}
        ${planet.environmentals && planet.environmentals.length ? `<div class="dossier-section"><div class="dossier-section-title">ENVIRONMENTAL HAZARDS</div><div class="dossier-links">${planet.environmentals.map((h) => `<span class="dossier-link">${esc(h)}</span>`).join('')}</div></div>` : ''}
        ${links ? `<div class="dossier-section"><div class="dossier-section-title">SUPPLY LINES</div><div class="dossier-links">${links}</div></div>` : ''}
      </div>`;
    $('#dossier-close').addEventListener('click', () => {
      APP.selectedPlanet = null;
      if (APP.map) APP.map.selected = null;
      renderDossierOverview();
    });
    $$('.dossier-link[data-select]', box).forEach((btn) => btn.addEventListener('click', () => {
      const p = (APP.state.planets || []).find((x) => x.index === Number(btn.dataset.select));
      if (p) { APP.selectedPlanet = p; APP.map.select(p.index, false); renderDossier(p); }
    }));
  }

  /* ── settings ───────────────────────────────────────────────────────── */
  function applySettings() {
    const alerts = !!APP.settings.alerts_enabled;
    const focus = !!APP.settings.alert_focus;
    setSwitch($('#alerts-switch'), alerts);
    setSwitch($('#focus-switch'), focus);
    setSwitch($('#autostart-switch'), APP.autostart);
    applyZorder(APP.settings.window_zorder);
    const opacity = clamp(Number(APP.settings.window_opacity) || 100, 35, 100);
    const range = $('#opacity-range');
    if (range) range.value = String(opacity);
    $('#opacity-value').textContent = `${opacity}%`;
    $('#poll-label').textContent = `AUTO REFRESH ${APP.settings.poll_seconds || 60}S`;
    document.body.classList.toggle('reduce-motion', !!APP.settings.reduced_motion);
  }

  const ZORDER_LABELS = { bottom: '置底', normal: '通常', top: '置顶' };

  function applyZorder(value) {
    const zorder = value === 'bottom' || value === 'normal' ? value : 'top';
    $$('#setting-zorder .seg').forEach((btn) => {
      const on = btn.dataset.zorder === zorder;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-checked', String(on));
    });
  }

  async function setZorder(value) {
    const zorder = ZORDER_LABELS[value] ? value : 'top';
    const previous = APP.settings.window_zorder;
    if (zorder === previous) return;
    APP.settings.window_zorder = zorder;
    APP.settings.always_on_top = zorder === 'top';
    applySettings();
    try {
      const result = await callApi('set_setting', 'window_zorder', zorder);
      if (!result || !result.ok) throw new Error((result && result.error) || 'failed');
      toast(`窗口层级：${ZORDER_LABELS[zorder]}。`);
    } catch (err) {
      APP.settings.window_zorder = previous;
      APP.settings.always_on_top = previous === 'top';
      applySettings();
      toast('窗口层级设置失败。', 'error');
    }
  }

  function setSwitch(el, on) {
    el.classList.toggle('is-on', !!on);
    el.setAttribute('aria-checked', String(!!on));
    const span = el.querySelector('span');
    if (span) span.textContent = on ? 'ON' : 'OFF';
  }

  async function toggleAutostart() {
    const el = $('#autostart-switch');
    el.disabled = true;
    try {
      const result = await callApi('set_autostart', !APP.autostart);
      if (result && result.ok) {
        APP.autostart = !!result.enabled;
        setSwitch(el, APP.autostart);
        toast(APP.autostart ? '已加入开机启动项。' : '已移除开机启动项。');
      } else {
        toast((result && result.error) || '开机自启设置失败。', 'error');
      }
    } catch (err) {
      toast('无法修改开机自启设置。', 'error');
    } finally {
      el.disabled = false;
    }
  }

  async function toggleSetting(key, value, label) {
    const previous = APP.settings[key];
    APP.settings[key] = value;
    applySettings();
    try {
      const result = await callApi('set_setting', key, value);
      if (!result || !result.ok) throw new Error((result && result.error) || 'failed');
      toast(`${label}已${value ? '开启' : '关闭'}。`);
    } catch (err) {
      APP.settings[key] = previous;
      applySettings();
      toast(`${label}设置失败。`, 'error');
    }
  }

  async function createShortcut() {
    try {
      const result = await callApi('create_desktop_shortcut');
      toast(result && result.ok ? '桌面快捷方式已创建。' : '快捷方式创建失败。', result && result.ok ? '' : 'error');
    } catch (err) {
      toast('快捷方式创建失败。', 'error');
    }
  }

  /* ── alerts ─────────────────────────────────────────────────────────── */
  function enqueueAlerts(alerts) {
    alerts.forEach((alert) => {
      if (!alert || APP.seenAlertIds.has(alert.id)) return;
      APP.seenAlertIds.add(alert.id);
      APP.alertQueue.push(alert);
    });
    if (!APP.alertOpen) showNextAlert();
  }

  function showNextAlert() {
    const alert = APP.alertQueue.shift();
    if (!alert) { APP.alertOpen = false; return; }
    APP.alertOpen = true;
    const severity = alert.severity || 'high';
    const el = document.createElement('div');
    el.className = `alert-banner ${severity === 'high' ? 'is-high' : severity === 'medium' ? 'is-medium' : ''}`;
    el.innerHTML = `
      <div class="alert-hazard"></div>
      <div class="alert-inner">
        <div class="alert-top"><span class="alert-kicker">PRIORITY TRANSMISSION // 优先通讯</span><span class="alert-time">${esc(localTime(alert.timestamp))}</span></div>
        <div class="alert-title">${esc(alert.title || 'PRIORITY ALERT')}</div>
        <div class="alert-body">${esc(alert.body || '')}</div>
        <div class="alert-actions"><button class="alert-ack">ACKNOWLEDGE // 确认</button><span class="alert-code">AUTH 7-ALPHA / ${esc(alert.kind || 'ALERT')}</span></div>
      </div>
      <div class="alert-timer"></div>`;
    $('#alert-layer').appendChild(el);
    showWidgetAlert(alert);
    document.body.classList.add('is-alerting');
    setTimeout(() => document.body.classList.remove('is-alerting'), 1000);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      clearWidgetAlert();
      el.style.opacity = '0';
      el.style.transform = 'translateX(24px)';
      setTimeout(() => {
        el.remove();
        APP.alertOpen = false;
        showNextAlert();
      }, 230);
    };
    el.querySelector('.alert-ack').addEventListener('click', () => {
      if (apiReady()) callApi('acknowledge', alert.id).catch(() => {});
      close();
    });
    const timer = setTimeout(close, severity === 'critical' ? 22000 : 16000);
  }

  function closeAlert() {
    const el = $('#alert-layer .alert-banner');
    if (el) {
      const btn = el.querySelector('.alert-ack');
      if (btn) btn.click();
    }
  }

  /* ── countdowns ─────────────────────────────────────────────────────── */
  function tickCountdowns() {
    const mo = APP.state && APP.state.major_order;
    if (mo) {
      const seconds = remaining(mo.expires_at, mo.remaining_seconds);
      const cd = $('#mo-countdown');
      if (cd) cd.textContent = duration(seconds);
      const wcd = $('#w-mo-time');
      if (wcd) wcd.textContent = duration(seconds);
    }
    $$('[data-expires]').forEach((el) => {
      const expires = el.dataset.expires;
      if (!expires) return;
      const seconds = remaining(expires, 0);
      el.textContent = duration(seconds);
    });
  }

  function updateCountdowns() { tickCountdowns(); }

  window.addEventListener('pywebviewready', boot);
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(() => { if (!APP.booted && apiReady()) boot(); }, 50);
  }
  window.addEventListener('DOMContentLoaded', () => {
    window.__HD2_BOOT_TS = Date.now();
    setTimeout(() => { if (!APP.booted) boot(); }, 200);
  });
})();
