(function () {
  function formatNum(value) {
    try {
      const n = Number(value);
      if (Number.isNaN(n)) return String(value);
      return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
    } catch {
      return String(value);
    }
  }

  function renderRow(s) {
    const tr = document.createElement('tr');
    tr.id = `run-${s.run_id}`;
    tr.innerHTML = `
      <td><code title="${s.run_id}">${s.run_id.slice(0, 6)}…${s.run_id.slice(-4)}</code></td>
      <td>${s.market}</td>
      <td>${s.status}</td>
      <td class="cell-num">${formatNum(s.position_notional)}</td>
      <td class="cell-num">${formatNum(s.entry_price)}</td>
      <td class="cell-num">${formatNum(s.mark_price)}</td>
      <td class="cell-num">${formatNum(s.unrealized_pnl)}</td>
      <td class="cell-num">${formatNum(s.realized_pnl)}</td>
      <td class="cell-num"><strong>${formatNum((Number(s.realized_pnl || 0) + Number(s.unrealized_pnl || 0)))}</strong></td>
      <td>${s.timestamp}</td>
    `;
    return tr;
  }

  function upsertRow(snapshot) {
    const tbody = document.getElementById('monitor-rows');
    if (!tbody) return;
    const existing = document.getElementById(`run-${snapshot.run_id}`);
    const row = renderRow(snapshot);
    if (existing) {
      existing.replaceWith(row);
    } else {
      // remove empty row if present
      const empty = tbody.querySelector('.cell-empty');
      if (empty) empty.parentElement?.remove();
      tbody.appendChild(row);
    }
  }

  function connectSSE() {
    try {
      const es = new EventSource('/monitoring/stream');
      es.onmessage = (evt) => {
        try {
          const payload = JSON.parse(evt.data);
          if (payload && payload.ok && payload.data) {
            upsertRow(payload.data);
          }
        } catch (e) {
          console.warn('SSE parse error', e);
        }
      };
      es.onerror = () => {
        // auto-reconnect after backoff
        es.close();
        setTimeout(connectSSE, 1500);
      };
    } catch (e) {
      console.warn('SSE error', e);
    }
  }

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    connectSSE();
  } else {
    document.addEventListener('DOMContentLoaded', connectSSE);
  }
})();
