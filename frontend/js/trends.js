/* ═══════════════════════════════════════════════════════════
   trends.js — Screen 4
   ═══════════════════════════════════════════════════════════ */

let _trendsCharts = {};
let _activeTab = 'revenue';

function renderTrends(container) {
  // Compute default dates (start_date = 90 days ago, end_date = today)
  const today = new Date();
  const ninetyDaysAgo = new Date(Date.now() - 90 * 86400000);
  const formatDate = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const defaultStart = formatDate(ninetyDaysAgo);
  const defaultEnd = formatDate(today);

  container.innerHTML = `
    <div class="screen">
      <div class="screen-title" style="margin-bottom:12px;">Analytics</div>

      <!-- Tab Row -->
      <div class="tab-row" style="margin-bottom:12px">
        <button class="tab-btn active" id="tab-revenue" onclick="switchTab('revenue', this)">📈 Revenue</button>
        <button class="tab-btn" id="tab-category" onclick="switchTab('category', this)">🏷️ Categories</button>
        <button class="tab-btn" id="tab-item" onclick="switchTab('item', this)">🔍 Item Deep Dive</button>
      </div>

      <!-- Date Range Selector -->
      <div class="card" style="padding:12px;margin-bottom:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;border:1px solid var(--color-border);background:var(--color-surface-2)">
        <div style="font-size:12px;font-weight:700;color:var(--color-text);letter-spacing:0.05em">FILTER RANGE:</div>
        <div style="display:flex;align-items:center;gap:6px">
          <label style="font-size:11px;color:var(--color-text-dim);font-weight:600">FROM</label>
          <input type="date" id="trends-start-date" class="api-key-input" style="padding:6px 10px;font-size:12px;max-width:130px" value="${defaultStart}">
        </div>
        <div style="display:flex;align-items:center;gap:6px">
          <label style="font-size:11px;color:var(--color-text-dim);font-weight:600">TO</label>
          <input type="date" id="trends-end-date" class="api-key-input" style="padding:6px 10px;font-size:12px;max-width:130px" value="${defaultEnd}">
        </div>
        <button class="btn btn-primary" onclick="applyTrendsFilter()" style="padding:6px 12px;font-size:12px">Apply</button>
        <button class="btn btn-ghost" onclick="resetTrendsFilter('${defaultStart}', '${defaultEnd}')" style="padding:6px 12px;font-size:12px">Reset</button>
      </div>

      <!-- Revenue Tab -->
      <div id="tab-panel-revenue">
        <!-- Summary cards -->
        <div class="stat-row" style="margin-bottom:10px;">
          <div class="stat-card card">
            <div class="stat-label" id="trends-revenue-label">Revenue ${infoTip('totalRevenue45')}</div>
            <div class="stat-value primary" id="total-revenue-val">₹—</div>
          </div>
          <div class="stat-card card">
            <div class="stat-label">Daily Average ${infoTip('dailyAvg')}</div>
            <div class="stat-value" id="avg-daily-rev">₹—</div>
          </div>
        </div>
        <div class="stat-row" style="margin-bottom:10px;">
          <div class="stat-card card">
            <div class="stat-label">Best Day ${infoTip('bestDay')}</div>
            <div class="stat-value" id="best-day-val">₹—</div>
          </div>
          <div class="stat-card card">
            <div class="stat-label">Total Units ${infoTip('totalUnits')}</div>
            <div class="stat-value" id="total-units-val">—</div>
          </div>
        </div>

        <!-- 30-Day trend -->
        <div class="card chart-card">
          <div class="card-title" id="revenue-trend-title">Revenue Trend</div>
          <canvas id="trend-chart-30" height="200"></canvas>
        </div>

        <!-- DOW pattern -->
        <div class="card chart-card">
          <div class="card-title">Day-of-Week Pattern ${infoTip('dowPattern')}</div>
          <canvas id="dow-chart" height="190"></canvas>
        </div>
      </div>

      <!-- Category Tab -->
      <div id="tab-panel-category" style="display:none">
        <div class="card chart-card">
          <div class="card-title">Revenue by Category ${infoTip('catRevenue')}</div>
          <canvas id="cat-revenue-chart" height="280"></canvas>
        </div>
        <div class="card chart-card">
          <div class="card-title">Units by Category</div>
          <canvas id="cat-qty-chart" height="280"></canvas>
        </div>
      </div>

      <!-- Item Tab -->
      <div id="tab-panel-item" style="display:none">
        <div style="margin-bottom:12px;">
          <select id="item-selector" class="item-select" onchange="loadItemTrend()">
            <option value="">Select an item...</option>
          </select>
        </div>
        <div class="card chart-card" id="item-chart-wrap" style="display:none">
          <div class="card-title" id="item-chart-title">Item Trend</div>
          <canvas id="item-trend-chart" height="200"></canvas>
        </div>
        <div id="item-stats-wrap" style="display:none">
          <div class="stat-row">
            <div class="stat-card card">
              <div class="stat-label">Avg / Day</div>
              <div class="stat-value" id="item-avg">—</div>
            </div>
            <div class="stat-card card">
              <div class="stat-label">Peak Day</div>
              <div class="stat-value" id="item-peak">—</div>
            </div>
          </div>
        </div>
        <div class="empty-state" id="item-empty">
          <div class="empty-state-icon">🔍</div>
          Select an item to view its trend
        </div>
      </div>

      <!-- ACCURACY TAB HIDDEN FOR SUBMISSION (restore when ready)
      See loadAccuracyData() function below for full implementation. -->
    </div>
  `;

  loadRevenueTrendsData();
  loadCategoryData();
  // loadAccuracyData(); // HIDDEN FOR SUBMISSION — restore this line to re-enable
  loadItemSelectorOptions();
}

