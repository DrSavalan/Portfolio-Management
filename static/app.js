const $ = (id) => document.getElementById(id);
const fmt = (n, digits = 2) => Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const pct = (n) => `${n >= 0 ? '+' : ''}${fmt(n * 100, 2)}%`;
const pctPlain = (n) => `${fmt(n * 100, 1)}%`;
const number = (n) => Number(n || 0).toLocaleString('en-US');
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const time = (date) => new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit' }).format(date);
let nextRefresh = 0;
let loaded = false;
const objectives = {
  duration: ['Stable asset allocation optimized for shorter drawdown duration.', 'minimizes drawdown duration'],
  drawdown: ['Stable asset allocation optimized for a smaller maximum drawdown.', 'minimizes maximum drawdown'],
  return: ['Stable asset allocation optimized for higher historical return.', 'maximizes in-sample return'],
  sharpe: ['Stable asset allocation optimized for a higher Sharpe ratio.', 'maximizes the Sharpe ratio'],
};
const selectedObjective = $('objective-select');
const selectedProfile = $('profile-select');
selectedProfile.value = ['short', 'medium', 'long'].includes(localStorage.getItem('portfolio-profile')) ? localStorage.getItem('portfolio-profile') : 'short';
const horizonNames = { short: '~3 months', medium: '1 year', long: '~4 years' };
function describeHorizon() { setText('horizon-label', `· ${horizonNames[selectedProfile.value]}`); }
describeHorizon();
function describeObjective() {
  const [summary, method] = objectives[selectedObjective.value];
  setText('objective-description', summary);
  setText('method-description', `The optimization ${method} and constrains allocation changes to limit overfitting. Portfolio-level analysis is generally less exposed to overfitting than a single-instrument backtest, but cannot eliminate it.`);
}

function setText(id, value) { const el = $(id); if (el) el.textContent = value; }
function setHidden(id, hidden) { const el = $(id); if (el) el.hidden = hidden; }

function formatDuration(days) {
  if (days < 1) return `${Math.max(1, Math.round(days * 24))} hours`;
  if (days < 60) return `${fmt(days, 1)} days`;
  return `${fmt(days / 30.4375, 1)} months`;
}

function timeframeLabel(timeframe) {
  return timeframe === 'H4' ? '4-hour' : timeframe === 'H1' ? '1-hour' : '15-minute';
}

function renderAllocation(data) {
  const rows = data.symbols || [];
  const total = rows.reduce((sum, item) => sum + item.weight, 0) || 1;
  let angle = 0;
  const colors = rows.map((item, index) => {
    const start = angle;
    angle = index === rows.length - 1 ? 360 : angle + item.weight / total * 360;
    return `${item.color} ${start.toFixed(2)}deg ${angle.toFixed(2)}deg`;
  });
  if (colors.length) $('donut-chart').style.background = `conic-gradient(${colors.join(',')})`;
  $('allocation-list').innerHTML = rows.map(item => `<div class="allocation-row"><span class="asset-emoji">${item.emoji}</span><span class="asset-name">${item.label}</span><span class="asset-bar"><span style="width:${(item.weight * 100).toFixed(1)}%;background:${item.color}"></span></span><strong class="asset-percent">${fmt(item.weight * 100, 1)}%</strong></div>`).join('') || '<div class="loading">No allocation data available.</div>';
  $('asset-count').textContent = `${number(rows.length)} assets`;
  document.querySelector('.donut-inner small').textContent = `${number(rows.length)} assets`;
}

function renderRebalance(data) {
  setText('rebalance-total', pctPlain(data.turnover || 0));
  const rows = (data.rebalance || []).map(item => {
    const action = item.action === 'buy' ? 'Increase' : item.action === 'sell' ? 'Reduce' : 'Hold';
    const previous = item.from == null ? '—' : `${fmt(item.from * 100, 1)}%`;
    return `<div class="rebalance-row"><div class="rebalance-asset"><span>${item.emoji}</span><span>${item.label}</span></div><span class="num">${previous}</span><span class="num">${fmt(item.to * 100, 1)}%</span><span class="action ${item.action}">${action}</span></div>`;
  }).join('');
  $('rebalance-rows').innerHTML = rows || '<div class="loading">No material rebalance required.</div>';
}

function renderChart(values) {
  if (!values || values.length < 2) return;
  const width = 800, height = 190, padding = 3;
  const min = Math.min(...values), max = Math.max(...values), spread = max - min || 1;
  const points = values.map((value, index) => [(index / (values.length - 1)) * width, padding + (1 - (value - min) / spread) * (height - padding * 2)]);
  const line = points.map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(' ');
  $('chart-line').setAttribute('d', line); $('chart-area').setAttribute('d', `${line} L${width},${height} L0,${height} Z`);
  setText('axis-top', fmt(max, 2)); setText('axis-mid', fmt((max + min) / 2, 2)); setText('axis-bottom', fmt(min, 2));
}

function renderProgress(events, busy) {
  const latest = new Map();
  (events || []).forEach(event => latest.set(event.key, event));
  setText('download-status', busy ? 'Processing' : Array.from(latest.values()).some(event => event.status === 'error' || event.status === 'missing') ? 'Needs attention' : latest.size ? 'Completed' : 'Waiting');
  const icons = { loading: '◌', done: '✓', missing: '!', error: '×' };
  $('download-steps').innerHTML = Array.from(latest.values()).map(event => `<div class="download-step ${event.status}"><span class="download-step-icon">${icons[event.status] || '•'}</span><div class="download-step-text"><div class="download-step-label">${escapeHtml(event.label)}</div><div class="download-step-detail">${escapeHtml(event.detail)}</div></div></div>`).join('') || '<div class="loading">Waiting to download market data…</div>';
}

