const PANEL_ID = 'askchip-voice-metrics-panel';
const PANEL_STYLE = 'position:fixed;right:12px;bottom:12px;width:240px;max-height:200px;overflow:auto;background:rgba(255,255,255,0.97);box-shadow:0 2px 6px rgba(0,0,0,0.2);border-radius:6px;padding:8px;z-index:2147483000;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;';
const TABLE_STYLE = 'width:100%;border-collapse:collapse;font-size:11px;';
const CELL_STYLE = 'padding:2px 4px;border-top:1px solid rgba(0,0,0,0.1);text-align:left;';

function getMetrics() {
  try {
    return typeof window.__getRecentVoiceMetrics === 'function' ? window.__getRecentVoiceMetrics().slice(0, 10) : [];
  } catch {
    return [];
  }
}

export function mountVoiceMetricsPanel() {
  try {
    if (window?.__askchip_config?.admin?.voice_metrics_panel !== true) return;
    if (document.getElementById(PANEL_ID)) return;
    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = PANEL_STYLE;
    const title = document.createElement('div');
    title.textContent = 'Voice Metrics';
    title.style.cssText = 'font-size:12px;font-weight:600;margin-bottom:4px;';
    const table = document.createElement('table');
    table.style.cssText = TABLE_STYLE;
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th style="text-align:left;padding:2px 4px;">#</th><th style="text-align:left;padding:2px 4px;">Payload</th></tr>';
    const tbody = document.createElement('tbody');
    const render = () => {
      tbody.innerHTML = '';
      const metrics = getMetrics();
      if (!metrics.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 2;
        cell.style.cssText = CELL_STYLE;
        cell.textContent = 'No metrics yet';
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
      }
      metrics.forEach((entry, idx) => {
        const row = document.createElement('tr');
        const idxCell = document.createElement('td');
        idxCell.style.cssText = CELL_STYLE;
        idxCell.textContent = String(idx + 1);
        const dataCell = document.createElement('td');
        dataCell.style.cssText = CELL_STYLE;
        try {
          dataCell.textContent = JSON.stringify(entry);
        } catch {
          dataCell.textContent = String(entry);
        }
        row.append(idxCell, dataCell);
        tbody.appendChild(row);
      });
    };
    render();
    window.addEventListener('voice-metrics-updated', render);
    table.append(thead, tbody);
    panel.append(title, table);
    document.body.appendChild(panel);
  } catch (err) {
    console.warn('[VoiceMetricsPanel] mount failed', err);
  }
}