function getSelectedDateRange() {
  const startEl = document.getElementById('trends-start-date');
  const endEl = document.getElementById('trends-end-date');
  if (startEl && endEl && startEl.value && endEl.value) {
    return { startDate: startEl.value, endDate: endEl.value };
  }
  return null;
}

async function applyTrendsFilter() {
  showToast('Applying date filter...', '');
  await loadRevenueTrendsData();
  await loadCategoryData();
  // await loadAccuracyData(); // HIDDEN FOR SUBMISSION — restore this line to re-enable
  const selector = document.getElementById('item-selector');
  if (selector && selector.value) {
    await loadItemTrend();
  }
}

async function resetTrendsFilter(defaultStart, defaultEnd) {
  const startEl = document.getElementById('trends-start-date');
  const endEl = document.getElementById('trends-end-date');
  if (startEl) startEl.value = defaultStart;
  if (endEl) endEl.value = defaultEnd;
  window.memCache = {};
  await applyTrendsFilter();
}

async function loadRevenueTrendsData() {
  const range = getSelectedDateRange() || 90;
  let trendData = await API.getRevenueTrend(range);
  if (trendData && trendData.length) {
    renderTrend30(trendData);
    renderSummaryStats(trendData);
    renderDOWChart(trendData);
  } else {
    // Clear display if no data
    setIf('total-revenue-val', '₹0');
    setIf('avg-daily-rev', '₹0');
    setIf('best-day-val', '₹0');
    setIf('trends-revenue-label', `0-Day Revenue ${infoTip('totalRevenue45')}`);
    destroyChart('trend30');
    destroyChart('dow');
  }
}

function renderSummaryStats(data) {
  const revenues = data.map(d => d.revenue || d.total_revenue || 0);
  const total = revenues.reduce((a, b) => a + b, 0);
  const avg = total / (revenues.length || 1);
  const best = revenues.length ? Math.max(...revenues) : 0;

  const fmtRev = v => `₹${Math.round(v).toLocaleString('en-IN')}`;

  setIf('total-revenue-val', fmtRev(total));
  setIf('avg-daily-rev', fmtRev(avg));
  setIf('best-day-val', fmtRev(best));

  const labelEl = document.getElementById('trends-revenue-label');
  if (labelEl) {
    labelEl.innerHTML = `${data.length}-Day Revenue ${infoTip('totalRevenue45')}`;
  }
}

function renderTrend30(data) {
  const el = document.getElementById('trend-chart-30');
  if (!el) return;
  destroyChart('trend30');

  const labels = data.map(d => {
    const dt = new Date(d.date + 'T00:00:00');
    return dt.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  });
  const values = data.map(d => d.revenue || d.total_revenue || 0);

  // Smart y-axis: pad 5% below min so small changes are visible
  const minVal = Math.min(...values.filter(v => v > 0));
  const maxVal = Math.max(...values);
  const padding = (maxVal - minVal) * 0.15;
  const suggestedMin = Math.max(0, minVal - padding);

  const ctx = el.getContext('2d');
  // Update card title to reflect actual date range
  const titleEl = el.closest('.card-card, .card')?.querySelector('.card-title');
  if (titleEl && data.length > 0) {
    const firstDate = new Date(data[0].date + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    const lastDate  = new Date(data[data.length-1].date + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    titleEl.textContent = `Revenue Trend (${firstDate} – ${lastDate})`;
  }

  _trendsCharts['trend30'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: '#E8531A',
        backgroundColor: createGradient(ctx, '#E8531A'),
        borderWidth: 2.5, fill: true, tension: 0.4,
        pointRadius: data.length > 30 ? 1 : 2,
        pointBackgroundColor: '#E8531A',
      }]
    },
    options: lineOpts('₹', true, suggestedMin)
  });
}