function render(data) {
  renderProgress(data.progress, data.busy);
  if (!data.ready) {
    if (!data.busy) setText('objective-status', '');
    setHidden('missing-notice', data.busy || !data.error);
    const downloadFailed = data.source === 'unavailable' || !data.source;
    $('source-badge').textContent = data.busy ? 'PROCESSING MT5 DATA' : downloadFailed ? 'MT5 DISCONNECTED' : 'MT5 DATA SAVED';
    setText('portfolio-title', data.busy ? 'Processing MT5 data…' : downloadFailed ? 'MT5 data unavailable' : 'Portfolio optimization unavailable');
    if (!data.busy && data.error) { $('missing-notice').textContent = `${downloadFailed ? 'MT5 connection failed' : 'Portfolio optimization failed'}: ${data.error}`; setHidden('missing-notice', false); }
    if (loaded) {
      loaded = false;
      ['metric-return','metric-dd','metric-sharpe','metric-recovery','health-return','health-dd','health-turnover','rebalance-total','risk-calmar','risk-sortino','risk-vol','risk-cagr','risk-bars'].forEach(id => setText(id, '—'));
      setText('sample-window', 'Waiting for saved MT5 prices in the selected horizon…');
      setText('return-context', 'Return under the selected objective');
      $('allocation-list').textContent = 'No current MT5 data available.';
      $('donut-chart').style.background = '#25354a';
      $('rebalance-rows').textContent = 'Waiting for a successful download.';
      $('chart-line').setAttribute('d', '');
      $('chart-area').setAttribute('d', '');
    }
    return;
  }
  loaded = true;
  setText('objective-status', data.busy ? 'Updating market data — previous portfolio shown' : '');
  setText('portfolio-title', 'Your optimized portfolio is ready ✧');
  const stats = data.stats;
  const source = data.source === 'mt5-excel' ? 'MT5 EXCEL DATA' : 'MT5 CONNECTED';
  $('source-badge').innerHTML = `<span class="pulse-dot"></span> ${source}`;
  $('cycle-badge').textContent = `CYCLE ${number(data.cycle)}`;
  setText('last-update', time(new Date(data.generated_at * 1000)));
  setText('metric-return', pct(stats.total_return));
  setText('return-context', `${data.objective === 'return' ? 'Maximum-return allocation' : 'Selected objective result'} · Max-return reference: ${pct(stats.max_return_benchmark)}`);
  const date = value => new Date(`${value}Z`).toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric'});
  setText('sample-window', `Saved MT5 ${timeframeLabel(data.timeframe)} prices: ${date(data.sample_start)} – ${date(data.sample_end)} · ${number(data.bars)} aligned intervals (not realized P&L)`);
  setText('metric-dd', formatDuration(stats.drawdown_duration_days));
  setText('metric-sharpe', fmt(stats.sharpe, 2));
  setText('metric-recovery', `-${fmt(stats.max_drawdown * 100, 2)}%`);
  setText('health-return', pct(stats.total_return));
  setText('health-return-tag', `${number(data.bars)} ${data.timeframe} INTERVALS`);
  setText('health-dd', formatDuration(stats.drawdown_duration_days));
  setText('health-turnover', pctPlain(data.turnover));
  setText('risk-calmar', fmt(stats.calmar, 2)); setText('risk-sortino', fmt(stats.sortino, 2)); setText('risk-vol', `${fmt(stats.volatility * 100, 2)}%`); setText('risk-cagr', pct(stats.cagr)); setText('risk-bars', number(data.bars));
  renderAllocation(data); renderRebalance(data); renderChart(data.equity_curve);
  const notices = [];
  if (data.missing?.length) notices.push(`Some MT5 symbols have fewer than 2 candles and were excluded: ${data.missing.join(', ')}`);
  if (data.history_limited) notices.push('Available history is shorter than the selected horizon; results use the shared dates shown below.');
  $('missing-notice').textContent = notices.join(' ');
  setHidden('missing-notice', !notices.length);
  nextRefresh = data.next_refresh_at * 1000;
}

async function fetchPortfolio() {
  const objective = selectedObjective.value;
  const profile = selectedProfile.value;
  try {
    const response = await fetch(`/api/portfolio?objective=${encodeURIComponent(objective)}&profile=${encodeURIComponent(profile)}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Analytics request failed');
    const data = await response.json();
    if (objective === selectedObjective.value && profile === selectedProfile.value) render(data);
  } catch {
    if (objective === selectedObjective.value && profile === selectedProfile.value) {
      setText('objective-status', 'Could not load allocation');
      if (!loaded) $('allocation-list').textContent = 'Could not connect to the analytics service.';
    }
  }
}
function tick() { if (nextRefresh) { const seconds = Math.max(0, Math.ceil((nextRefresh - Date.now()) / 1000)); setText('countdown', `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`); if (seconds === 0) fetchPortfolio(); } }
selectedProfile.addEventListener('change', () => {
  localStorage.setItem('portfolio-profile', selectedProfile.value);
  describeHorizon();
  setText('objective-status', 'Downloading selected timeframe…');
  fetchPortfolio();
});

selectedObjective.addEventListener('change', () => {
  localStorage.setItem('portfolio-objective', selectedObjective.value);
  describeObjective();
  setText('objective-status', 'Optimizing saved MT5 data…');
  fetchPortfolio();
});
describeObjective();
setText('today', new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'short', day: 'numeric' }).format(new Date()));
fetchPortfolio(); setInterval(fetchPortfolio, 30000); setInterval(tick, 1000);
