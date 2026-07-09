/* ═══════════════════════════════════════════════════════════
   app.js — Navigation + App Init + API Status
   ═══════════════════════════════════════════════════════════ */

// ── Screen registry ────────────────────────────────────────────────────────────
const SCREENS = {
  dashboard:       renderDashboard,
  recommendations: renderRecommendations,
  'log-sales':     renderLogSales,
  trends:          renderTrends,
  settings:        renderSettings,
};

// Screens that persist their DOM between visits (not re-rendered each time)
const PERSIST_SCREENS = new Set(['settings']);

let _activeScreen = null;
const _screenCache = {}; // Caches rendered DOM nodes for persistent screens

// ── Navigation ─────────────────────────────────────────────────────────────────
function navigateTo(screenId) {
  if (_activeScreen === screenId) return;

  document.querySelectorAll('.nav-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.screen === screenId)
  );

  const main = document.getElementById('app-main');
  if (!main) return;
  main.scrollTop = 0;

  // Save current screen's DOM if it's a persistent screen
  if (_activeScreen && PERSIST_SCREENS.has(_activeScreen)) {
    _screenCache[_activeScreen] = main.innerHTML;
  }

  _activeScreen = screenId;
  main.innerHTML = '';

  // Restore from cache if available for persistent screens
  if (PERSIST_SCREENS.has(screenId) && _screenCache[screenId]) {
    main.innerHTML = _screenCache[screenId];
    // Re-attach event listeners for buttons (innerHTML doesn't preserve them)
    _reattachSettingsListeners();
    return;
  }

  const renderFn = SCREENS[screenId];
  if (renderFn) renderFn(main);
}

// Re-attach onclick handlers after restoring settings DOM from cache
function _reattachSettingsListeners() {
  const btns = {
    'retrain-btn': retrainModel,
  };
  for (const [id, fn] of Object.entries(btns)) {
    const el = document.getElementById(id);
    if (el) el.onclick = fn;
  }
}

// ── Header ─────────────────────────────────────────────────────────────────────
function initHeader() {
  const now = new Date();
  const hour = now.getHours();
  let greeting;
  if (hour < 6)       greeting = 'Good night';
  else if (hour < 12) greeting = 'Good morning';
  else if (hour < 17) greeting = 'Good afternoon';
  else if (hour < 20) greeting = 'Good evening';
  else                greeting = 'Good night';
  setEl('greeting-text', greeting + ', Chef! 👨‍🍳');

  // Use local timezone (not UTC) — fixes IST users seeing previous day
  function _updateDate() {
    const d = new Date();
    const dateStr = d.toLocaleDateString('en-IN', {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'
    });
    setEl('today-date', dateStr);
  }
  _updateDate();
  // Refresh date every minute so it stays current if left open overnight
  setInterval(_updateDate, 60000);
}

// ── API Status Dot ─────────────────────────────────────────────────────────────
// Render free-tier cold-starts can take 30-50s. Strategy:
//   • First attempt: 35s timeout — if OK → green, if timeout → show yellow "waking"
//   • Auto-retry after 30s (one more chance before going red)
let _apiWaking = false;
let _wakeRetryTimer = null;

async function _pingHealth(timeoutMs) {
  const controller = new AbortController();
  const tid = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // Use /ping (lightweight, no DB query) to check if server is alive
    const res = await fetch(`${BASE_URL}/ping`, { signal: controller.signal });
    clearTimeout(tid);
    return res.ok;
  } catch {
    clearTimeout(tid);
    return false;
  }
}