function renderDOWChart(data) {
  const el = document.getElementById('dow-chart');
  if (!el) return;
  destroyChart('dow');

  // Aggregate by day of week
  const DOW_NAMES = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const totals = new Array(7).fill(0);
  const counts = new Array(7).fill(0);
  data.forEach(d => {
    const dow = new Date(d.date + 'T00:00:00').getDay();
    totals[dow] += d.revenue || d.total_revenue || 0;
    counts[dow]++;
  });

  // Reorder Mon → Sun
  const reorder = [1,2,3,4,5,6,0];
  const labels = reorder.map(i => DOW_NAMES[i]);
  const avgs = reorder.map(i => counts[i] > 0 ? totals[i] / counts[i] : 0);
  const maxVal = Math.max(...avgs);
  const colors = avgs.map(v => v === maxVal ? '#E8531A' : (v > maxVal * 0.8 ? '#F97316' : '#2E3450'));

  const ctx = el.getContext('2d');
  _trendsCharts['dow'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: avgs,
        backgroundColor: colors,
        borderRadius: 8, borderSkipped: false,
      }]
    },
    options: {
      ...barOpts(),
      scales: {
        ...barOpts().scales,
        y: { ...barOpts().scales.y, ticks: { ...barOpts().scales.y.ticks, callback: v => `₹${(v/1000).toFixed(0)}k` } }
      }
    }
  });
}

async function loadCategoryData() {
  const range = getSelectedDateRange() || 90;
  const data = await API.getCategoryTrends(range);
  if (!data || !data.length) {
    // Clear graphs if no category data
    destroyChart('catRev');
    destroyChart('catQty');
    setIf('total-units-val', '0');
    return;
  }

  const sorted = [...data].sort((a, b) => b.revenue - a.revenue);
  const CAT_COLORS = {
    biryani:'#F97316', chicken:'#EF4444', beverage:'#06B6D4',
    bread:'#D97706', dairy:'#A78BFA', other:'#8B90B0',
    starter:'#84CC16', rice:'#F59E0B', ice_cream:'#EC4899',
    seafood:'#6366F1', family_pack:'#E8531A', soup:'#14B8A6', egg:'#FBBF24'
  };
  const labels = sorted.map(d => d.category);
  const colors = labels.map(l => CAT_COLORS[l] || '#8B90B0');

  // Revenue chart
  const revEl = document.getElementById('cat-revenue-chart');
  if (revEl) {
    destroyChart('catRev');
    const ctx = revEl.getContext('2d');
    _trendsCharts['catRev'] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{ data: sorted.map(d => d.revenue), backgroundColor: colors, borderRadius: 6 }]
      },
      options: {
        ...barOpts(),
        indexAxis: 'y',
        scales: {
          x: { grid: { color: '#2E3450' }, ticks: { color: '#8B90B0', callback: v => `₹${(v/1000).toFixed(0)}k` } },
          y: { grid: { display: false }, ticks: { color: '#F0F2FF', font: { size: 11 } } }
        }
      }
    });
  }

  // Qty chart
  const qtyEl = document.getElementById('cat-qty-chart');
  if (qtyEl) {
    destroyChart('catQty');
    const qSorted = [...data].sort((a, b) => b.qty - a.qty);
    const qLabels = qSorted.map(d => d.category);
    const qColors = qLabels.map(l => CAT_COLORS[l] || '#8B90B0');
    const ctx = qtyEl.getContext('2d');
    _trendsCharts['catQty'] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: qLabels,
        datasets: [{ data: qSorted.map(d => d.qty), backgroundColor: qColors, borderRadius: 6 }]
      },
      options: {
        ...barOpts(),
        indexAxis: 'y',
        scales: {
          x: { grid: { color: '#2E3450' }, ticks: { color: '#8B90B0' } },
          y: { grid: { display: false }, ticks: { color: '#F0F2FF', font: { size: 11 } } }
        }
      }
    });
  }

  // Total units
  const totalUnits = data.reduce((s, d) => s + (d.qty || 0), 0);
  setIf('total-units-val', totalUnits.toLocaleString('en-IN'));
}

async function loadItemSelectorOptions() {
  const items = await API.getAllItems() || [];
  const sel = document.getElementById('item-selector');
  if (!sel) return;
  items.sort((a, b) => (b.avg_qty || 0) - (a.avg_qty || 0));
  items.forEach(item => {
    const opt = document.createElement('option');
    opt.value = item.item_name;
    opt.textContent = item.item_name;
    sel.appendChild(opt);
  });
}

