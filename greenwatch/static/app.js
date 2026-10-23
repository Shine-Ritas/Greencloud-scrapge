'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let state = null;
let priceFilter = 'target';
let activeAction = false;
let toastTimer;
let chartKey = '';

function formatTime(epoch, options = {}) {
  if (!epoch) return '—';
  return new Intl.DateTimeFormat('en-GB', {timeZone: state?.settings.timezone || 'Asia/Bangkok', hour:'2-digit', minute:'2-digit', ...options}).format(new Date(epoch * 1000));
}
function relative(epoch) {
  if (!epoch) return 'Never';
  const seconds = Math.max(0, Math.floor(Date.now()/1000 - epoch));
  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds/60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds/3600)} hr ago`;
  return `${Math.floor(seconds/86400)} days ago`;
}
function toast(message, error=false) {
  $('toast').textContent = message;
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {$('toast').hidden = true;}, error ? 9000 : 5000);
}
async function action(path) {
  const response = await fetch(path, {method:'POST', headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The action failed');
  return data;
}
function countdown() {
  if (!state) return;
  const remaining = Math.max(0, Math.ceil(state.next_scrape_at - Date.now()/1000));
  const h = String(Math.floor(remaining/3600)).padStart(2,'0');
  const m = String(Math.floor(remaining%3600/60)).padStart(2,'0');
  const s = String(remaining%60).padStart(2,'0');
  $('countdown').textContent = remaining ? `${h}:${m}:${s}` : 'Due now';
  $('metric-last').textContent = state.last_success_at ? relative(state.last_success_at) : 'No checks yet';
}
function country(location) {
  const text = location.toLowerCase();
  if (text.includes('singapore')) return 'SG';
  if (/tokyo|osaka|japan/.test(text)) return 'JP';
  if (text.includes('hong kong')) return 'HK';
  if (/sydney|melbourne|australia/.test(text)) return 'AU';
  if (/seoul|korea/.test(text)) return 'KR';
  return 'AP';
}
function renderProducts() {
  if (!state) return;
  const location = $('location-filter').value;
  const products = state.products.filter(p => (!location || p.location === location) && (priceFilter === 'all' || p.price_cents === 2500 && p.billing === 'Annually'));
  $('inventory-count').textContent = products.length;
  $('filter-target').classList.toggle('active', priceFilter === 'target');
  $('filter-all').classList.toggle('active', priceFilter === 'all');
  $('filter-target').setAttribute('aria-pressed', priceFilter === 'target');
  $('filter-all').setAttribute('aria-pressed', priceFilter === 'all');
  $('products-body').innerHTML = products.length ? products.map(p => {
    const good = p.eligible && p.present;
    const status = !p.present ? 'Not in latest check' : good ? 'Match found' : p.stock === 0 ? 'Out of stock' : !p.orderable ? 'Not orderable' : 'Outside target';
    const badge = good ? 'available' : p.stock > 0 && p.present ? 'other' : 'soldout';
    const ram = p.specs.find(s => /\bRAM\b/.test(s)) || '';
    const drive = p.specs.find(s => /Hard drive/.test(s)) || '';
    const specs = `${ram}${ram && drive ? ' · ' : ''}${drive}`.replace(' Hard drive','');
    return `<tr><td><div class="plan-cell"><span class="server-icon" aria-hidden="true">▤</span><div><span class="plan-name">${escapeHtml(p.name)}</span><span class="plan-specs" title="${escapeHtml(p.specs.join(' · '))}">${escapeHtml(specs)}</span></div></div></td><td><span class="country-dot">${country(p.location)}</span>${escapeHtml(p.location)}</td><td class="price">$${(p.price_cents/100).toFixed(2)} <small>/${p.billing === 'Annually' ? 'yr' : escapeHtml(p.billing)}</small></td><td><span class="stock-count">${p.present ? p.stock : '—'}</span></td><td><span class="badge ${badge}"><span class="tiny-dot ${good ? 'mint-dot' : ''}"></span>${status}</span></td><td><a class="order-link" href="${escapeHtml(p.url)}" target="_blank" rel="noreferrer" aria-label="View ${escapeHtml(p.name)} at GreenCloud">↗</a></td></tr>`;
  }).join('') : `<tr><td colspan="6" class="empty-state">${state.last_success_at ? 'No plans match these filters.' : 'Waiting for the first successful scrape. Results will appear here automatically.'}</td></tr>`;
  $('inventory-footer').textContent = `${products.length} plans shown · ${state.products.filter(p=>p.present).length} APAC plans monitored`;
}
function renderChart() {
  const key = JSON.stringify(state.trend);
  if (key === chartKey) return;
  chartKey = key;
  const data = state.trend;
  if (!data.length) return;
  const width = 640, height = 192, left = 28, right = 13, top = 14, bottom = 26;
  const plotWidth = width-left-right, plotHeight = height-top-bottom;
  const max = Math.max(3, ...data.map(d=>d.match_count));
  const first = data[0].started_at, last = data.at(-1).started_at;
  const points = data.map(d => [data.length === 1 ? left+plotWidth/2 : left+(d.started_at-first)/(last-first || 1)*plotWidth, top+plotHeight-d.match_count/max*plotHeight]);
  const path = points.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const ticks = [...new Set([0,Math.round(max/3),Math.round(max*2/3),max])];
  const grid = ticks.map(n=>{const y=top+plotHeight-n/max*plotHeight;return `<line x1="${left}" x2="${width-right}" y1="${y}" y2="${y}" stroke="#edf1ee" stroke-dasharray="3 4"/><text x="11" y="${y+3}" fill="#a5b1aa" font-size="9" text-anchor="middle">${n}</text>`;}).join('');
  const area = data.length > 1 ? `<path d="${path} L${points.at(-1)[0]},${top+plotHeight} L${points[0][0]},${top+plotHeight} Z" fill="url(#chart-fill)"/>` : '';
  const dots = data.map((d,i)=>`<circle cx="${points[i][0]}" cy="${points[i][1]}" r="${data.length>40?2:3}" fill="#38a37a" stroke="white" stroke-width="1.5"><title>${escapeHtml(formatTime(d.started_at,{month:'short',day:'numeric'}))}: ${d.match_count} matching plans</title></circle>`).join('');
  const label = epoch=>escapeHtml(formatTime(epoch,{month:'short',day:'numeric'}));
  $('chart').innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#9bdfbc" stop-opacity=".3"/><stop offset="100%" stop-color="#9bdfbc" stop-opacity=".03"/></linearGradient></defs>${grid}${area}<path d="${path}" fill="none" stroke="#43ad83" stroke-width="2" stroke-linejoin="round"/>${dots}<text x="${left}" y="${height-5}" fill="#a2aea7" font-size="8">${label(first)}</text>${data.length>1?`<text x="${width-right}" y="${height-5}" fill="#a2aea7" font-size="8" text-anchor="end">${label(last)}</text>`:''}</svg>`;
  $('chart').setAttribute('aria-label', `${data.length} successful checks; latest count: ${data.at(-1).match_count} matching plans`);
  $('chart-range').textContent = `${data.length} successful ${data.length === 1 ? 'check' : 'checks'}`;
}
function render() {
  const s = state;
  const current = s.products.filter(p=>p.present);
  const matches = current.filter(p=>p.eligible);
  const latest = s.runs[0];
  const failed = latest?.status === 'error';
  const stale = s.last_success_at && s.now - s.last_success_at > 21660;
  $('timezone-label').textContent = s.settings.timezone;
  $('schedule-zone').textContent = s.settings.timezone;
  $('metric-matches').textContent = matches.length;
  $('metric-locations').textContent = new Set(current.map(p=>p.location)).size;
  $('locations-note').textContent = `${current.length} APAC plans · all price tiers`;
  $('metric-alerts').textContent = s.alert_counts.sent || 0;
  $('alerts-note').textContent = `Within retained history · ${s.alert_counts.pending || 0} pending`;
  $('last-note').textContent = s.last_success_at ? `${formatTime(s.last_success_at,{day:'numeric',month:'short'})} · ${s.settings.timezone}` : 'Waiting for first successful check';
  $('health-dot').className = `status-dot ${failed || stale ? 'bad' : s.last_success_at ? 'online' : ''}`;
  $('monitor-state').textContent = s.running ? 'Checking inventory…' : failed ? 'Last check failed' : stale ? 'Inventory is stale' : s.last_success_at ? 'Monitor is watching' : 'Waiting for first check';
  $('scrape-button').disabled = s.running || activeAction;
  $('scrape-label').textContent = s.running ? 'Checking…' : 'Scrape now';
  $('scrape-icon').classList.toggle('spinning', s.running);
  const warnings = [];
  if (!s.settings.telegram_configured) warnings.push('Telegram is not configured. Add your bot token and channel ID to .env, then recreate the container.');
  if (failed) warnings.push(`Last check failed: ${latest.error}. Previous stock is shown; alerts are paused until a successful check.`);
  else if (stale) warnings.push('Stock data is older than six hours. Availability may have changed.');
  if (s.alert_counts.failed) warnings.push(`${s.alert_counts.failed} notification(s) exhausted delivery retries. Check the notification log and your Telegram configuration.`);
  $('warning').hidden = !warnings.length;
  $('warning').textContent = warnings.join(' ');
  $('telegram-state').textContent = s.settings.telegram_configured ? 'Telegram configured' : 'Setup required';
  $('telegram-dot').className = `tiny-dot ${s.settings.telegram_configured ? 'mint-dot' : ''}`;
  $('pending-note').textContent = s.alert_counts.pending ? `${s.alert_counts.pending} pending. Up to 5 delivery attempts; only while the plan still qualifies.` : 'Up to 5 delivery attempts per stock event.';
  $('test-button').disabled = !s.settings.telegram_configured;
  const selected = $('location-filter').value;
  const locations = [...new Set(s.products.map(p=>p.location))].sort();
  $('location-filter').innerHTML = '<option value="">All locations</option>' + locations.map(l=>`<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
  if (locations.includes(selected)) $('location-filter').value = selected;
  renderProducts();
  renderChart();
  $('success-rate').textContent = s.summary.total ? `${Math.round(s.summary.successful/s.summary.total*100)}% success · retained checks` : 'No checks yet';
  $('runs').innerHTML = s.runs.length ? s.runs.map(r=>`<div class="activity-item"><span class="activity-mark ${r.status}">${r.status === 'ok' ? '✓' : r.status === 'running' ? '↻' : '!'}</span><div class="activity-text"><strong>${r.trigger === 'manual' ? 'Manual' : 'Scheduled'} check ${r.status === 'ok' ? 'completed' : r.status === 'running' ? 'in progress' : 'failed'}</strong><p>${r.status === 'ok' ? `${r.apac_count} APAC plans · ${r.match_count} matches · ${Math.max(0,(r.finished_at-r.started_at)).toFixed(1)}s` : escapeHtml(r.error || 'Fetching store inventory…')}</p></div><div class="activity-time">${formatTime(r.started_at)}<small>${formatTime(r.started_at,{month:'short',day:'numeric',hour:undefined,minute:undefined})}</small></div></div>`).join('') : '<p class="empty-state">No checks yet.</p>';
  const productNames = Object.fromEntries(s.products.map(p=>[p.id,p.name]));
  $('notifications').innerHTML = s.notifications.length ? s.notifications.map(n=>`<div class="activity-item"><span class="activity-mark ${n.status === 'failed' ? 'error' : n.status === 'pending' ? 'running' : ''}">${n.status === 'sent' ? '✓' : n.status === 'failed' ? '!' : '·'}</span><div class="activity-text"><strong>${escapeHtml(productNames[n.product_id] || n.product_id)}</strong><p>${escapeHtml(n.status)} · ${n.attempts} ${n.attempts===1?'attempt':'attempts'}${n.error ? ` · ${escapeHtml(n.error)}` : ''}</p></div><div class="activity-time">${formatTime(n.sent_at || n.created_at)}<small>${formatTime(n.sent_at || n.created_at,{day:'numeric',month:'short',hour:undefined,minute:undefined})}</small></div></div>`).join('') : '<p class="empty-state">No alerts yet. We’ll keep watch.</p>';
  $('cleanup-note').textContent = `Keep ${s.settings.retention_days} days of history`;
  $('next-cleanup').textContent = formatTime(s.next_cleanup_at,{day:'numeric',month:'short'});
  $('last-cleanup').textContent = s.last_cleanup ? `Last: ${formatTime(s.last_cleanup.at,{month:'short',day:'numeric'})} · ${s.last_cleanup.runs_removed} runs removed` : 'No cleanup has run yet';
  $('auth-state').textContent = s.settings.authentication_enabled ? 'Password protected' : 'No password configured';
  countdown();
}
async function refresh() {
  try {
    const response = await fetch('/api/status', {cache:'no-store'});
    if (!response.ok) throw new Error(response.status===401 ? 'Authentication expired. Reload the dashboard.' : 'The monitor is unavailable.');
    state = await response.json();
    $('connection-error').hidden = true;
    render();
  } catch (error) {
    $('connection-error').textContent = `${error.message} Retrying automatically.`;
    $('connection-error').hidden = false;
  }
}
$('scrape-button').addEventListener('click', async()=>{
  activeAction = true;
  $('scrape-button').disabled = true;
  try {toast((await action('/api/scrape')).message);} catch(error) {toast(error.message,true);}
  finally {activeAction=false;await refresh();}
});
$('test-button').addEventListener('click', async()=>{
  $('test-button').disabled=true;
  try {toast((await action('/api/telegram/test')).message);} catch(error) {toast(error.message,true);}
  finally {$('test-button').disabled=!state?.settings.telegram_configured;}
});
$('filter-target').addEventListener('click',()=>{priceFilter='target';renderProducts();});
$('filter-all').addEventListener('click',()=>{priceFilter='all';renderProducts();});
$('location-filter').addEventListener('change',renderProducts);
refresh();
setInterval(countdown,1000);
setInterval(refresh,5000);