async function checkApiStatus() {
  const dot = document.getElementById('api-dot');
  if (!dot) return;

  // Quick 5s check first — if already awake, respond instantly
  const quick = await _pingHealth(5000);
  if (quick) {
    dot.style.background = '';
    dot.classList.add('online');
    dot.classList.remove('offline');
    dot.title = 'API Online ✓';
    _apiWaking = false;
    clearTimeout(_wakeRetryTimer);
    return;
  }

  // Server didn't respond in 5s — could be cold-starting. Show yellow.
  dot.classList.remove('online', 'offline');
  dot.style.background = '#f59e0b';
  dot.style.boxShadow = '0 0 6px #f59e0b';
  dot.title = 'API waking up (Render cold start)…';
  _apiWaking = true;

  // Wait up to 40s for cold start to complete
  const waking = await _pingHealth(40000);
  if (waking) {
    dot.style.background = '';
    dot.style.boxShadow = '';
    dot.classList.add('online');
    dot.classList.remove('offline');
    dot.title = 'API Online ✓';
    _apiWaking = false;
  } else {
    // Genuinely offline
    dot.style.background = '';
    dot.style.boxShadow = '';
    dot.classList.add('offline');
    dot.classList.remove('online');
    dot.title = 'API Offline — backend may be down';
    _apiWaking = false;
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function showToast(message, type = '') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.className = `toast ${type} show`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = 'toast'; }, 3200);
}

// ── Utilities ──────────────────────────────────────────────────────────────────
function setEl(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Splash ─────────────────────────────────────────────────────────────────────
function hideSplash() {
  setTimeout(() => {
    const splash = document.getElementById('splash');
    if (splash) splash.classList.add('hidden');
  }, 2000);
}

// ── Chart.js global defaults ───────────────────────────────────────────────────
if (typeof Chart !== 'undefined') {
  Chart.defaults.color = '#9B9B9B';
  Chart.defaults.font.family = "'Inter', sans-serif";
  Chart.defaults.borderColor = '#2A2A2A';
}

// ── Init ───────────────────────────────────────────────────────────────────────
function initApp() {
  initHeader();
  checkApiStatus();
  setInterval(checkApiStatus, 90000);   // Re-check every 90s

  // ── Keepalive: ping every 10 min to prevent Render free-tier from sleeping ──
  // Render spins down after 15 min of inactivity. A silent ping keeps it awake
  // while any browser tab has the app open.
  setInterval(() => {
    fetch(`${BASE_URL}/ping`, { method: 'GET', cache: 'no-store' }).catch(() => {});
  }, 10 * 60 * 1000);  // 10 minutes

  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => navigateTo(btn.dataset.screen));
  });

  navigateTo('dashboard');
  hideSplash();
}