async function loadItemTrend() {
  const sel = document.getElementById('item-selector');
  const item = sel?.value;
  if (!item) return;

  document.getElementById('item-chart-wrap').style.display = 'none';
  document.getElementById('item-stats-wrap').style.display = 'none';
  document.getElementById('item-empty').style.display = 'block';
  document.getElementById('item-empty').innerHTML = `
    <div class="empty-state-icon">⏳</div>
    Loading trend for "${item}"...`;

  destroyChart('itemTrend');

  // getItemTrend returns { item, days, series: [...] }
  const range = getSelectedDateRange() || 90;
  const resp = await API.getItemTrend(item, range);
  const data = resp?.series || [];

  if (!data.length) {
    const rangeLabel = range.startDate ? `${range.startDate} to ${range.endDate}` : 'selected range';
    document.getElementById('item-empty').innerHTML = `
      <div class="empty-state-icon">📉</div>
      No sales data for "${item}" in the ${rangeLabel}.`;
    return;
  }

  document.getElementById('item-empty').style.display = 'none';
  document.getElementById('item-chart-wrap').style.display = 'block';
  document.getElementById('item-stats-wrap').style.display = 'block';

  const firstDate = new Date(data[0].date + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  const lastDate  = new Date(data[data.length-1].date + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  document.getElementById('item-chart-title').textContent =
    `${item} — ${firstDate} to ${lastDate} (${data.length} days)`;

  const el = document.getElementById('item-trend-chart');
  const ctx = el.getContext('2d');
  const vals = data.map(d => d.qty || d.qty_sold || 0);

  // Smart y-axis
  const minVal = Math.min(...vals.filter(v => v > 0));
  const maxVal = Math.max(...vals);
  const suggestedMin = Math.max(0, minVal - (maxVal - minVal) * 0.15);

  _trendsCharts['itemTrend'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map(d => {
        const dt = new Date(d.date + 'T00:00:00');
        return dt.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
      }),
      datasets: [{
        data: vals,
        borderColor: '#06B6D4', fill: true,
        backgroundColor: createGradient(ctx, '#06B6D4'),
        borderWidth: 2, tension: 0.4,
        pointRadius: data.length > 30 ? 1 : 2,
        pointBackgroundColor: '#06B6D4',
      }]
    },
    options: lineOpts('units', false, suggestedMin)
  });

  const nonZero = vals.filter(v => v > 0);
  const avg = nonZero.length > 0 ? nonZero.reduce((a, b) => a + b, 0) / nonZero.length : 0;
  setIf('item-avg', avg.toFixed(1));
  setIf('item-peak', Math.max(...vals));
}

function switchTab(tab, btn) {
  _activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  ['revenue','category','item'].forEach(t => {  // 'accuracy' hidden for submission
    const el = document.getElementById(`tab-panel-${t}`);
    if (el) el.style.display = t === tab ? 'block' : 'none';
  });
}

// ── Chart helpers ──────────────────────────────────────────────────────────────
function createGradient(ctx, color) {
  return (context) => {
    const chart = context.chart;
    const { ctx: c, chartArea } = chart;
    if (!chartArea) return 'transparent';
    const grad = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    grad.addColorStop(0, color + '40');
    grad.addColorStop(1, color + '00');
    return grad;
  };
}

function lineOpts(unit, currency = false, suggestedMin = undefined) {
  return {
    responsive: true,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#1A1D27', borderColor: '#2E3450', borderWidth: 1,
        titleColor: '#F0F2FF', bodyColor: '#8B90B0',
        callbacks: {
          label: (ctx) => currency
            ? ` ₹${Math.round(ctx.parsed.y).toLocaleString('en-IN')}`
            : ` ${ctx.parsed.y} ${unit}`
        }
      }
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: '#8B90B0', font: { size: 10 }, maxTicksLimit: 8 } },
      y: {
        grid: { color: '#2E3450' },
        // suggestedMin keeps scale tight around data; still allows 0 as hard min for data
        suggestedMin: suggestedMin,
        ticks: {
          color: '#8B90B0', font: { size: 10 },
          callback: currency
            ? (v) => {
                if (v >= 100000) return `₹${(v/100000).toFixed(1)}L`;
                if (v >= 1000)   return `₹${(v/1000).toFixed(0)}k`;
                return `₹${v}`;
              }
            : undefined
        }
      }
    },
    animation: { duration: 700, easing: 'easeOutQuart' }
  };
}

function barOpts() {
  return {
    responsive: true,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#1A1D27', borderColor: '#2E3450', borderWidth: 1,
        titleColor: '#F0F2FF', bodyColor: '#8B90B0',
      }
    },
    scales: {
      x: { grid: { color: '#2E3450' }, ticks: { color: '#8B90B0', font: { size: 11 } } },
      y: { grid: { display: false }, ticks: { color: '#F0F2FF', font: { size: 11 } } }
    },
    animation: { duration: 700, easing: 'easeOutQuart' }
  };
}

function destroyChart(key) {
  if (_trendsCharts[key]) {
    _trendsCharts[key].destroy();
    delete _trendsCharts[key];
  }
}

function setIf(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}