// ── Info Tooltip System ────────────────────────────────────────────────────────
// Usage: infoTip('Title', 'Explanation text here') → returns HTML string with ⓘ button
const INFO_TIPS = {
  'todayRevenue':    { title: '💰 Today\'s Revenue', body: 'Total money earned from all food and drink orders sold today. Updates as you log sales in the Log Sales tab. Note: manual entries need a unit price to show revenue — if you just entered quantities, this shows ₹0 but units sold is correct.' },
  'todayUnits':      { title: '📦 Units Sold', body: 'Total number of individual food/drink portions sold today across all items. Logged manually in the Log Sales tab or via PDF upload.' },
  'avgPerItem':      { title: '📊 Avg ₹ Per Unit', body: 'Average revenue per unit sold today. Calculated as Today\'s Revenue ÷ Total Units Sold. Only accurate when revenue data is available.' },
  'activeItems':     { title: '🍽️ Active Items', body: 'Total number of distinct menu items in the system. This is your full menu catalog — 223 items from your April–May POS data.' },
  'revenueTrend':    { title: '📈 Revenue Trend', body: 'Your daily gross revenue over the last 14 days. Each point is one day\'s total sales. Gaps mean no sales were logged for that day. Data comes from your actual POS records (April–May 2026).' },
  'tomorrowOrders':  { title: '🔮 Tomorrow\'s Top Orders', body: 'The AI\'s top predicted order quantities for tomorrow — how many units of each item you should procure/prepare. Based on: your past 45 days of sales patterns + tomorrow\'s weather forecast + day-of-week trends + festival calendar. Sorted by highest recommended quantity.' },
  'actualVsPred':    { title: '📊 Actual vs Predicted (Today)', body: 'Side-by-side comparison of what the AI predicted you would sell today (violet bars) vs what was actually sold (cyan bars), grouped by food category. A close match means the AI is calibrated well. Big gaps highlight where the model needs improvement.' },
  'recDate':         { title: '📅 Ordering For', body: 'The date you are ordering ingredients/stock for. Defaults to tomorrow. You can change it to plan ahead for any future date — the AI will adjust recommendations for that day\'s weather and day-of-week pattern.' },
  'recContext':      { title: 'ℹ️ Context Signals', body: 'The three signals the AI used to generate today\'s recommendations:\n1. Date & Day — day-of-week pattern (e.g. Sundays sell 30% more biryani)\n2. Weather — tomorrow\'s forecast adjusts quantities (rain boosts soups, heat boosts cold drinks)\n3. Festival — if a festival falls on this date, demand gets a multiplier boost.' },
  'recQty':          { title: '🔢 Recommended Quantity', body: 'How many units of this item you should order/prepare for tomorrow. Formula: (7-day average) × (day-of-week factor) × (weather factor) × (festival multiplier) + 10% safety buffer. You can tap − or + to override this number.' },
  'recReason':       { title: '💡 Signal / Reason', body: 'Why the AI chose this quantity. Examples:\n↑ Sunday peak day — Sundays historically sell more\n↑ Hot, 44°C — heat increases cold drink demand\n🎉 Diwali — festival multiplier applied\n↓ Slow Tuesday — Tuesdays are historically quieter' },
  'logDate':         { title: '📅 Sales Date', body: 'The date you are logging sales for. Defaults to today. Change this if you forgot to log yesterday\'s sales — the data will still be used to improve future recommendations.' },
  'totalRevenue45':  { title: '💰 45-Day Revenue', body: 'Total gross revenue from all sales records in the database. Currently covers April 1 to May 15, 2026 — your 45 days of POS history that was pre-loaded into the system.' },
  'dailyAvg':        { title: '📊 Daily Average', body: 'Average daily revenue across all days in the database. Calculated as Total Revenue ÷ Number of days with recorded sales.' },
  'bestDay':         { title: '🏆 Best Day', body: 'The single highest-revenue day in your entire sales history. Useful for understanding your peak capacity.' },
  'totalUnits':      { title: '📦 Total Units', body: 'Total number of individual food/drink portions sold across all 45 days of data. Gives you a sense of overall volume.' },
  'dowPattern':      { title: '📅 Day-of-Week Pattern', body: 'Average revenue for each day of the week (Monday through Sunday) across all your historical data. This tells you which days are consistently busy vs slow — and the AI uses this pattern to adjust tomorrow\'s recommendations.' },
  'catRevenue':      { title: '🏷️ Revenue by Category', body: 'Revenue breakdown by food category (Biryanis, Chicken, Beverages, etc.) across your 45-day history. Shows which food groups drive the most income for the hotel.' },
  'itemDeepDive':    { title: '🔍 Item Deep Dive', body: 'Search for any specific menu item and see its complete sales history — daily quantities sold over time, plus its day-of-week pattern. Useful for understanding which items are trending up or down.' },
  'accuracyScore':   { title: '🎯 Model Accuracy', body: 'The overall percentage accuracy of the AI\'s predictions. A higher percentage indicates that tomorrow\'s predictions are closer to actual sales. Formula: 100% minus the average error percentage relative to total orders.' },
  'mae':             { title: '📐 Mean Absolute Error (MAE)', body: 'The average absolute difference between the quantities recommended by the AI and the actual quantities sold. A lower MAE indicates more precise recommendations (fewer wasted ingredients and fewer lost sales).' },
};

function infoTip(key) {
  return `<button class="info-btn" onclick="showInfoPopup('${key}')" title="What is this?" aria-label="More info">ⓘ</button>`;
}

function showInfoPopup(key) {
  const tip = INFO_TIPS[key];
  if (!tip) return;
  // Remove any existing popup
  const existing = document.getElementById('info-popup-overlay');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.className = 'info-popup-overlay';
  overlay.id = 'info-popup-overlay';
  overlay.innerHTML = `
    <div class="info-popup" role="dialog" aria-modal="true">
      <div class="info-popup-title"><span>ℹ️</span>${tip.title}</div>
      <div class="info-popup-body">${tip.body.replace(/\n/g, '<br>')}</div>
      <button class="info-popup-close" onclick="document.getElementById('info-popup-overlay').remove()">Got it</button>
    </div>
  `;
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
}

document.addEventListener('DOMContentLoaded', initApp);
