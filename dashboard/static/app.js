let flightRanking = { order: "desc", limit: 15 };

const PHASES_ORDER = ["CLIMB", "CRUISE", "DESCENT", "LEVEL DESCENT", "LEVEL FLIGHT"];
const PHASE_COLOR_VAR = {
  "CLIMB": "--series-climb",
  "CRUISE": "--series-cruise",
  "DESCENT": "--series-descent",
  "LEVEL DESCENT": "--series-level-descent",
  "LEVEL FLIGHT": "--series-level-flight",
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function phaseColor(phase) {
  return cssVar(PHASE_COLOR_VAR[phase] || "--series-climb");
}
function ink(role) {
  return cssVar(`--text-${role}`);
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ---- loading indicator -----------------------------------------------

let activeRequests = 0;
function beginLoad() {
  activeRequests++;
  document.getElementById("loading-indicator").hidden = false;
}
function endLoad() {
  activeRequests = Math.max(0, activeRequests - 1);
  if (activeRequests === 0) document.getElementById("loading-indicator").hidden = true;
}
async function fetchJSON(url, options) {
  beginLoad();
  try {
    const res = await fetch(url, options);
    return await res.json();
  } finally {
    endLoad();
  }
}

// ---- chart card chrome: title, export, reset zoom, info -----------------

const chartRegistry = new Map();

function resetZoom(chartId) {
  const el = document.getElementById(chartId);
  if (!el || !el.layout) return;
  if (el.layout.geo) {
    Plotly.relayout(chartId, { "geo.fitbounds": "locations" });
    return;
  }
  const update = {};
  Object.keys(el.layout).forEach(k => {
    if (/^xaxis\d*$/.test(k) || /^yaxis\d*$/.test(k)) update[`${k}.autorange`] = true;
  });
  Plotly.relayout(chartId, update);
}

function ensureChartCard(chartId, { title, exportName, infoText }) {
  const container = document.getElementById(chartId);
  if (!container) return;
  const card = container.closest(".chart-card");
  if (!card) return;
  const header = card.querySelector(".chart-card-header");

  let entry = chartRegistry.get(chartId);
  if (!entry) {
    const titleEl = document.createElement("h3");
    titleEl.className = "chart-title-text";
    const actions = document.createElement("div");
    actions.className = "chart-actions";
    header.appendChild(titleEl);
    header.appendChild(actions);

    const exportBtn = document.createElement("button");
    exportBtn.type = "button";
    exportBtn.className = "action-btn";
    exportBtn.textContent = "Export PNG";
    actions.appendChild(exportBtn);

    const resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.className = "action-btn";
    resetBtn.textContent = "Reset zoom";
    resetBtn.addEventListener("click", () => resetZoom(chartId));
    actions.appendChild(resetBtn);

    const infoBtn = document.createElement("button");
    infoBtn.type = "button";
    infoBtn.className = "action-btn";
    infoBtn.textContent = "ⓘ Info";
    actions.appendChild(infoBtn);

    const infoP = document.createElement("p");
    infoP.className = "chart-info-text";
    infoP.hidden = true;
    card.insertBefore(infoP, container);

    infoBtn.addEventListener("click", () => {
      infoP.hidden = !infoP.hidden;
      infoBtn.classList.toggle("active", !infoP.hidden);
    });

    entry = { titleEl, exportBtn, infoP };
    chartRegistry.set(chartId, entry);
  }
  entry.titleEl.textContent = title;
  entry.infoP.textContent = infoText;
  entry.exportBtn.onclick = () => {
    Plotly.downloadImage(chartId, { format: "png", filename: exportName, width: 1200, height: 700, scale: 2 });
  };
}

function ensureInfoOnlyCard(containerId, { title, infoText }) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const card = container.closest(".chart-card");
  if (!card) return;
  const header = card.querySelector(".chart-card-header");

  let entry = chartRegistry.get(containerId);
  if (!entry) {
    const titleEl = document.createElement("h3");
    titleEl.className = "chart-title-text";
    const actions = document.createElement("div");
    actions.className = "chart-actions";
    header.appendChild(titleEl);
    header.appendChild(actions);

    const infoBtn = document.createElement("button");
    infoBtn.type = "button";
    infoBtn.className = "action-btn";
    infoBtn.textContent = "ⓘ Info";
    actions.appendChild(infoBtn);

    const infoP = document.createElement("p");
    infoP.className = "chart-info-text";
    infoP.hidden = true;
    card.insertBefore(infoP, container);

    infoBtn.addEventListener("click", () => {
      infoP.hidden = !infoP.hidden;
      infoBtn.classList.toggle("active", !infoP.hidden);
    });

    entry = { titleEl, infoP };
    chartRegistry.set(containerId, entry);
  }
  entry.titleEl.textContent = title;
  entry.infoP.textContent = infoText;
}

function exportTableAsCSV(rows, columns, filename) {
  const header = columns.map(c => c.label).join(",");
  const lines = rows.map(r => columns.map(c => JSON.stringify(r[c.key] ?? "")).join(","));
  const csv = [header, ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---- shared chart helpers -------------------------------------------------

function baseLayout(xTitle, yTitle) {
  return {
    autosize: true,
    margin: { l: 56, r: 16, t: 8, b: 44 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: ink("secondary"), size: 12, family: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    xaxis: {
      title: { text: xTitle, font: { color: ink("muted") } },
      gridcolor: cssVar("--gridline"),
      zerolinecolor: cssVar("--baseline"),
      linecolor: cssVar("--baseline"),
      tickfont: { color: ink("muted") },
      automargin: true,
    },
    yaxis: {
      title: { text: yTitle, font: { color: ink("muted") } },
      gridcolor: cssVar("--gridline"),
      zerolinecolor: cssVar("--baseline"),
      linecolor: cssVar("--baseline"),
      tickfont: { color: ink("muted") },
      automargin: true,
    },
    legend: { orientation: "h", y: -0.22, font: { color: ink("secondary") } },
    hoverlabel: { bgcolor: cssVar("--surface-1"), bordercolor: cssVar("--border"), font: { color: ink("primary") } },
  };
}

const PLOTLY_CONFIG = { responsive: true, displayModeBar: false };

function segmentsByPhase(xs, ys, phases) {
  const segments = [];
  let start = 0;
  for (let i = 1; i <= phases.length; i++) {
    if (i === phases.length || phases[i] !== phases[start]) {
      const end = i;
      const xSeg = xs.slice(start, end);
      const ySeg = ys.slice(start, end);
      if (end < phases.length) { xSeg.push(xs[end]); ySeg.push(ys[end]); }
      segments.push({ phase: phases[start], x: xSeg, y: ySeg });
      start = end;
    }
  }
  return segments;
}

// Contiguous [start, end] intervals per phase occurrence (a phase can recur
// non-contiguously, e.g. DESCENT / LEVEL DESCENT interleaving).
function phaseIntervals(ts, phases) {
  const intervals = [];
  let start = 0;
  for (let i = 1; i <= phases.length; i++) {
    if (i === phases.length || phases[i] !== phases[start]) {
      intervals.push({ phase: phases[start], start: ts[start], end: ts[i - 1] });
      start = i;
    }
  }
  return intervals;
}

function sumByPhase(values, phases) {
  const totals = {};
  const counts = {};
  for (let i = 0; i < phases.length; i++) {
    totals[phases[i]] = (totals[phases[i]] || 0) + values[i];
    counts[phases[i]] = (counts[phases[i]] || 0) + 1;
  }
  return PHASES_ORDER
    .filter(p => totals[p] !== undefined)
    .map(p => ({ phase: p, total: totals[p], n: counts[p] }));
}

function rollingAverage(values, window) {
  const result = [];
  for (let i = 0; i < values.length; i++) {
    const start = Math.max(0, i - window + 1);
    const slice = values.slice(start, i + 1);
    result.push(slice.reduce((a, b) => a + b, 0) / slice.length);
  }
  return result;
}

function phaseSegmentTraces(xs, ys, phases, extra) {
  const seen = new Set();
  return segmentsByPhase(xs, ys, phases).map(seg => {
    const showlegend = !seen.has(seg.phase);
    seen.add(seg.phase);
    return {
      x: seg.x,
      y: seg.y,
      type: "scatter",
      mode: "lines",
      name: seg.phase,
      legendgroup: seg.phase,
      showlegend,
      line: { width: 2, color: phaseColor(seg.phase) },
      ...extra,
    };
  });
}

// =========================================================================
// AGGREGATE VIEW
// =========================================================================

function activePhases() {
  return Array.from(document.querySelectorAll("#phase-checkboxes input:checked")).map(i => i.value);
}
function activeAircraft() {
  return Array.from(document.querySelectorAll("#aircraft-checkboxes input:checked")).map(i => i.value);
}
function altitudeFilterEnabled() {
  return document.getElementById("altitude-enable").checked;
}
function locationFilterEnabled() {
  return document.getElementById("location-enable").checked;
}

function timeToMinutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function currentFilters() {
  const altOn = altitudeFilterEnabled();
  const locOn = locationFilterEnabled();
  const regOn = registrationFilterEnabled();
  return {
    aircraft_types: activeAircraft(),
    date_from: document.getElementById("date-from").value,
    date_to: document.getElementById("date-to").value,
    takeoff_start_min: timeToMinutes(document.getElementById("takeoff-from").value || "00:00"),
    takeoff_end_min: timeToMinutes(document.getElementById("takeoff-to").value || "23:30"),
    phases: activePhases(),
    altitude_min: altOn ? Number(document.getElementById("altitude-min").value) : null,
    altitude_max: altOn ? Number(document.getElementById("altitude-max").value) : null,
    origin_icaos: locOn ? locationSelectionIcaos("dep") : null,
    destination_icaos: locOn ? locationSelectionIcaos("arr") : null,
    registrations: regOn ? activeRegistrations() : null,
    top_n: appSettings.topN,
    flights_limit: flightRanking.limit,
    flights_order: flightRanking.order,
  };
}

function fmt(n, digits = 3) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (n === 0) return "0";
  return n.toPrecision(digits);
}

function renderStatCards(data) {
  const cards = [
    { label: "Flights matched", value: data.n_flights.toLocaleString() },
    { label: "Timesteps (rows)", value: data.n_rows.toLocaleString() },
    { label: "Total dust ingested (g)", value: fmt(data.total_dust_g) },
    { label: "Mean dust / timestep (g)", value: fmt(data.mean_dust_per_row) },
    { label: "Mean dust / flight (g)", value: fmt(data.mean_dust_per_flight) },
  ];
  document.getElementById("stat-cards").innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
    </div>
  `).join("");
}

function renderDustPerTime(data) {
  const traces = [];
  for (const phase of PHASES_ORDER) {
    const series = data.dust_per_time.by_phase[phase];
    if (!series || series.t.length === 0) continue;
    traces.push({
      x: series.t.map(s => s / 60),
      y: series.mean_dust,
      type: "scatter",
      mode: "lines",
      name: phase,
      line: { width: 2, color: phaseColor(phase) },
    });
  }
  Plotly.react("chart-dust-time", traces, baseLayout("Elapsed time (min)", "Mean dust ingested (g)"), PLOTLY_CONFIG);
  ensureChartCard("chart-dust-time", {
    title: "Dust ingested per elapsed time",
    exportName: "dust_per_elapsed_time",
    infoText: "Mean of CoreDustIngested_g (column R, per-timestep, not cumulative) grouped into 30-second buckets of elapsed time since each flight's first non-UNKNOWN-phase row, averaged across every flight matching the current filters, split by flight phase.",
  });
}

function renderCumulative(data) {
  const series = data.cumulative_dust_per_time;
  const trace = {
    x: series.t.map(s => s / 60),
    y: series.cumulative_mean_dust,
    type: "scatter",
    mode: "lines",
    name: "Cumulative dust",
    line: { width: 2, color: cssVar("--series-climb") },
    fill: "tozeroy",
    fillcolor: cssVar("--series-climb") + "1a",
  };
  const layout = baseLayout("Elapsed time (min)", "Cumulative mean dust ingested (g)");
  layout.showlegend = false;
  Plotly.react("chart-cumulative", [trace], layout, PLOTLY_CONFIG);
  ensureChartCard("chart-cumulative", {
    title: "Cumulative dust ingested per elapsed time",
    exportName: "cumulative_dust_per_elapsed_time",
    infoText: "Running sum (across elapsed-time buckets, in order) of the per-bucket mean dust values from the chart above. Approximates the average matching flight's cumulative dust ingested over the course of the flight.",
  });
}

function renderPdf(data) {
  const traces = [];
  for (const phase of PHASES_ORDER) {
    const series = data.dust_per_time_in_phase.by_phase[phase];
    if (!series || series.t.length === 0) continue;
    traces.push({
      x: series.t.map(s => s / 60),
      y: series.mean_dust,
      type: "scatter",
      mode: "lines",
      name: phase,
      line: { width: 2, color: phaseColor(phase) },
    });
  }
  const layout = baseLayout("Time spent in phase (min)", "Mean dust ingested (g)");
  Plotly.react("chart-pdf", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-pdf", {
    title: "Dust ingested vs time spent in phase",
    exportName: "dust_vs_time_in_phase",
    infoText: "Mean of CoreDustIngested_g (column R, per-timestep, not cumulative) grouped into 30-second buckets of time already spent in the current phase, not elapsed time since flight start, so every phase's line starts at zero regardless of when in the flight it occurs. A run of consecutive same-phase rows counts as one phase segment; averaged across every flight matching the current filters, split by flight phase.",
  });
}

function renderAltitude(data) {
  const traces = [];
  for (const phase of PHASES_ORDER) {
    const series = data.dust_per_altitude[phase];
    if (!series || series.alt.length === 0) continue;
    traces.push({
      x: series.alt,
      y: series.mean_dust,
      type: "scatter",
      mode: "lines",
      name: phase,
      line: { width: 2, color: phaseColor(phase) },
    });
  }
  Plotly.react("chart-altitude", traces, baseLayout("Altitude (ft)", "Mean dust ingested (g)"), PLOTLY_CONFIG);
  ensureChartCard("chart-altitude", {
    title: "Mean dust ingested vs altitude",
    exportName: "dust_vs_altitude",
    infoText: "Mean CoreDustIngested_g per 1,000ft altitude bucket (column F), split by phase, across all matching flights.",
  });
}

function renderBoxplot(data) {
  const traces = data.phase_boxplot
    .sort((a, b) => PHASES_ORDER.indexOf(a.phase) - PHASES_ORDER.indexOf(b.phase))
    .map(row => ({
      x: [row.phase],
      q1: [row.q1],
      median: [row.median],
      q3: [row.q3],
      lowerfence: [row.min],
      upperfence: [row.max],
      type: "box",
      name: row.phase,
      marker: { color: phaseColor(row.phase) },
      line: { color: phaseColor(row.phase) },
      fillcolor: phaseColor(row.phase) + "33",
    }));
  const layout = baseLayout("", "Dust ingested per timestep (g)");
  layout.yaxis.type = "log";
  layout.showlegend = false;
  Plotly.react("chart-boxplot", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-boxplot", {
    title: "Dust ingested per timestep, by phase",
    exportName: "dust_by_phase_boxplot",
    infoText: "Quartiles (Q1 / median / Q3) of per-timestep CoreDustIngested_g for each phase; whiskers capped at 1.5× the interquartile range (standard Tukey boxplot).",
  });
}

const ROLLING_AVG_WINDOW = 7;

function renderByDate(data) {
  const series = data.dust_by_date;
  const dailyTrace = {
    x: series.date,
    y: series.total_dust,
    type: "scatter",
    mode: "lines+markers",
    name: "Daily total",
    line: { width: 1, color: cssVar("--text-muted") },
    marker: { size: 4, color: cssVar("--text-muted") },
    opacity: 0.6,
  };
  const rollingTrace = {
    x: series.date,
    y: rollingAverage(series.total_dust, ROLLING_AVG_WINDOW),
    type: "scatter",
    mode: "lines",
    name: `${ROLLING_AVG_WINDOW}-day rolling avg`,
    line: { width: 3, color: cssVar("--series-climb") },
  };
  const layout = baseLayout("Date", "Total dust ingested (g)");
  layout.xaxis.type = "category";
  layout.xaxis.tickangle = -45;
  layout.xaxis.nticks = 20;
  layout.margin.b = 70;
  Plotly.react("chart-by-date", [dailyTrace, rollingTrace], layout, PLOTLY_CONFIG);
  ensureChartCard("chart-by-date", {
    title: "Total dust ingested by date",
    exportName: "dust_by_date",
    infoText: `Sum of CoreDustIngested_g across all matching flights and timesteps, grouped by calendar date (thin muted line), plus a ${ROLLING_AVG_WINDOW}-day rolling average (bold line) to make trends and sustained high-dust periods easier to read than day-to-day noise alone.`,
  });
}

function renderByHour(data) {
  const series = data.dust_by_hour;
  const trace = {
    x: series.hour.map(h => `${String(h).padStart(2, "0")}:00`),
    y: series.mean_dust,
    type: "bar",
    marker: { color: cssVar("--series-climb") },
  };
  const layout = baseLayout("Takeoff hour of day", "Mean dust ingested per timestep (g)");
  layout.showlegend = false;
  layout.xaxis.type = "category";
  Plotly.react("chart-by-hour", [trace], layout, PLOTLY_CONFIG);
  ensureChartCard("chart-by-hour", {
    title: "Dust ingested vs takeoff hour of day",
    exportName: "dust_by_takeoff_hour",
    infoText: "Mean per-timestep CoreDustIngested_g across all matching flights, grouped by the flight's recorded takeoff hour (local recorded time, from the filename).",
  });
}

function renderByRoute(data) {
  const routes = data.dust_by_route.slice().reverse();
  const trace = {
    x: routes.map(r => r.total_dust),
    y: routes.map(r => r.route),
    type: "bar",
    orientation: "h",
    marker: { color: cssVar("--series-climb") },
    hovertemplate: "%{y}<br>Total dust: %{x:.3g} g<extra></extra>",
  };
  const layout = baseLayout("Total dust ingested (g)", "");
  layout.showlegend = false;
  layout.margin.l = 90;
  Plotly.react("chart-by-route", [trace], layout, PLOTLY_CONFIG);
  ensureChartCard("chart-by-route", {
    title: "Total dust ingested by route",
    exportName: "dust_by_route",
    infoText: "Sum of CoreDustIngested_g across all matching flights, grouped by origin→destination airport pair (ICAO codes, joined in from the aircraft-type Summary CSV). Top 15 routes by total dust shown.",
  });
}

function renderTopFlightsTable(data) {
  const rows = data.top_flights;
  const container = document.getElementById("top-flights-table");
  const orderLabel = flightRanking.order === "asc" ? "cleanest" : "dustiest";
  ensureInfoOnlyCard("top-flights-table", {
    title: "Flight ranking",
    infoText: `The ${flightRanking.limit} individual flights with the ${orderLabel} total CoreDustIngested_g summed across timesteps matching the current filters. Toggle sort order and count above the table.`,
  });
  if (rows.length === 0) {
    container.innerHTML = "<p class=\"hint\">No flights to show.</p>";
    return;
  }
  const columns = [
    { key: "date", label: "Date" },
    { key: "takeoff_hhmm", label: "Takeoff" },
    { key: "callsign", label: "Callsign" },
    { key: "registration", label: "Registration" },
    { key: "origin", label: "Origin" },
    { key: "destination", label: "Destination" },
    { key: "total_dust", label: "Total dust (g)" },
    { key: "n", label: "Timesteps" },
  ];
  const body = rows.map(r => `
    <tr>
      <td>${r.date}</td>
      <td>${r.takeoff_hhmm}</td>
      <td>${r.callsign ?? ""}</td>
      <td>${r.registration ?? ""}</td>
      <td>${r.origin ?? "-"}</td>
      <td>${r.destination ?? "-"}</td>
      <td class="num">${fmt(r.total_dust)}</td>
      <td class="num">${r.n.toLocaleString()}</td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table class="data-table">
      <thead><tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody>
    </table>
    <button class="link-btn" id="export-top-flights" style="margin-top:8px;">Export CSV</button>
  `;
  document.getElementById("export-top-flights").addEventListener("click", () => {
    exportTableAsCSV(rows, columns, "flight_ranking.csv");
  });
}

function renderAll(data) {
  const emptyState = document.getElementById("empty-state");
  const chartGrid = document.getElementById("chart-grid");
  renderStatCards(data);
  if (data.n_flights === 0) {
    emptyState.hidden = false;
    chartGrid.style.display = "none";
    return;
  }
  emptyState.hidden = true;
  chartGrid.style.display = "grid";
  renderDustPerTime(data);
  renderCumulative(data);
  renderPdf(data);
  renderAltitude(data);
  renderBoxplot(data);
  renderByHour(data);
  renderByRoute(data);
  renderByDate(data);
  renderTopFlightsTable(data);
}

let lastAggregateFilters = null;
let lastAggregateData = null;

async function refresh() {
  const filters = currentFilters();
  if (filters.aircraft_types.length === 0 || filters.phases.length === 0 ||
      (filters.registrations !== null && filters.registrations.length === 0)) {
    lastAggregateFilters = filters;
    lastAggregateData = {
      n_flights: 0, n_rows: 0, total_dust_g: 0, mean_dust_per_row: 0, mean_dust_per_flight: 0,
    };
    renderAll(lastAggregateData);
    return;
  }
  const data = await fetchJSON("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  lastAggregateFilters = filters;
  lastAggregateData = data;
  renderAll(data);
}
const debouncedRefresh = debounce(refresh, 300);

function buildCheckboxes(containerId, values, checkedByDefault = true) {
  const container = document.getElementById(containerId);
  container.innerHTML = values.map(v => `
    <label>
      <input type="checkbox" value="${v}" ${checkedByDefault ? "checked" : ""}>
      ${v}
    </label>
  `).join("");
}

let defaults = null;

function applyDefaults() {
  buildCheckboxes("aircraft-checkboxes", defaults.aircraft_types, true);
  buildCheckboxes("phase-checkboxes", PHASES_ORDER, true);
  document.querySelectorAll("#aircraft-checkboxes input, #phase-checkboxes input").forEach(input => {
    input.addEventListener("change", debouncedRefresh);
  });
  document.getElementById("date-from").min = defaults.date_min;
  document.getElementById("date-from").max = defaults.date_max;
  document.getElementById("date-to").min = defaults.date_min;
  document.getElementById("date-to").max = defaults.date_max;
  document.getElementById("date-from").value = defaults.date_min;
  document.getElementById("date-to").value = defaults.date_max;
  document.getElementById("takeoff-from").value = "00:00";
  document.getElementById("takeoff-to").value = "23:30";
  document.getElementById("altitude-enable").checked = false;
  document.getElementById("altitude-min").value = 0;
  document.getElementById("altitude-max").value = 45000;
  document.getElementById("altitude-min").disabled = true;
  document.getElementById("altitude-max").disabled = true;

  document.getElementById("location-enable").checked = false;
  document.querySelectorAll("#location-fields input[type=checkbox]").forEach(i => { i.checked = false; });
  ["dep-icao", "arr-icao"].forEach(id => { document.getElementById(id).value = ""; });
  refreshCityCheckboxes("dep");
  refreshCityCheckboxes("arr");
  setLocationFieldsDisabled(true);

  document.getElementById("registration-enable").checked = false;
  document.getElementById("registration-search").value = "";
  document.getElementById("registration-search").disabled = true;
  document.getElementById("registration-select-all").disabled = true;
  document.getElementById("registration-deselect-all").disabled = true;
  buildRegistrationCheckboxes(defaults.registrations, true);
  document.querySelectorAll("#registration-checkboxes input").forEach(i => { i.disabled = true; });
}

// ---- location filter (departure / arrival airport, multi-select) ---------

let airports = [];

function checkedValues(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map(i => i.value);
}

function populateCheckboxGroup(containerId, items, { disabled = false } = {}) {
  const container = document.getElementById(containerId);
  if (items.length === 0) {
    container.innerHTML = `<p class="no-match">No matches</p>`;
    return;
  }
  container.innerHTML = items.map(({ value, label }) => `
    <label>
      <input type="checkbox" value="${value}" ${disabled ? "disabled" : ""}>
      ${label}
    </label>
  `).join("");
}

function setLocationFieldsDisabled(disabled) {
  document.querySelectorAll("#location-fields input").forEach(el => { el.disabled = disabled; });
}

function refreshCityCheckboxes(prefix) {
  const checkedCountries = checkedValues(`${prefix}-country-checkboxes`);
  const pool = checkedCountries.length
    ? airports.filter(a => checkedCountries.includes(a.country))
    : airports;
  const items = pool
    .slice()
    .sort((a, b) => a.city.localeCompare(b.city))
    .map(a => ({ value: a.icao, label: `${a.city} (${a.icao})` }));
  const locOn = document.getElementById("location-enable").checked;
  populateCheckboxGroup(`${prefix}-city-checkboxes`, items, { disabled: !locOn });
  document.querySelectorAll(`#${prefix}-city-checkboxes input`).forEach(i => {
    i.addEventListener("change", debouncedRefresh);
  });
}

function setupLocationBlock(prefix) {
  const countries = [...new Set(airports.map(a => a.country))].sort()
    .map(c => ({ value: c, label: c }));
  populateCheckboxGroup(`${prefix}-country-checkboxes`, countries, { disabled: true });
  document.querySelectorAll(`#${prefix}-country-checkboxes input`).forEach(i => {
    i.addEventListener("change", () => {
      refreshCityCheckboxes(prefix);
      debouncedRefresh();
    });
  });
  refreshCityCheckboxes(prefix);

  const icaoInput = document.getElementById(`${prefix}-icao`);
  icaoInput.addEventListener("input", debounce(() => {
    icaoInput.value = icaoInput.value.toUpperCase();
    debouncedRefresh();
  }, 400));
}

// A typed ICAO list (comma/space separated) wins outright; otherwise checked
// cities narrow within checked countries; otherwise checked countries alone
// match any airport in them.
function locationSelectionIcaos(prefix) {
  const typed = document.getElementById(`${prefix}-icao`).value
    .split(/[,\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
  if (typed.length > 0) return typed;

  const checkedCities = checkedValues(`${prefix}-city-checkboxes`);
  if (checkedCities.length > 0) return checkedCities;

  const checkedCountries = checkedValues(`${prefix}-country-checkboxes`);
  if (checkedCountries.length > 0) {
    return airports.filter(a => checkedCountries.includes(a.country)).map(a => a.icao);
  }
  return null;
}

function setupLocationFilter() {
  setupLocationBlock("dep");
  setupLocationBlock("arr");
  const enableBox = document.getElementById("location-enable");
  enableBox.addEventListener("change", () => {
    setLocationFieldsDisabled(!enableBox.checked);
    debouncedRefresh();
  });
}

// ---- registration filter (searchable multi-select) ------------------------

function buildRegistrationCheckboxes(registrations, checkedByDefault) {
  const container = document.getElementById("registration-checkboxes");
  container.innerHTML = registrations.map(r => `
    <label>
      <input type="checkbox" value="${r}" ${checkedByDefault ? "checked" : ""}>
      ${r}
    </label>
  `).join("");
  container.querySelectorAll("input").forEach(i => {
    i.addEventListener("change", debouncedRefresh);
  });
}

function activeRegistrations() {
  return checkedValues("registration-checkboxes");
}
function registrationFilterEnabled() {
  return document.getElementById("registration-enable").checked;
}

function setRegistrationCheckedState(checked) {
  // Only affects registrations currently visible under the search filter.
  document.querySelectorAll("#registration-checkboxes label").forEach(label => {
    if (label.hidden) return;
    label.querySelector("input").checked = checked;
  });
  debouncedRefresh();
}

function setupRegistrationFilter() {
  const enableBox = document.getElementById("registration-enable");
  const searchInput = document.getElementById("registration-search");
  const selectAllBtn = document.getElementById("registration-select-all");
  const deselectAllBtn = document.getElementById("registration-deselect-all");

  enableBox.addEventListener("change", () => {
    const on = enableBox.checked;
    searchInput.disabled = !on;
    selectAllBtn.disabled = !on;
    deselectAllBtn.disabled = !on;
    document.querySelectorAll("#registration-checkboxes input").forEach(i => { i.disabled = !on; });
    debouncedRefresh();
  });

  searchInput.addEventListener("input", () => {
    const q = searchInput.value.trim().toUpperCase();
    document.querySelectorAll("#registration-checkboxes label").forEach(label => {
      const reg = label.querySelector("input").value.toUpperCase();
      label.hidden = q.length > 0 && !reg.includes(q);
    });
  });

  selectAllBtn.addEventListener("click", () => setRegistrationCheckedState(true));
  deselectAllBtn.addEventListener("click", () => setRegistrationCheckedState(false));
}

// =========================================================================
// SINGLE FLIGHT VIEW
// =========================================================================

function singleActiveAircraft() {
  return Array.from(document.querySelectorAll("#single-aircraft-checkboxes input:checked")).map(i => i.value);
}

async function loadFlightsForDate() {
  const date = document.getElementById("single-date").value;
  const select = document.getElementById("single-flight-select");
  if (!date) return;
  const actypes = singleActiveAircraft();
  const flights = await fetchJSON(
    `/api/flights_on_date?date=${encodeURIComponent(date)}&aircraft_types=${encodeURIComponent(actypes.join(","))}`
  );
  if (flights.length === 0) {
    select.innerHTML = `<option value="">No flights on this date</option>`;
    showSingleFlightEmpty();
    return;
  }
  select.innerHTML = [`<option value="">Choose a flight… (${flights.length} found)</option>`]
    .concat(flights.map(f => {
      const route = (f.origin_icao && f.destination_icao) ? `${f.origin_icao}→${f.destination_icao}` : "route unknown";
      return `<option value="${f.flight_id}">${f.takeoff_hhmm} ${f.callsign || ""} (${route})</option>`;
    }))
    .join("");
}

function showSingleFlightEmpty() {
  document.getElementById("single-empty-state").hidden = false;
  document.getElementById("single-flight-panel").hidden = true;
}

function flightRouteString(flight) {
  return (flight.origin_icao && flight.destination_icao)
    ? `${flight.origin_icao}→${flight.destination_icao}`
    : "route unknown";
}
function flightLabel(flight) {
  const who = flight.callsign || flight.registration || flight.flight_id;
  return `${who} · departed ${flight.takeoff_hhmm} · ${flightRouteString(flight)}`;
}

function renderFlightMap(flight) {
  const trace = flight.trace;
  const traces = phaseSegmentTraces(trace.lon, trace.lat, trace.phase, { mode: "lines" }).map(t => ({
    ...t,
    lon: t.x, lat: t.y, x: undefined, y: undefined, type: "scattergeo",
  }));
  const n = trace.lat.length;
  if (n > 0) {
    traces.push({
      type: "scattergeo", mode: "markers+text",
      lon: [trace.lon[0]], lat: [trace.lat[0]],
      marker: { size: 10, color: cssVar("--series-cruise") },
      text: [flight.origin_icao || "Departure"], textposition: "top center",
      name: "Departure", showlegend: false,
    });
    traces.push({
      type: "scattergeo", mode: "markers+text",
      lon: [trace.lon[n - 1]], lat: [trace.lat[n - 1]],
      marker: { size: 10, color: cssVar("--series-descent") },
      text: [flight.destination_icao || "Arrival"], textposition: "bottom center",
      name: "Arrival", showlegend: false,
    });
  }
  const layout = baseLayout("", "");
  layout.geo = {
    projection: { type: "natural earth" },
    showland: true, landcolor: cssVar("--gridline"),
    showocean: true, oceancolor: cssVar("--page"),
    showcountries: true, countrycolor: cssVar("--baseline"),
    bgcolor: "transparent",
    fitbounds: "locations",
  };
  layout.paper_bgcolor = "transparent";
  Plotly.react("chart-flight-map", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-flight-map", {
    title: `Flight path: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_path`,
    infoText: "Raw Lat/Lon trace for this flight (columns D/E), segmented and colored by recorded flight phase. Departure/arrival markers are this flight's first and last recorded points, labelled with the origin/destination ICAO codes from the Summary CSV.",
  });
}

// =========================================================================
// DUST SOURCE TABS (Dust source attribution + Compare methods)
// =========================================================================

// Region-wide, not per-flight -- fetched once and shared by both dust-source
// tabs instead of re-requesting it on every flight change.
let susceptibilityPromise = null;
function fetchSusceptibility() {
  if (!susceptibilityPromise) susceptibilityPromise = fetchJSON("/api/dust_susceptibility");
  return susceptibilityPromise;
}

const SUSCEPTIBILITY_LAYER_LABELS = {
  sand_gt_50pct: "Sand content > 50% (ISRIC SoilGrids)",
  worldcover_bare_sparse: "Bare/sparse vegetation (ESA WorldCover)",
  arenosols: "Arenosols probability > 25% (ISRIC WRB)",
  fluvisols: "Fluvisols probability > 25% (ISRIC WRB)",
  solonchaks: "Solonchaks probability > 25% (ISRIC WRB)",
};

// Reference lines drawn AFTER the heatmap (later in the traces array =
// on top in Plotly) -- a heatmap cell is an opaque fill even at its
// "zero" color, so lines listed before it would just be painted over.
// Always shown here (unlike the main map's borders, which has its own
// toggle) since the user just wants geographic context on these, not a
// configurable option.
function regionReferenceTraces(coastlines, borders) {
  const traces = [];
  if (coastlines?.lon?.length) {
    traces.push({
      type: "scatter", mode: "lines", x: coastlines.lon, y: coastlines.lat,
      line: { width: 1, color: ink("muted") }, opacity: 0.7,
      hoverinfo: "skip", name: "Coastline", showlegend: false,
    });
  }
  if (borders?.lon?.length) {
    traces.push({
      type: "scatter", mode: "lines", x: borders.lon, y: borders.lat,
      line: { width: 1, color: ink("muted"), dash: "dot" }, opacity: 0.6,
      hoverinfo: "skip", name: "Country border", showlegend: false,
    });
  }
  return traces;
}

// Region-wide, independent of which flight is selected -- rendered once
// when the susceptibility grid loads, not on every flight change.
function renderSusceptibilityLayers(data, coastlines, borders) {
  if (!data || !data.layer_grids) return;
  for (const [layerId, grid] of Object.entries(data.layer_grids)) {
    const chartId = `chart-layer-${layerId}`;
    if (!document.getElementById(chartId)) continue;
    const label = SUSCEPTIBILITY_LAYER_LABELS[layerId] || layerId;
    const trace = {
      type: "heatmap", x: data.lon, y: data.lat, z: grid,
      colorscale: [[0, cssVar("--surface-1")], [1, cssVar("--series-descent")]],
      zmin: 0, zmax: 1, showscale: false,
      hovertemplate: "lon %{x:.2f}, lat %{y:.2f}<br>flagged: %{z}<extra></extra>",
      name: label, showlegend: false,
    };
    const layout = baseLayout("Longitude", "Latitude");
    layout.yaxis.scaleanchor = "x";
    layout.margin = { l: 44, r: 8, t: 4, b: 32 };
    Plotly.react(chartId, [trace, ...regionReferenceTraces(coastlines, borders)], layout, PLOTLY_CONFIG);
    ensureChartCard(chartId, {
      title: label,
      exportName: `susceptibility_${layerId}`,
      infoText: "One of the 5 independent inputs to the source susceptibility vote used above. Highlighted " +
        "cells are where this one dataset alone flags the area as a plausible dust source, regardless of " +
        "what the other 4 layers say -- see how much they agree or disagree by toggling layers on and off.",
    });
  }
}

function wireSusceptibilityLayerToggles() {
  document.querySelectorAll(".ds-layer-toggle").forEach(cb => {
    cb.addEventListener("change", () => {
      const card = document.querySelector(`[data-layer-card="${cb.dataset.layer}"]`);
      if (card) card.hidden = !cb.checked;
    });
  });
}

// Which of the 5 layers to gate by, and how many of THOSE need to agree --
// read fresh on every render rather than cached, so toggling a checkbox
// takes effect immediately via the normal renderChart() path.
function gateSelection() {
  const layers = Array.from(document.querySelectorAll(".ds-gate-layer:checked")).map(cb => cb.value);
  const mode = document.getElementById("ds-gate-mode").value;
  return { layers, mode };
}

const GATE_MODE_PHRASES = {
  any: "none of them flag it",
  majority: "fewer than a majority of them flag it",
  all: "at least one of them doesn't flag it",
};
function gateInfoText({ layers, mode }) {
  if (layers.length === 0) return "no layers are currently selected below, so nothing";
  const names = layers.map(id => SUSCEPTIBILITY_LAYER_LABELS[id] || id).join(", ");
  return `cells where, among the ${layers.length} selected layer(s) (${names}), ${GATE_MODE_PHRASES[mode]} as a plausible source`;
}

// Zeroes out density cells whose nearest susceptibility grid point doesn't
// meet the chosen agreement rule among the chosen layers -- the stricter
// reading of the HYSPLIT result: only the modeled source terrain that also
// has independent soil/land-cover support. susceptibility is a coarser
// (0.25deg) uniform grid than the density grid, so nearest-neighbor lookup
// via direct index arithmetic is exact rather than approximate. Sums the
// individual per-layer 0/1 grids for just the selected layers, rather than
// using the backend's precomputed all-5-layer tier grid, so any subset
// works, not just the fixed default.
function gateDensityBySusceptibility(density, densityLon, densityLat, susceptibility, selection) {
  const { lon: subLon, lat: subLat, layer_grids } = susceptibility;
  const { layers, mode } = selection;
  const grids = layers.map(id => layer_grids[id]).filter(Boolean);
  const lonStep = subLon[1] - subLon[0], latStep = subLat[1] - subLat[0];
  const clamp = (v, n) => Math.max(0, Math.min(n - 1, v));
  // "majority" of zero selected layers can never pass -- treat as "no gate"
  // (everything passes) rather than zeroing the whole map out silently.
  if (grids.length === 0) return density;
  const threshold = mode === "all" ? grids.length : mode === "any" ? 1 : Math.floor(grids.length / 2) + 1;
  return density.map((row, i) => {
    const si = clamp(Math.round((densityLat[i] - subLat[0]) / latStep), subLat.length);
    return row.map((v, j) => {
      const sj = clamp(Math.round((densityLon[j] - subLon[0]) / lonStep), subLon.length);
      const votes = grids.reduce((sum, g) => sum + (g[si][sj] ? 1 : 0), 0);
      return votes >= threshold ? v : 0;
    });
  });
}

// World-map reference for the Cartesian lon/lat contour charts below -- same
// arlmap coastline data the offline matplotlib tool draws with (see
// density_utils.plot_coastlines), served pre-filtered to the relevant box by
// /api/coastlines so the payload stays tiny.
function coastlineTrace(coastlines) {
  if (!coastlines || !coastlines.lon || coastlines.lon.length === 0) return null;
  return {
    type: "scatter", mode: "lines", x: coastlines.lon, y: coastlines.lat,
    line: { width: 1, color: ink("muted") }, opacity: 0.7,
    hoverinfo: "skip", name: "Coastline", showlegend: false,
  };
}
async function fetchCoastlinesForBbox(lonArr, latArr) {
  const params = new URLSearchParams({
    lon_min: Math.min(...lonArr), lon_max: Math.max(...lonArr),
    lat_min: Math.min(...latArr), lat_max: Math.max(...latArr),
  });
  return fetchJSON(`/api/coastlines?${params}`);
}

// Political (country) border lines -- separate from coastlineTrace() above
// because HYSPLIT's own arlmap basemap only carries coastlines, not
// borders; served from Natural Earth data by /api/borders (see app.py).
// Dashed and a different color from the coastline so the two don't blend.
function bordersTrace(borders) {
  if (!borders || !borders.lon || borders.lon.length === 0) return null;
  return {
    type: "scatter", mode: "lines", x: borders.lon, y: borders.lat,
    line: { width: 1, color: ink("muted"), dash: "dot" }, opacity: 0.6,
    hoverinfo: "skip", name: "Country border", showlegend: false,
  };
}
async function fetchBordersForBbox(lonArr, latArr) {
  const params = new URLSearchParams({
    lon_min: Math.min(...lonArr), lon_max: Math.max(...lonArr),
    lat_min: Math.min(...latArr), lat_max: Math.max(...latArr),
  });
  return fetchJSON(`/api/borders?${params}`);
}
// True-color satellite background for the source-attribution map, via NASA
// GIBS's public Worldview Snapshots API (no account/key needed). Returns a
// plain image URL -- the browser loads it directly as a Plotly layout image,
// no backend fetch involved. VIIRS SNPP true-color has full daily global
// coverage back to 2012, well before this dataset starts.
const LAND_IMAGERY_LAYER = "VIIRS_SNPP_CorrectedReflectance_TrueColor";
// Greyscale hillshade from ASTER's global elevation model -- unlike the
// true-color layer this isn't date-dependent (terrain doesn't change day to
// day), but GIBS's snapshot API still requires a TIME param, so the flight's
// own date is passed through anyway; any date returns the same image.
const TERRAIN_LAYER = "ASTER_GDEM_Greyscale_Shaded_Relief";
// MODIS daytime land surface temperature (not air temperature) -- hot bare
// sand/rock reads yellow/orange, cooler vegetated or wet ground reads green,
// white patches are missing data (cloud cover for that day's pass).
const LST_LAYER = "MODIS_Terra_Land_Surface_Temp_Day";
// Standard Inferno stops, but faded to fully transparent at the low end
// instead of a solid low-end color -- a plain "Inferno" string colorscale
// paints every zero-density cell too, so the density grid's full rectangular
// domain shows up as a flat colored box even where there's no real source
// signal (worse still with land imagery on, since a flat trace opacity would
// dim that whole box into a wash over the image instead of a clean overlay).
// Fading only the near-zero cells to alpha 0 leaves the actual plume fully
// opaque while everything else -- imagery or plain page background -- shows
// through untouched.
const INFERNO_ALPHA_COLORSCALE = [
  [0, "rgba(0,0,4,0)"], [0.15, "rgba(40,11,84,0.55)"], [0.3, "rgba(101,21,110,0.75)"],
  [0.45, "rgba(159,42,99,0.85)"], [0.6, "rgba(212,72,66,0.92)"], [0.75, "rgba(245,125,21,0.96)"],
  [0.9, "rgba(250,193,39,1)"], [1, "rgba(252,255,164,1)"],
];
function gibsSnapshotUrl(layer, dateStr, lonMin, lonMax, latMin, latMax) {
  const params = new URLSearchParams({
    REQUEST: "GetSnapshot",
    LAYERS: layer,
    CRS: "EPSG:4326",
    TIME: dateStr,
    WRAP: "day",
    // GIBS bbox order is south,west,north,east -- not the lon/lat-min/max
    // order used everywhere else in this file, easy to get backwards.
    BBOX: `${latMin},${lonMin},${latMax},${lonMax}`,
    FORMAT: "image/jpeg",
    WIDTH: "1000",
    HEIGHT: "1000",
  });
  return `https://wvs.earthdata.nasa.gov/api/v1/snapshot?${params}`;
}

// Prefers whichever source grid(s) are actually computed (they're already
// padded to cover the full trajectory extent); falls back to the flight's
// own recorded path so a map still shows before anything's been computed.
// Must include susceptibility's extent too -- it's a much wider, region-wide
// grid, and when it's toggled on the plot's axis autoranges to fit it, so a
// bbox limited to just the flight's own small density grid leaves the
// coastline reference covering only a fraction of what's actually visible.
function sourceGridBbox(flight, density, susceptibility) {
  const lons = [], lats = [];
  if (density && density.computed) { lons.push(...density.lon); lats.push(...density.lat); }
  if (susceptibility) { lons.push(...susceptibility.lon); lats.push(...susceptibility.lat); }
  if (lons.length === 0) { lons.push(...flight.trace.lon); lats.push(...flight.trace.lat); }
  return [lons, lats];
}

function pickerActiveAircraft(prefix) {
  return Array.from(document.querySelectorAll(`#${prefix}-aircraft-checkboxes input:checked`)).map(i => i.value);
}
function showFlightPickerEmpty(prefix) {
  document.getElementById(`${prefix}-empty-state`).hidden = false;
  document.getElementById(`${prefix}-flight-panel`).hidden = true;
}

// Shared aircraft/date/flight picker wiring -- identical across every
// dust-source-style tab; only what happens on a flight selection differs,
// so that's the one thing the caller provides.
function wireFlightPicker(prefix, loadFlight) {
  async function loadFlightsForDate() {
    const date = document.getElementById(`${prefix}-date`).value;
    const select = document.getElementById(`${prefix}-flight-select`);
    if (!date) return;
    try {
      const actypes = pickerActiveAircraft(prefix);
      const flights = await fetchJSON(
        `/api/flights_on_date?date=${encodeURIComponent(date)}&aircraft_types=${encodeURIComponent(actypes.join(","))}`
      );
      if (flights.length === 0) {
        select.innerHTML = `<option value="">No flights on this date</option>`;
        showFlightPickerEmpty(prefix);
        return;
      }
      select.innerHTML = [`<option value="">Choose a flight… (${flights.length} found)</option>`]
        .concat(flights.map(f => {
          const route = (f.origin_icao && f.destination_icao) ? `${f.origin_icao}→${f.destination_icao}` : "route unknown";
          return `<option value="${f.flight_id}">${f.takeoff_hhmm} ${f.callsign || ""} (${route})</option>`;
        }))
        .join("");
    } catch (err) {
      console.error(`${prefix}: failed to load flights for ${date}`, err);
      select.innerHTML = `<option value="">Failed to load flights, see console, then try again</option>`;
      showFlightPickerEmpty(prefix);
    }
  }

  buildCheckboxes(`${prefix}-aircraft-checkboxes`, defaults.aircraft_types, true);
  document.querySelectorAll(`#${prefix}-aircraft-checkboxes input`).forEach(input => {
    input.addEventListener("change", loadFlightsForDate);
  });
  const dateInput = document.getElementById(`${prefix}-date`);
  dateInput.min = defaults.date_min;
  dateInput.max = defaults.date_max;
  dateInput.value = defaults.date_min;
  dateInput.addEventListener("change", loadFlightsForDate);
  document.getElementById(`${prefix}-flight-select`).addEventListener("change", (e) => {
    loadFlight(e.target.value);
  });
  loadFlightsForDate();
}

// ---- "Compute dust source" button: launches flight_backtrack.py for the
// selected flight and polls until it's done, instead of requiring a
// terminal on the HYSPLIT machine. Only one job runs system-wide at a time
// (enforced server-side) -- see /api/flight/{flight_id}/compute in app.py.
function formatElapsed(s) {
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}
function formatProgress(data) {
  if (!data.point || !data.total_points) return "starting…";
  if (data.percent == null) return `point ${data.point} of ${data.total_points}`;
  return `point ${data.point} of ${data.total_points}, ${data.percent.toFixed(0)}% complete ` +
    `(each point is its own separate HYSPLIT run, so this resets to 0% at the start of every point, that's normal, not stuck)`;
}

function wireComputeJob({ getFlightId, getJob, getExtraParams, panelId, btnId, statusId, onDone }) {
  const panel = document.getElementById(panelId);
  const btn = document.getElementById(btnId);
  const status = document.getElementById(statusId);
  let pollTimer = null;

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  function reset() {
    stopPolling();
    btn.disabled = false;
    status.textContent = "";
  }

  async function poll() {
    const data = await fetchJSON("/api/compute_status");
    if (data.status === "running") {
      if (data.flight_id === getFlightId() && data.job === getJob()) {
        status.textContent = `Running, ${formatElapsed(data.elapsed_s)} elapsed. ${formatProgress(data)}`;
      }
      return; // keep polling regardless -- if it's someone else's job, wait for it to clear
    }
    stopPolling();
    if (data.status === "error" && data.flight_id === getFlightId() && data.job === getJob()) {
      status.textContent = `Failed: ${(data.log_tail || []).slice(-1)[0] || "see server log"}`;
      btn.disabled = false;
      return;
    }
    status.textContent = "Checking result…";
    await onDone();
    reset();
  }

  async function start() {
    btn.disabled = true;
    status.textContent = "Starting…";
    const params = new URLSearchParams({ job: getJob(), ...(getExtraParams ? getExtraParams() : {}) });
    const res = await fetchJSON(
      `/api/flight/${encodeURIComponent(getFlightId())}/compute?${params}`,
      { method: "POST" },
    );
    if (res.status === "busy") {
      status.textContent = `Busy computing ${res.running.flight_id} (${res.running.job}). Try again once that finishes.`;
      btn.disabled = false;
      return;
    }
    status.textContent = "Started. This can take a while.";
    stopPolling();
    pollTimer = setInterval(poll, 5000);
    poll();
  }

  btn.addEventListener("click", start);
  return { panel, reset };
}

const STRATEGY_LABELS = {
  topn: "top N peak points",
  trigger: "trigger-based real high-concentration events",
  dense: "dense, ~1 point/min along the route",
};
const SURROGATE_WARNING = "⚠ SURROGATE MODEL ESTIMATE, not a physical simulation: a statistical model trained " +
  "only on flights that already have a real HYSPLIT result, predicting the climatological/seasonal pattern those " +
  "imply -- it has not seen this flight's actual meteorology. Treat it as a fast, rough guide for exploration, " +
  "not as evidence on its own; cross-check against a real HYSPLIT run (or the MERRA-2/AOD corroboration panels) " +
  "before relying on it.";
const DENSITY_INFO = (data, isSurrogate) => {
  const prefix = isSurrogate ? SURROGATE_WARNING + " " : "";
  if (!data) {
    return prefix + (isSurrogate
      ? "Not yet estimated for this flight (or for this strategy)."
      : "HYSPLIT backward-trajectory source density. Not yet computed for this flight (or for this strategy).");
  }
  const engine = isSurrogate ? "Surrogate model" : "HYSPLIT backward-trajectory";
  return prefix + `${engine} source density: ${data.n_points_used} release points sampled along this ` +
    `flight's real trajectory (${STRATEGY_LABELS[data.strategy] || data.strategy} strategy), each weighted by ` +
    `modeled concentration × dust ingested at that point (${data.runtime_hours}h backward dispersion runs). ` +
    (isSurrogate ? "Generated instantly via surrogate_backtrack.py." : "Generated offline via flight_backtrack.py.");
};

// ---- MERRA-2 wind vectors (independent transport-direction cross-check) --

// Arrow per release point, pointing in the direction the wind is blowing
// TOWARD (opposite of the meteorological FROM-direction MERRA-2 reports),
// length scaled by speed. Longitude delta corrected by cos(lat) so arrows
// aren't visually stretched east-west away from the equator.
function windArrowAnnotations(points, fixedColor) {
  const maxSpeed = Math.max(1, ...points.map(p => p.speed_ms || 0));
  const maxLenDeg = 1.2;
  return points.filter(p => p.speed_ms != null).map(p => {
    const towardRad = ((p.direction_from_deg + 180) % 360) * Math.PI / 180;
    const lenDeg = (p.speed_ms / maxSpeed) * maxLenDeg;
    const dlat = lenDeg * Math.cos(towardRad);
    const dlon = lenDeg * Math.sin(towardRad) / Math.cos(p.lat * Math.PI / 180);
    const agree = p.wind_source_angle_diff_deg;
    // fixedColor lets the main HYSPLIT map draw these in one distinct color
    // (kept apart from the density/AOD/susceptibility palette already on
    // that chart), while the dedicated wind panel keeps its own
    // agreement-based coloring (teal/orange/red by angle diff).
    const color = fixedColor || (agree == null ? ink("muted")
      : agree < 45 ? cssVar("--series-cruise")
      : agree < 90 ? cssVar("--series-climb")
      : cssVar("--series-descent"));
    return {
      x: p.lon + dlon, y: p.lat + dlat, ax: p.lon, ay: p.lat,
      axref: "x", ayref: "y", xref: "x", yref: "y",
      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 2, arrowcolor: color,
    };
  });
}

// Straight-line back-trajectory using only the single wind vector already
// fetched at each release point (a "frozen field" approximation: assumes
// wind stays constant for the whole period, which real wind never does).
// Needs zero extra MERRA-2 lookups since it reuses wind_vectors data
// already fetched, unlike a real multi-step trajectory, which would need a
// fresh lookup at every step's new position and time and would be far too
// slow to compute live (the wind panel's own single lookup per point
// already costs tens of seconds).
const BACKTRAJ_HOURS = 24;
function frozenFieldBackTrajectory(point, hours) {
  const towardRad = ((point.direction_from_deg + 180) % 360) * Math.PI / 180;
  const distDeg = (point.speed_ms * 3600 * hours) / 111320;  // about 111.32km per degree latitude
  const dlat = distDeg * Math.cos(towardRad);
  const dlon = distDeg * Math.sin(towardRad) / Math.cos(point.lat * Math.PI / 180);
  return { lat: point.lat - dlat, lon: point.lon - dlon };
}
function backTrajectoryTraces(points, hours, color) {
  return points.filter(p => p.speed_ms != null).map((p, i) => {
    const back = frozenFieldBackTrajectory(p, hours);
    return {
      type: "scatter", mode: "lines", x: [p.lon, back.lon], y: [p.lat, back.lat],
      line: { width: 1.5, color, dash: "dot" },
      hovertemplate: `${p.point_label}: ${hours}h wind only back trajectory, straight line, constant wind assumed<extra></extra>`,
      name: "Wind-only back-trajectory", showlegend: i === 0,
    };
  });
}

// Wind vectors used to be their own separate chart; now they're drawn
// directly onto the main source-attribution map (see renderChart's
// windOverlayTraces/windOverlayAnnotations below) so this just keeps the
// status/uplift text under that map up to date -- no chart of its own.
function renderWindVectors(data) {
  const status = document.getElementById("ds-wind-status");
  if (!data.computed) {
    status.textContent = "Compute HYSPLIT source attribution for this flight first. Wind vectors are checked at its release points.";
  } else if (!data.configured) {
    status.textContent = `${data.reason} ${data.setup || ""}`;
  } else {
    const failed = data.points.filter(p => p.error);
    status.textContent = failed.length
      ? `${failed.length} of ${data.points.length} release point(s) failed their wind lookup, see console.`
      : "";
    if (failed.length) console.warn("wind vector per-point failures:", failed);
  }
}

// The wind-colored release-point markers + centroid star for the main map's
// wind overlay -- a colored variant of the plain "Release points" trace, so
// this REPLACES that trace rather than sitting on top of it (both would
// otherwise draw at the exact same coordinates).
function windOverlayTraces(density, windData) {
  const points = windData.points.filter(p => !p.error);
  return [
    {
      type: "scatter", mode: "markers", x: points.map(p => p.lon), y: points.map(p => p.lat),
      marker: {
        size: 9,
        color: points.map(p => p.wind_source_angle_diff_deg ?? null),
        colorscale: [[0, cssVar("--series-cruise")], [0.5, cssVar("--series-climb")], [1, cssVar("--series-descent")]],
        cmin: 0, cmax: 180,
        colorbar: {
          title: { text: "wind/source angle (°)", font: { color: ink("muted") } },
          tickfont: { color: ink("muted") }, x: 1.2, len: 0.9,
        },
        line: { width: 1, color: ink("primary") },
      },
      text: points.map(p => `${p.point_label}: wind ${fmt(p.speed_ms)} m/s from ${Math.round(p.direction_from_deg)}°` +
        ` @ ${Math.round(p.pressure_hpa)}hPa, source bearing ${Math.round(p.source_bearing_deg)}°,` +
        ` diff ${Math.round(p.wind_source_angle_diff_deg)}°`),
      hovertemplate: "%{text}<extra></extra>", name: "Release points (wind agreement)",
    },
    windData.centroid_lat != null ? {
      type: "scatter", mode: "markers", x: [windData.centroid_lon], y: [windData.centroid_lat],
      marker: { size: 14, symbol: "star", color: cssVar("--series-cruise"), line: { width: 1, color: ink("primary") } },
      name: "HYSPLIT source centroid", hovertemplate: "HYSPLIT source centroid<extra></extra>",
    } : null,
  ].filter(Boolean);
}

// Returns the fetched data (or null on failure) so the caller can cache it
// for a cheap re-render (e.g. toggling borders) without repeating this
// endpoint's real, ~30s-per-flight MERRA-2 network cost.
// isStale(): checked right before rendering, not just before caching --
// a previous version only guarded the currentWindData/renderChart() call
// at each call site, but rendered the status text and wind chart
// unconditionally in here regardless of whether the user had since
// switched flight or strategy. That let a stale "not computed" response
// from a strategy the user had already clicked away from land on top of
// the correct, already-computed result for whatever they're on now.
async function loadWindVectors(flight, strategy, method, coastlines, borders, isStale) {
  if (!flight) return null;
  // This is a live MERRA-2 reanalysis lookup, not a local computation --
  // routinely 30-60s. Without this, the map just keeps showing plain
  // (non-overlaid) release points that whole time with zero indication
  // anything is happening, which reads as "wind vectors broken", not "still
  // loading".
  document.getElementById("ds-wind-status").textContent =
    "Loading MERRA-2 wind vectors… (~30-60s, live reanalysis lookup)";
  try {
    const data = await fetchJSON(`/api/flight/${encodeURIComponent(flight.flight_id)}/wind_vectors?strategy=${strategy}&method=${method}`);
    if (isStale && isStale()) return null;
    renderWindVectors(data);
    return data;
  } catch (err) {
    if (isStale && isStale()) return null;
    console.error("wind_vectors fetch failed", err);
    document.getElementById("ds-wind-status").textContent = "Failed to load, see console.";
    return null;
  }
}

// ---- % agreement between HYSPLIT density and real MODIS AOD -------------

function pct(n) {
  return (n === null || n === undefined || Number.isNaN(n)) ? "-" : `${n.toFixed(1)}%`;
}

function renderAodAgreement(data) {
  const el = document.getElementById("ds-aod-agreement");
  if (!data.computed) {
    el.textContent = "";
    return;
  }
  if (!data.configured) {
    el.textContent = `AOD agreement: ${data.reason} ${data.setup || ""}`;
    return;
  }
  if (data.error) {
    el.textContent = `AOD agreement: ${data.error}`;
    return;
  }
  el.textContent = `AOD agreement (${data.date}): ${pct(data.jaccard_pct)} overlap (Jaccard) between HYSPLIT's ` +
    `top ${100 - data.density_percentile}% density region and MODIS cells with AOD ≥ ${data.aod_threshold}. ` +
    `${pct(data.pct_hysplit_in_aod)} of HYSPLIT's high-density area falls within elevated AOD, ` +
    `${pct(data.pct_aod_in_hysplit)} of the elevated-AOD area falls within HYSPLIT's high-density region.`;
}

async function loadAodAgreement(flight, strategy, method) {
  if (!flight) return;
  const el = document.getElementById("ds-aod-agreement");
  try {
    const data = await fetchJSON(`/api/flight/${encodeURIComponent(flight.flight_id)}/aod_agreement?strategy=${strategy}&method=${method}`);
    renderAodAgreement(data);
  } catch (err) {
    console.error("aod_agreement fetch failed", err);
    el.textContent = "AOD agreement: failed to load, see console.";
  }
}

function setupAttributionTab() {
  const prefix = "ds";
  const chartId = "chart-ds-source";
  let currentFlight = null;
  let currentDensity = null;
  let susceptibility = null;
  let coastlines = null;
  let borders = null;
  let currentWindData = null;

  fetchSusceptibility().then(async data => {
    susceptibility = data.computed ? data : null;
    renderChart();
    if (susceptibility) {
      // Region-wide extent (the susceptibility grid's own lon/lat), not
      // any one flight's -- these layer maps aren't tied to a flight.
      const [regionCoastlines, regionBorders] = await Promise.all([
        fetchCoastlinesForBbox(susceptibility.lon, susceptibility.lat),
        fetchBordersForBbox(susceptibility.lon, susceptibility.lat),
      ]);
      renderSusceptibilityLayers(susceptibility, regionCoastlines, regionBorders);
    }
  });
  wireSusceptibilityLayerToggles();

  // Not per-flight -- whether a trained model exists at all, so fetched
  // once. The surrogate radio stays disabled until this confirms one is
  // there (mirrors the MERRA-2/AOD panels' degrade-gracefully contract).
  const surrogateInput = document.getElementById("ds-method-surrogate-input");
  fetchJSON("/api/surrogate_status").then(status => {
    if (status.available) surrogateInput.disabled = false;
  });

  function currentStrategy() {
    const checked = document.querySelector('input[name="ds-strategy"]:checked');
    return checked ? checked.value : "topn";
  }

  function currentMethod() {
    const checked = document.querySelector('input[name="ds-method"]:checked');
    return checked ? checked.value : "hysplit";
  }

  // alt_min/alt_max are optional -- omitted entirely when blank, so the
  // backend serves the full, unfiltered density grid by default. When set,
  // the backend re-KDEs already-computed HYSPLIT output restricted to that
  // altitude band rather than requiring a new compute.
  function densityQueryParams() {
    const params = new URLSearchParams({ strategy: currentStrategy(), method: currentMethod() });
    const altMin = document.getElementById("ds-alt-min-input").value;
    const altMax = document.getElementById("ds-alt-max-input").value;
    if (altMin !== "") params.set("alt_min", altMin);
    if (altMax !== "") params.set("alt_max", altMax);
    return params;
  }

  function updateStrategyHint() {
    const pointsInput = document.getElementById("ds-points-input");
    const pointsLabel = document.getElementById("ds-points-label");
    const ignored = currentStrategy() === "trigger" || currentStrategy() === "dense";
    pointsInput.disabled = ignored;
    pointsLabel.style.opacity = ignored ? 0.5 : 1;

    document.getElementById("ds-strategy-hint").textContent = "";
  }

  async function loadDensityForStrategy() {
    currentDensity = await fetchJSON(
      `/api/flight/${encodeURIComponent(currentFlight.flight_id)}/dust_source_density?${densityQueryParams()}`,
    );
    renderChart();
  }

  // Overlays the OTHER method's result as outline contours on the SAME map,
  // computing it first (via its own compute-job panel, sharing the same
  // /api/flight/{id}/compute machinery the main Compute button uses) if it
  // isn't already there. Once both are in hand, logs the pair to
  // /api/surrogate/log_comparison so the Model performance tab's comparison
  // table picks it up -- see that endpoint's docstring for why it's a log
  // the user builds up here rather than a full filesystem scan.
  let otherDensity = null;
  const overlayToggle = document.getElementById("ds-overlay-toggle");
  const overlayLabel = document.getElementById("ds-overlay-label");

  function otherMethodOf() {
    return currentMethod() === "surrogate" ? "hysplit" : "surrogate";
  }

  const overlayCompute = wireComputeJob({
    getFlightId: () => currentFlight.flight_id,
    getJob: () => "density",
    getExtraParams: () => ({
      strategy: currentStrategy(),
      method: otherMethodOf(),
      hours: document.getElementById("ds-hours-input").value,
      points: document.getElementById("ds-points-input").value,
    }),
    panelId: "ds-overlay-compute-panel", btnId: "ds-overlay-compute-btn", statusId: "ds-overlay-compute-status",
    onDone: loadOverlay,
  });
  const overlayComputeMessage = document.getElementById("ds-overlay-compute-message");

  // Set once both grids are in hand and the backend has computed overlap
  // stats for them (see /api/surrogate/log_comparison) -- read by
  // overlayInfoText() below to show the same numbers the Model performance
  // tab's comparison table will end up showing for this flight.
  let overlapStats = null;

  async function logComparison() {
    try {
      overlapStats = await fetchJSON("/api/surrogate/log_comparison", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flight_id: currentFlight.flight_id, strategy: currentStrategy() }),
      });
    } catch (err) {
      overlapStats = null;
      console.error("failed to log surrogate comparison", err); // non-critical -- worst case it just won't show up in the Model performance table
    }
    renderChart(); // picks up overlapStats in the info text
  }

  async function loadOverlay() {
    otherDensity = null;
    overlapStats = null;
    overlayLabel.textContent = otherMethodOf() === "surrogate" ? "⚡ surrogate estimate" : "HYSPLIT";
    if (!overlayToggle.checked || !currentFlight || !currentDensity?.computed) {
      overlayCompute.panel.hidden = true;
      renderChart();
      return;
    }
    const other = otherMethodOf();
    const params = new URLSearchParams({ strategy: currentStrategy(), method: other });
    const result = await fetchJSON(
      `/api/flight/${encodeURIComponent(currentFlight.flight_id)}/dust_source_density?${params}`,
    );
    // Stale by the time this resolves (flight/strategy/method/toggle changed
    // again) -- don't clobber whatever's now current with an old answer.
    if (!overlayToggle.checked || currentDensity?.method !== currentMethod() || currentStrategy() !== params.get("strategy")) return;

    if (!result.computed) {
      overlayCompute.panel.hidden = false;
      overlayComputeMessage.textContent =
        `${overlayLabel.textContent} (${STRATEGY_LABELS[currentStrategy()]}) not yet computed for this flight.`;
      renderChart();
      return;
    }
    overlayCompute.panel.hidden = true;
    overlayCompute.reset();
    otherDensity = result;
    renderChart();
    logComparison();
  }
  overlayToggle.addEventListener("change", loadOverlay);

  function overlayInfoText() {
    if (!overlayToggle.checked || !otherDensity) return "";
    const statsText = overlapStats
      ? ` ${overlapStats.jaccard_pct.toFixed(0)}% Jaccard overlap of each method's top-${100 - overlapStats.percentile}% ` +
        `density region (${overlapStats.pct_hysplit_in_surrogate.toFixed(0)}% of HYSPLIT's high-density area is also ` +
        `flagged by the surrogate; ${overlapStats.pct_surrogate_in_hysplit.toFixed(0)}% the other way around).`
      : " Computing overlap stats…";
    return ` Overlaid (outline contours): ${overlayLabel.textContent} result for the same flight and strategy.` +
      `${statsText} Logged to the Model performance tab.`;
  }

  // Refreshes every strategy-dependent panel for whatever flight/strategy/
  // method is current -- shared by the strategy/method radio buttons AND by
  // a compute job finishing. A compute job's own onDone used to only call
  // loadDensityForStrategy(), so the MERRA-2/wind/AOD-agreement panels
  // kept showing whatever they last fetched (near-certainly a stale "not
  // computed yet" from before you clicked Compute) even after a fresh
  // result landed on the main map right above them.
  async function refreshStrategyPanels() {
    await loadDensityForStrategy();
    loadOverlay();
    const requestFlightId = currentFlight.flight_id, requestStrategy = currentStrategy(), requestMethod = currentMethod();
    loadWindVectors(
      currentFlight, requestStrategy, requestMethod, coastlines, borders,
      () => currentFlight?.flight_id !== requestFlightId || currentStrategy() !== requestStrategy || currentMethod() !== requestMethod,
    ).then(d => {
      if (d) { currentWindData = d; renderChart(); }
    });
    loadAodAgreement(currentFlight, currentStrategy(), currentMethod());
  }

  const compute = wireComputeJob({
    getFlightId: () => currentFlight.flight_id,
    getJob: () => "density",
    getExtraParams: () => ({
      strategy: currentStrategy(),
      method: currentMethod(),
      hours: document.getElementById("ds-hours-input").value,
      points: document.getElementById("ds-points-input").value,
    }),
    panelId: "ds-compute-panel", btnId: "ds-compute-btn", statusId: "ds-compute-status",
    onDone: refreshStrategyPanels,
  });
  const computeMessage = document.getElementById("ds-compute-message");

  function renderChart() {
    if (!currentFlight || !currentDensity) return;

    const isSurrogate = currentMethod() === "surrogate";
    const methodLabel = isSurrogate ? "⚡ surrogate estimate" : "HYSPLIT";
    const altSuffix = currentDensity?.filtered
      ? ` [${currentDensity.alt_min ?? "−∞"}–${currentDensity.alt_max ?? "∞"} ft]`
      : "";
    const title = `Dust source attribution (${methodLabel}, ${STRATEGY_LABELS[currentStrategy()]}): ${flightLabel(currentFlight)}${altSuffix}`;
    const exportName = `${currentFlight.flight_id}_dustsource_density_${currentStrategy()}_${currentMethod()}`;

    if (!currentDensity.computed) {
      compute.panel.hidden = false;
      computeMessage.textContent = isSurrogate
        ? `Surrogate estimate (${STRATEGY_LABELS[currentStrategy()]}) not yet computed for this flight -- instant, no HYSPLIT install needed.`
        : `HYSPLIT (${STRATEGY_LABELS[currentStrategy()]}) not yet computed for this flight.`;
      Plotly.react(
        chartId, [coastlineTrace(coastlines), bordersTrace(borders)].filter(Boolean),
        baseLayout("Longitude", "Latitude"), PLOTLY_CONFIG,
      );
      ensureChartCard(chartId, {
        title, exportName,
        infoText: DENSITY_INFO(null, isSurrogate),
      });
      return;
    }
    compute.panel.hidden = true;
    compute.reset();

    // Altitude band narrowed the release points down to under 2 -- the
    // backend can't fit a density estimate from that few, and returns no
    // grid at all (computed stays true; there just isn't a map this time).
    if (currentDensity.insufficient_data) {
      Plotly.react(
        chartId, [coastlineTrace(coastlines), bordersTrace(borders)].filter(Boolean),
        baseLayout("Longitude", "Latitude"), PLOTLY_CONFIG,
      );
      ensureChartCard(chartId, {
        title, exportName,
        infoText: `Only ${currentDensity.n_points_used} release point(s) fall in this altitude band -- too few ` +
          "to fit a density map from (needs at least 2). Widen the altitude range to see a result.",
      });
      return;
    }

    const gated = document.getElementById("ds-gate-toggle").checked && susceptibility;
    const densityZ = gated
      ? gateDensityBySusceptibility(currentDensity.density, currentDensity.lon, currentDensity.lat, susceptibility, gateSelection())
      : currentDensity.density;
    const windPoints = (currentWindData?.computed && currentWindData?.configured)
      ? currentWindData.points.filter(p => !p.error) : [];
    const showBacktraj = document.getElementById("ds-backtraj-toggle").checked && windPoints.length > 0;
    const BACKTRAJ_COLOR = cssVar("--series-level-descent");
    const showLandImagery = document.getElementById("ds-landimg-toggle").checked;
    const showTerrain = document.getElementById("ds-terrain-toggle").checked;
    const showLST = document.getElementById("ds-lst-toggle").checked;
    const showWindOverlay = document.getElementById("ds-wind-overlay-toggle").checked && windPoints.length > 0;
    const [bboxLons, bboxLats] = sourceGridBbox(currentFlight, currentDensity, susceptibility);
    const traces = [
      {
        type: "contour", x: currentDensity.lon, y: currentDensity.lat, z: densityZ,
        colorscale: INFERNO_ALPHA_COLORSCALE, contours: { coloring: "heatmap" },
        colorbar: {
          title: { text: "density", font: { color: ink("muted") } }, tickfont: { color: ink("muted") },
          x: showWindOverlay ? 1 : undefined, len: showWindOverlay ? 0.9 : undefined,
        },
        hovertemplate: "lon %{x:.2f}, lat %{y:.2f}<br>density %{z:.3g}<extra></extra>",
        name: gated ? "Source attribution (gated)" : "Source attribution", showlegend: false,
      },
      (overlayToggle.checked && otherDensity) ? {
        type: "contour", x: otherDensity.lon, y: otherDensity.lat, z: otherDensity.density,
        contours: { coloring: "lines", showlabels: false }, line: { color: cssVar("--series-descent"), width: 2 },
        showscale: false, hoverinfo: "skip", name: `${overlayLabel.textContent} (overlay)`,
      } : null,
      coastlineTrace(coastlines),
      bordersTrace(borders),
      {
        type: "scatter", mode: "lines", x: currentFlight.trace.lon, y: currentFlight.trace.lat,
        line: { width: 1.5, color: ink("primary") }, name: "Flight path",
      },
      ...(showWindOverlay ? windOverlayTraces(currentDensity, currentWindData) : [{
        type: "scatter", mode: "markers",
        x: currentDensity.release_points.map(p => p.lon), y: currentDensity.release_points.map(p => p.lat),
        marker: { size: 7, color: cssVar("--series-cruise"), line: { width: 1, color: ink("primary") } },
        text: currentDensity.release_points.map(p => `${p.point_label}: ${fmt(p.dust_ingested_g)} g at release`),
        hovertemplate: "%{text}<extra></extra>", name: "Release points",
      }]),
      ...(showBacktraj ? backTrajectoryTraces(windPoints, BACKTRAJ_HOURS, BACKTRAJ_COLOR) : []),
    ].filter(Boolean);
    const layout = baseLayout("Longitude", "Latitude");
    layout.yaxis.scaleanchor = "x";  // keep lon/lat aspect square, like the Streamlit tool's ax.set_aspect("equal")
    if (showWindOverlay) {
      // Room for the second (wind) colorbar past the density one -- default
      // margin is nowhere near wide enough for two side by side.
      layout.margin = { ...layout.margin, r: 140 };
      layout.annotations = windArrowAnnotations(windPoints);
    }
    // Land imagery and terrain relief are independent toggles but both draw
    // a background raster -- if both are on, terrain (checked second here)
    // ends up on top since neither has any transparency to blend through.
    if (showLandImagery || showTerrain) {
      const lonMin = Math.min(...bboxLons), lonMax = Math.max(...bboxLons);
      const latMin = Math.min(...bboxLats), latMax = Math.max(...bboxLats);
      const imageBase = {
        xref: "x", yref: "y",
        x: lonMin, y: latMax, sizex: lonMax - lonMin, sizey: latMax - latMin,
        xanchor: "left", yanchor: "top", sizing: "stretch", layer: "below",
      };
      layout.images = [
        showLandImagery
          ? { ...imageBase, source: gibsSnapshotUrl(LAND_IMAGERY_LAYER, currentFlight.date, lonMin, lonMax, latMin, latMax) }
          : null,
        showTerrain
          ? { ...imageBase, source: gibsSnapshotUrl(TERRAIN_LAYER, currentFlight.date, lonMin, lonMax, latMin, latMax) }
          : null,
      ].filter(Boolean);
      // Visible without opening Info -- a raster background otherwise
      // carries no on-chart label of its own for which layer/date it is.
      // Concat (not overwrite) -- the wind overlay above may have already
      // populated layout.annotations with its arrows.
      const bgLabel = [showLandImagery && "VIIRS true color", showTerrain && "ASTER GDEM shaded relief"]
        .filter(Boolean).join(" + ");
      layout.annotations = (layout.annotations || []).concat([{
        text: `${bgLabel}, ${currentFlight.date}`, xref: "paper", yref: "paper",
        x: 0.01, y: 0.99, xanchor: "left", yanchor: "top", showarrow: false,
        font: { size: 11, color: ink("muted") },
        bgcolor: cssVar("--surface-1"), bordercolor: cssVar("--border"), borderwidth: 1, borderpad: 4,
      }]);
    }
    Plotly.react(chartId, traces, layout, PLOTLY_CONFIG);
    ensureChartCard(chartId, {
      title, exportName,
      infoText: DENSITY_INFO(currentDensity, isSurrogate) +
        (document.getElementById("ds-gate-toggle").checked
          ? (susceptibility
            ? ` Susceptibility-gated: ${gateInfoText(gateSelection())} are zeroed out, leaving only HYSPLIT-modeled source terrain that also has independent susceptibility support.`
            : " Susceptibility-gated is checked, but the susceptibility grid hasn't been computed on this machine (run build_susceptibility_grid.py) -- showing the ungated result.")
          : "") +
        (showLandImagery
          ? ` Background: NASA VIIRS true-color imagery for ${currentFlight.date}, via NASA GIBS's public ` +
            "Worldview Snapshots API (no account needed) -- real satellite photography of the ground, not a " +
            "rendered basemap, so actual terrain (desert, vegetation, water, urban areas) under the modeled " +
            "source region is visible directly. Cloud cover on the day can obscure parts of the image; that's " +
            "a real gap in the satellite pass, not a rendering issue."
          : "") +
        (showTerrain
          ? " Background: ASTER GDEM greyscale shaded relief (hillshade), via the same NASA GIBS API -- " +
            "elevation, not a photo, so it shows terrain ruggedness (mountain ranges, wadis, escarpments) " +
            "directly, unobscured by cloud cover or vegetation. Not date-dependent (terrain doesn't change), " +
            "unlike the true-color imagery above."
          : "") +
        (showWindOverlay
          ? " Release points are colored by MERRA-2 wind vectors: an independent reanalysis's own wind field " +
            "(not derived from HYSPLIT, whose trajectories use separate, typically GDAS, met data) at each " +
            "point's real lat/lon/altitude/time. Color is the angle between that wind's FROM-direction and the " +
            "bearing from this point back to HYSPLIT's dust-weighted source centroid (star) -- teal means an " +
            "independent wind field agrees air was plausibly arriving from the same direction HYSPLIT's own " +
            "trajectory implies, red means it doesn't (see the wind/source angle colorbar). Arrows point the " +
            "direction the wind is blowing toward, length scaled by speed."
          : "") +
        (showBacktraj
          ? ` Wind-only back-trajectory: a straight line from each release point, ${BACKTRAJ_HOURS} hours ` +
            "upwind at that point's own MERRA-2 wind speed and direction, held constant. This is a simplified, " +
            "independent check computed with no extra data lookups, not a real multi-step trajectory. Real wind " +
            "changes over time and space, so the further the line runs, the less reliable the constant-wind " +
            "assumption gets. Compare where it points against HYSPLIT's own source region as a rough sanity check, " +
            "not a substitute for it."
          : "") +
        overlayInfoText(),
    });
  }

  async function loadFlight(flightId) {
    compute.reset();
    overlayCompute.reset();
    currentWindData = null;  // stale-cache guard -- don't let a toggle change mid-fetch re-render the previous flight's wind
    if (!flightId) { showFlightPickerEmpty(prefix); currentFlight = null; currentDensity = null; return; }
    try {
      const [flight, density, susceptibilityData] = await Promise.all([
        fetchJSON(`/api/flight/${encodeURIComponent(flightId)}`),
        fetchJSON(`/api/flight/${encodeURIComponent(flightId)}/dust_source_density?${densityQueryParams()}`),
        fetchSusceptibility(),
      ]);
      document.getElementById(`${prefix}-empty-state`).hidden = true;
      document.getElementById(`${prefix}-flight-panel`).hidden = false;
      currentFlight = flight;
      currentDensity = density;
      updateStrategyHint();
      // fetchSusceptibility() is cached, so this is cheap even though the
      // tab-level fetch at the top of setupAttributionTab already kicked it
      // off -- awaiting it here (rather than relying on that one alone) is
      // what guarantees its extent is actually available before the bbox
      // below is computed, instead of racing it.
      susceptibility = susceptibilityData.computed ? susceptibilityData : null;
      const bbox = sourceGridBbox(flight, density, susceptibility);
      [coastlines, borders] = await Promise.all([
        fetchCoastlinesForBbox(...bbox),
        fetchBordersForBbox(...bbox),
      ]);
      renderChart();
      loadOverlay();
      // Guard against a stale ~30s-slow MERRA-2 fetch for a PREVIOUS flight
      // resolving after the user has already switched to a new one: only
      // apply the result if this is still the flight actually selected
      // when it lands, otherwise silently discard it.
      loadWindVectors(
        currentFlight, currentStrategy(), currentMethod(), coastlines, borders,
        () => currentFlight?.flight_id !== flightId,
      ).then(d => {
        if (d) { currentWindData = d; renderChart(); }
      });
      loadAodAgreement(currentFlight, currentStrategy(), currentMethod());
    } catch (err) {
      console.error(`${prefix}: failed to load flight ${flightId}`, err);
      showFlightPickerEmpty(prefix);
    }
  }

  wireFlightPicker(prefix, loadFlight);
  const gateToggle = document.getElementById(`${prefix}-gate-toggle`);
  const gateSettings = document.getElementById(`${prefix}-gate-settings`);
  gateToggle.addEventListener("change", () => {
    gateSettings.hidden = !gateToggle.checked;
    renderChart();
  });
  document.querySelectorAll(".ds-gate-layer").forEach(cb => cb.addEventListener("change", renderChart));
  document.getElementById(`${prefix}-gate-mode`).addEventListener("change", renderChart);
  document.getElementById(`${prefix}-landimg-toggle`).addEventListener("change", renderChart);
  document.getElementById(`${prefix}-terrain-toggle`).addEventListener("change", renderChart);
  document.getElementById(`${prefix}-wind-overlay-toggle`).addEventListener("change", renderChart);
  document.getElementById(`${prefix}-backtraj-toggle`).addEventListener("change", renderChart);
  document.querySelectorAll('input[name="ds-strategy"]').forEach(input => {
    input.addEventListener("change", () => {
      compute.reset();
      overlayCompute.reset();
      updateStrategyHint();
      if (!currentFlight) return;
      refreshStrategyPanels();
    });
  });
  document.querySelectorAll('input[name="ds-method"]').forEach(input => {
    input.addEventListener("change", () => {
      compute.reset();
      overlayCompute.reset();
      if (!currentFlight) return;
      refreshStrategyPanels();
    });
  });
  // Altitude band re-fetches just the density map, not the full compute --
  // it re-KDEs already-computed HYSPLIT/surrogate output for a subset of
  // release points, so it doesn't touch MERRA-2/wind/AOD-agreement (those
  // work off the full release-point set regardless of this filter).
  const debouncedAltRefresh = debounce(() => { if (currentFlight) loadDensityForStrategy(); }, 400);
  document.getElementById("ds-alt-min-input").addEventListener("input", debouncedAltRefresh);
  document.getElementById("ds-alt-max-input").addEventListener("input", debouncedAltRefresh);
}

function renderFlightAltitude(flight) {
  const trace = flight.trace;
  const minutes = trace.t.map(s => s / 60);
  const traces = phaseSegmentTraces(minutes, trace.altitude_ft, trace.phase);
  Plotly.react("chart-flight-altitude", traces, baseLayout("Elapsed time (min)", "Altitude (ft)"), PLOTLY_CONFIG);
  ensureChartCard("chart-flight-altitude", {
    title: `Altitude profile: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_altitude`,
    infoText: "Raw Alt_ft (column F) for this flight vs elapsed time since its first non-UNKNOWN-phase row, segmented and colored by recorded flight phase.",
  });
}

function renderFlightDust(flight) {
  const trace = flight.trace;
  const minutes = trace.t.map(s => s / 60);
  let running = 0;
  const cumulative = trace.dust_g.map(v => (running += v));
  const traces = [
    {
      x: minutes, y: trace.dust_g, type: "scatter", mode: "lines",
      name: "Per timestep", line: { width: 1, color: ink("muted") },
    },
    {
      x: minutes, y: cumulative, type: "scatter", mode: "lines",
      name: "Cumulative", line: { width: 2, color: cssVar("--series-climb") },
    },
  ];
  const layout = baseLayout("Elapsed time (min)", "Dust ingested (g)");
  Plotly.react("chart-flight-dust", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-flight-dust", {
    title: `Dust ingested: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_dust`,
    infoText: "Raw CoreDustIngested_g (column R) per timestep for this flight (thin line), plus its running cumulative sum (bold line), both on the same axis since they share units (g).",
  });
}

function renderFlightPhaseTimeline(flight) {
  const trace = flight.trace;
  const intervals = phaseIntervals(trace.t, trace.phase);
  const seen = new Set();
  const traces = intervals.map(iv => {
    const showlegend = !seen.has(iv.phase);
    seen.add(iv.phase);
    return {
      x: [(iv.end - iv.start) / 60],
      y: ["Flight"],
      base: [iv.start / 60],
      type: "bar",
      orientation: "h",
      name: iv.phase,
      showlegend,
      marker: { color: phaseColor(iv.phase) },
      hovertemplate: `${iv.phase}<br>%{base:.1f}–%{x:.1f} min<extra></extra>`,
    };
  });
  const layout = baseLayout("Elapsed time (min)", "");
  layout.barmode = "stack";
  layout.margin.b = 44;
  layout.margin.l = 16;
  layout.showlegend = false;
  layout.yaxis.showticklabels = false;
  Plotly.react("chart-flight-phases", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-flight-phases", {
    title: `Phase timeline: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_phase_timeline`,
    infoText: "When each flight phase occurred, derived from contiguous runs of the phase column against elapsed time. A phase can appear more than once (e.g. DESCENT and LEVEL DESCENT often interleave). Colors match the phase legend shown on the altitude/dust/speed charts below.",
  });
}

function renderFlightSpeed(flight) {
  const trace = flight.trace;
  const minutes = trace.t.map(s => s / 60);
  const traces = phaseSegmentTraces(minutes, trace.tas_kn, trace.phase);
  Plotly.react("chart-flight-speed", traces, baseLayout("Elapsed time (min)", "True airspeed (kn)"), PLOTLY_CONFIG);
  ensureChartCard("chart-flight-speed", {
    title: `True airspeed: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_speed`,
    infoText: "Raw TAS_kn (true airspeed, knots) for this flight vs elapsed time, segmented and colored by recorded flight phase.",
  });
}

function renderFlightVerticalRate(flight) {
  const trace = flight.trace;
  const minutes = trace.t.map(s => s / 60);
  const traces = phaseSegmentTraces(minutes, trace.vertical_rate, trace.phase);
  const layout = baseLayout("Elapsed time (min)", "Vertical rate (ft/min)");
  layout.yaxis.zeroline = true;
  Plotly.react("chart-flight-vspeed", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-flight-vspeed", {
    title: `Vertical rate: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_vertical_rate`,
    infoText: "Raw vertical_rate (ft/min, positive = climbing) for this flight vs elapsed time, segmented and colored by recorded flight phase. Useful for seeing exactly how aggressively the aircraft climbed or descended.",
  });
}

function renderFlightDustByPhase(flight) {
  const trace = flight.trace;
  const totals = sumByPhase(trace.dust_g, trace.phase);
  const layout = baseLayout("", "Total dust ingested (g)");
  layout.showlegend = false;
  Plotly.react("chart-flight-dust-by-phase", [{
    x: totals.map(t => t.phase),
    y: totals.map(t => t.total),
    type: "bar",
    marker: { color: totals.map(t => phaseColor(t.phase)) },
    hovertemplate: "%{x}<br>%{y:.3g} g<extra></extra>",
  }], layout, PLOTLY_CONFIG);
  ensureChartCard("chart-flight-dust-by-phase", {
    title: `Dust ingested by phase: ${flightLabel(flight)}`,
    exportName: `${flight.flight_id}_dust_by_phase`,
    infoText: "Sum of CoreDustIngested_g for this flight, grouped by phase. Complements the aggregate per-phase box plot with this specific flight's totals.",
  });
}

function renderFlightInfo(flight) {
  const cards = [
    { label: "Flight", value: `${flight.callsign || "-"} / ${flight.registration || "-"}` },
    { label: "Date / takeoff", value: `${flight.date} ${flight.takeoff_hhmm}` },
    { label: "Route", value: flightRouteString(flight) },
    { label: "Total dust ingested (g)", value: fmt(flight.total_dust_g) },
    { label: "Usable timesteps", value: flight.n_rows.toLocaleString() },
  ];
  document.getElementById("single-flight-info").innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
    </div>
  `).join("");
}

async function loadFlightDetail(flightId) {
  if (!flightId) { showSingleFlightEmpty(); return; }
  const flight = await fetchJSON(`/api/flight/${encodeURIComponent(flightId)}`);
  document.getElementById("single-empty-state").hidden = true;
  document.getElementById("single-flight-panel").hidden = false;
  renderFlightInfo(flight);
  if (flight.trace.t.length > 0) {
    renderFlightMap(flight);
    renderFlightPhaseTimeline(flight);
    renderFlightAltitude(flight);
    renderFlightDust(flight);
    renderFlightSpeed(flight);
    renderFlightVerticalRate(flight);
    renderFlightDustByPhase(flight);
  }
}

function initSingleFlightView() {
  buildCheckboxes("single-aircraft-checkboxes", defaults.aircraft_types, true);
  document.querySelectorAll("#single-aircraft-checkboxes input").forEach(input => {
    input.addEventListener("change", loadFlightsForDate);
  });
  const dateInput = document.getElementById("single-date");
  dateInput.min = defaults.date_min;
  dateInput.max = defaults.date_max;
  dateInput.value = defaults.date_min;
  dateInput.addEventListener("change", loadFlightsForDate);
  document.getElementById("single-flight-select").addEventListener("change", (e) => {
    loadFlightDetail(e.target.value);
  });
  loadFlightsForDate();
}

// =========================================================================
// COMPARE FLIGHTS VIEW
// =========================================================================

const COMPARE_MIN_SLOTS = 2;
const COMPARE_MAX_SLOTS = 8;
const COMPARE_COLOR_VARS = [
  "--series-flight-1", "--series-flight-2", "--series-flight-3", "--series-flight-4",
  "--series-flight-5", "--series-flight-6", "--series-flight-7", "--series-flight-8",
];

let compareSlots = [];
let compareSlotCounter = 0;

function compareSlotColorVar(i) {
  return COMPARE_COLOR_VARS[i % COMPARE_COLOR_VARS.length];
}
function compareSlotColor(i) {
  return cssVar(compareSlotColorVar(i));
}
function compareShortLabel(flight) {
  return `Flight ${flight.colorIndex + 1}: ${flight.callsign || flight.registration || flight.flight_id}`;
}

function newCompareSlot() {
  return { key: compareSlotCounter++, date: defaults.date_min, flightId: "", flights: [] };
}

function compareActiveAircraft() {
  return Array.from(document.querySelectorAll("#compare-aircraft-checkboxes input:checked")).map(i => i.value);
}

async function loadCompareSlotFlights(key) {
  const slot = compareSlots.find(s => s.key === key);
  if (!slot || !slot.date) return;
  const actypes = compareActiveAircraft();
  slot.flights = await fetchJSON(
    `/api/flights_on_date?date=${encodeURIComponent(slot.date)}&aircraft_types=${encodeURIComponent(actypes.join(","))}`
  );
  if (!slot.flights.some(f => f.flight_id === slot.flightId)) slot.flightId = "";
  renderCompareSlots();
}

function addCompareSlot() {
  if (compareSlots.length >= COMPARE_MAX_SLOTS) return;
  const slot = newCompareSlot();
  compareSlots.push(slot);
  renderCompareSlots();
  loadCompareSlotFlights(slot.key);
}

function removeCompareSlot(key) {
  if (compareSlots.length <= COMPARE_MIN_SLOTS) return;
  compareSlots = compareSlots.filter(s => s.key !== key);
  renderCompareSlots();
  refreshComparison();
}

function renderCompareSlots() {
  const container = document.getElementById("compare-slots");
  container.innerHTML = compareSlots.map((slot, i) => {
    const options = slot.flights.length === 0
      ? `<option value="">No flights on this date</option>`
      : [`<option value="">Choose a flight…</option>`].concat(slot.flights.map(f => {
          const route = (f.origin_icao && f.destination_icao) ? `${f.origin_icao}→${f.destination_icao}` : "route unknown";
          const selected = f.flight_id === slot.flightId ? "selected" : "";
          return `<option value="${f.flight_id}" ${selected}>${f.takeoff_hhmm} ${f.callsign || ""} (${route})</option>`;
        })).join("");
    return `
      <div class="compare-slot" style="--slot-color: var(${compareSlotColorVar(i)})">
        <div class="compare-slot-header">
          <span class="compare-slot-label">Flight ${i + 1}</span>
          ${compareSlots.length > COMPARE_MIN_SLOTS ? `<button type="button" class="compare-slot-remove" data-key="${slot.key}">Remove</button>` : ""}
        </div>
        <input type="date" class="compare-slot-date" data-key="${slot.key}" min="${defaults.date_min}" max="${defaults.date_max}" value="${slot.date}">
        <select class="compare-slot-flight" data-key="${slot.key}">${options}</select>
      </div>
    `;
  }).join("");

  container.querySelectorAll(".compare-slot-remove").forEach(btn => {
    btn.addEventListener("click", () => removeCompareSlot(Number(btn.dataset.key)));
  });
  container.querySelectorAll(".compare-slot-date").forEach(input => {
    input.addEventListener("change", () => {
      const slot = compareSlots.find(s => s.key === Number(input.dataset.key));
      slot.date = input.value;
      slot.flightId = "";
      loadCompareSlotFlights(slot.key);
    });
  });
  container.querySelectorAll(".compare-slot-flight").forEach(select => {
    select.addEventListener("change", () => {
      const slot = compareSlots.find(s => s.key === Number(select.dataset.key));
      slot.flightId = select.value;
      refreshComparison();
    });
  });

  document.getElementById("compare-add-slot").disabled = compareSlots.length >= COMPARE_MAX_SLOTS;
}

function renderCompareLegend(flights) {
  document.getElementById("compare-legend").innerHTML = flights.map(f => `
    <div class="stat-card" style="border-left: 4px solid ${compareSlotColor(f.colorIndex)}">
      <div class="label">${compareShortLabel(f)}</div>
      <div class="value">${fmt(f.total_dust_g)} g</div>
    </div>
  `).join("");
}

function renderComparePhases(flights) {
  const traces = [];
  const seenPhases = new Set();
  flights.forEach(f => {
    const label = compareShortLabel(f);
    phaseIntervals(f.trace.t, f.trace.phase).forEach(iv => {
      const showlegend = !seenPhases.has(iv.phase);
      seenPhases.add(iv.phase);
      traces.push({
        x: [(iv.end - iv.start) / 60],
        y: [label],
        base: [iv.start / 60],
        type: "bar",
        orientation: "h",
        name: iv.phase,
        showlegend,
        marker: { color: phaseColor(iv.phase) },
        hovertemplate: `${iv.phase}<br>%{base:.1f}–%{x:.1f} min<extra></extra>`,
      });
    });
  });
  const layout = baseLayout("Elapsed time (min)", "");
  layout.barmode = "stack";
  layout.margin.l = 150;
  Plotly.react("chart-compare-phases", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-compare-phases", {
    title: "Phase timeline comparison",
    exportName: "flight_comparison_phases",
    infoText: "When each phase occurred for each selected flight (one row per flight), colored by phase so timing patterns are directly comparable across flights.",
  });
}

function renderCompareMetric(chartId, flights, { yKey, yTitle, title, infoText, exportName, cumulative = false }) {
  const traces = flights.map(f => {
    const minutes = f.trace.t.map(s => s / 60);
    let yvals = f.trace[yKey];
    if (cumulative) {
      let running = 0;
      yvals = yvals.map(v => (running += v));
    }
    return {
      x: minutes,
      y: yvals,
      type: "scatter",
      mode: "lines",
      name: compareShortLabel(f),
      line: { width: 2, color: compareSlotColor(f.colorIndex) },
    };
  });
  Plotly.react(chartId, traces, baseLayout("Elapsed time (min)", yTitle), PLOTLY_CONFIG);
  ensureChartCard(chartId, { title, exportName, infoText });
}

async function refreshComparison() {
  const active = compareSlots
    .map((s, i) => ({ ...s, colorIndex: i }))
    .filter(s => s.flightId);
  const emptyState = document.getElementById("compare-empty-state");
  const panel = document.getElementById("compare-panel");
  if (active.length < COMPARE_MIN_SLOTS) {
    emptyState.hidden = false;
    panel.hidden = true;
    return;
  }
  const flights = await Promise.all(active.map(s =>
    fetchJSON(`/api/flight/${encodeURIComponent(s.flightId)}`).then(f => ({ ...f, colorIndex: s.colorIndex }))
  ));
  emptyState.hidden = true;
  panel.hidden = false;
  renderCompareLegend(flights);
  renderComparePhases(flights);
  renderCompareMetric("chart-compare-altitude", flights, {
    yKey: "altitude_ft", yTitle: "Altitude (ft)", title: "Altitude comparison",
    exportName: "flight_comparison_altitude",
    infoText: "Raw Alt_ft per flight vs elapsed time since its first non-UNKNOWN-phase row, one line per flight.",
  });
  renderCompareMetric("chart-compare-dust", flights, {
    yKey: "dust_g", yTitle: "Dust ingested (g)", title: "Dust per timestep comparison",
    exportName: "flight_comparison_dust",
    infoText: "Raw CoreDustIngested_g per timestep per flight vs elapsed time, one line per flight.",
  });
  renderCompareMetric("chart-compare-cumulative", flights, {
    yKey: "dust_g", yTitle: "Cumulative dust ingested (g)", title: "Cumulative dust comparison",
    exportName: "flight_comparison_cumulative_dust", cumulative: true,
    infoText: "Running cumulative sum of CoreDustIngested_g per flight vs elapsed time, one line per flight.",
  });
  renderCompareMetric("chart-compare-speed", flights, {
    yKey: "tas_kn", yTitle: "True airspeed (kn)", title: "Airspeed comparison",
    exportName: "flight_comparison_speed",
    infoText: "Raw TAS_kn per flight vs elapsed time, one line per flight.",
  });
}

function setupCompareView() {
  buildCheckboxes("compare-aircraft-checkboxes", defaults.aircraft_types, true);
  document.querySelectorAll("#compare-aircraft-checkboxes input").forEach(input => {
    input.addEventListener("change", () => {
      compareSlots.forEach(s => loadCompareSlotFlights(s.key));
    });
  });
  compareSlots = [newCompareSlot(), newCompareSlot()];
  renderCompareSlots();
  compareSlots.forEach(s => loadCompareSlotFlights(s.key));
  document.getElementById("compare-add-slot").addEventListener("click", addCompareSlot);
}

// =========================================================================
// ROUTES VIEW
// =========================================================================

const ROUTES_TOP_N = 200; // effectively "all" -- only ~30-40 real pairs exist across 11 airports

function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}
function interpolateColor(hex1, hex2, t) {
  const c1 = hexToRgb(hex1), c2 = hexToRgb(hex2);
  const r = Math.round(c1.r + (c2.r - c1.r) * t);
  const g = Math.round(c1.g + (c2.g - c1.g) * t);
  const b = Math.round(c1.b + (c2.b - c1.b) * t);
  return `rgb(${r}, ${g}, ${b})`;
}
function routeIntensityColor(t) {
  // Sequential blue ramp, light -> dark, matching the dashboard's sequential hue.
  return interpolateColor("#9ec5f4", "#0d366b", t);
}

function airportByIcao(icao) {
  return airports.find(a => a.icao === icao);
}

function routesFilters() {
  return {
    aircraft_types: Array.from(document.querySelectorAll("#routes-aircraft-checkboxes input:checked")).map(i => i.value),
    date_from: document.getElementById("routes-date-from").value,
    date_to: document.getElementById("routes-date-to").value,
    takeoff_start_min: 0,
    takeoff_end_min: 1439,
    phases: PHASES_ORDER.slice(),
    top_n: ROUTES_TOP_N,
  };
}

function renderRoutesMap(routes) {
  const maxDust = Math.max(...routes.map(r => r.total_dust), 1e-9);
  const lineTraces = routes
    .filter(r => airportByIcao(r.origin) && airportByIcao(r.destination))
    .map(r => {
      const o = airportByIcao(r.origin);
      const d = airportByIcao(r.destination);
      const t = r.total_dust / maxDust;
      return {
        type: "scattergeo",
        mode: "lines",
        lon: [o.lon, d.lon],
        lat: [o.lat, d.lat],
        line: { width: 1.5 + t * 4, color: routeIntensityColor(t) },
        opacity: 0.85,
        name: r.route,
        hovertemplate: `${r.route}<br>Total dust: ${fmt(r.total_dust)} g<br>Flights: ${r.n_flights}<extra></extra>`,
        showlegend: false,
      };
    });

  const usedIcaos = new Set(routes.flatMap(r => [r.origin, r.destination]));
  const markerAirports = airports.filter(a => usedIcaos.has(a.icao) && a.lat != null);
  const markerTrace = {
    type: "scattergeo",
    mode: "markers+text",
    lon: markerAirports.map(a => a.lon),
    lat: markerAirports.map(a => a.lat),
    text: markerAirports.map(a => a.icao),
    textposition: "top center",
    marker: { size: 8, color: cssVar("--text-primary") },
    hovertemplate: markerAirports.map(a => `${a.icao}: ${a.city}, ${a.country}<extra></extra>`),
    showlegend: false,
  };

  const layout = baseLayout("", "");
  layout.geo = {
    projection: { type: "natural earth" },
    showland: true, landcolor: cssVar("--gridline"),
    showocean: true, oceancolor: cssVar("--page"),
    showcountries: true, countrycolor: cssVar("--baseline"),
    bgcolor: "transparent",
    fitbounds: "locations",
  };
  layout.paper_bgcolor = "transparent";
  Plotly.react("chart-routes-map", [...lineTraces, markerTrace], layout, PLOTLY_CONFIG);
  ensureChartCard("chart-routes-map", {
    title: "Routes by total dust ingested",
    exportName: "routes_map",
    infoText: "Every origin→destination route flown by the matching flights, drawn as a straight line between airports. Line color and thickness scale with that route's total CoreDustIngested_g (darker/thicker = more dust). Airport markers show every airport that appears in at least one matching route.",
  });
}

function renderRoutesTable(routes) {
  const container = document.getElementById("routes-table");
  ensureInfoOnlyCard("routes-table", {
    title: "All routes",
    infoText: "Every route flown by the matching flights, sorted by total dust ingested. Mirrors the map above in table form, plus mean dust per timestep and flight counts.",
  });
  if (routes.length === 0) {
    container.innerHTML = "<p class=\"hint\">No routes to show.</p>";
    return;
  }
  const columns = [
    { key: "route", label: "Route" },
    { key: "total_dust", label: "Total dust (g)" },
    { key: "mean_dust", label: "Mean dust / timestep (g)" },
    { key: "n_flights", label: "Flights" },
  ];
  const body = routes.map(r => `
    <tr>
      <td>${r.route}</td>
      <td class="num">${fmt(r.total_dust)}</td>
      <td class="num">${fmt(r.mean_dust)}</td>
      <td class="num">${r.n_flights.toLocaleString()}</td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table class="data-table">
      <thead><tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody>
    </table>
    <button class="link-btn" id="export-routes" style="margin-top:8px;">Export CSV</button>
  `;
  document.getElementById("export-routes").addEventListener("click", () => {
    exportTableAsCSV(routes, columns, "routes.csv");
  });
}

function renderRoutesStats(routes, data) {
  const totalDust = routes.reduce((a, r) => a + r.total_dust, 0);
  const totalFlights = routes.reduce((a, r) => a + r.n_flights, 0);
  const cards = [
    { label: "Routes", value: routes.length.toLocaleString() },
    { label: "Flights covered", value: totalFlights.toLocaleString() },
    { label: "Total dust ingested (g)", value: fmt(totalDust) },
  ];
  document.getElementById("routes-stat-cards").innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
    </div>
  `).join("");
}

async function refreshRoutes() {
  const filters = routesFilters();
  const emptyState = document.getElementById("routes-empty-state");
  const panel = document.getElementById("routes-panel");
  if (filters.aircraft_types.length === 0) {
    emptyState.hidden = false;
    panel.style.display = "none";
    document.getElementById("routes-stat-cards").innerHTML = "";
    return;
  }
  const data = await fetchJSON("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  const routes = data.dust_by_route;
  renderRoutesStats(routes, data);
  if (routes.length === 0) {
    emptyState.hidden = false;
    panel.style.display = "none";
    return;
  }
  emptyState.hidden = true;
  panel.style.display = "grid";
  renderRoutesMap(routes);
  renderRoutesTable(routes);
}
const debouncedRefreshRoutes = debounce(refreshRoutes, 300);

function setupModelPerformanceView() {
  const retrainBtn = document.getElementById("mp-retrain-btn");
  const retrainStatus = document.getElementById("mp-retrain-status");
  const fmt1 = v => v == null ? "—" : v.toFixed(1);
  let pollTimer = null;

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function loadStats() {
    const status = await fetchJSON("/api/surrogate_status");
    const cards = status.available
      ? [
          { label: "Training rows", value: status.n_training_rows?.toLocaleString() ?? "—" },
          { label: "Training flights", value: status.n_training_flights?.toLocaleString() ?? "—" },
          {
            label: "CV MAE (log10 concentration)",
            value: status.cv_mae != null
              ? `${status.cv_mae.toFixed(3)} ± ${status.cv_mae_std?.toFixed(3) ?? "?"}`
              : "not recorded (retrain to compute)",
          },
          {
            label: "CV RMSE (log10 concentration)",
            value: status.cv_rmse != null ? `${status.cv_rmse.toFixed(3)} ± ${status.cv_rmse_std?.toFixed(3) ?? "?"}` : "—",
          },
          {
            label: "CV R²",
            value: status.cv_r2 != null ? `${status.cv_r2.toFixed(3)} ± ${status.cv_r2_std?.toFixed(3) ?? "?"}` : "—",
          },
          { label: "Last trained", value: status.trained_utc ? new Date(status.trained_utc).toLocaleString() : "unknown" },
        ]
      : [{ label: "Model", value: "not trained yet -- click Retrain below" }];
    document.getElementById("mp-stat-cards").innerHTML = cards.map(c => `
      <div class="stat-card">
        <div class="label">${c.label}</div>
        <div class="value">${c.value}</div>
      </div>
    `).join("");
  }

  // Shared by both sections below -- a plain distribution histogram of a
  // 0-100 percentage across flights, so the mean/median in the summary
  // line above it isn't the only view into the spread (a tight cluster
  // around the mean reads very differently from a bimodal split, even
  // with the same average).
  function renderPctHistogram(chartId, values, xLabel, color) {
    Plotly.react(
      chartId,
      [{
        type: "histogram", x: values, marker: { color },
        xbins: { start: 0, end: 100, size: 5 },
        hovertemplate: `%{x}${"%"}<br>%{y} flight(s)<extra></extra>`,
      }],
      { ...baseLayout(xLabel, "Flights"), bargap: 0.05, height: 260 },
      PLOTLY_CONFIG,
    );
  }

  async function loadAgreement() {
    const summary = document.getElementById("mp-agreement-summary");
    const table = document.getElementById("mp-agreement-table");
    summary.textContent = "Loading…";
    const data = await fetchJSON("/api/surrogate/performance");
    if (data.n_pairs === 0) {
      summary.textContent = "Nothing compared yet -- on the Dust source attribution tab, check \"Overlay other method\" " +
        "for a flight to compute and log a comparison here.";
      table.innerHTML = "";
      document.getElementById("chart-mp-agreement").innerHTML = "";
      document.getElementById("chart-mp-hysplit-in-surrogate").innerHTML = "";
      document.getElementById("chart-mp-surrogate-in-hysplit").innerHTML = "";
      return;
    }
    summary.textContent = `${data.n_pairs} flight(s) compared -- mean ${data.mean_jaccard_pct.toFixed(0)}%, ` +
      `median ${data.median_jaccard_pct.toFixed(0)}% Jaccard overlap of each method's high-density region ` +
      `(HYSPLIT area in surrogate: mean ${data.mean_pct_hysplit_in_surrogate.toFixed(0)}%, median ${data.median_pct_hysplit_in_surrogate.toFixed(0)}%; ` +
      `surrogate area in HYSPLIT: mean ${data.mean_pct_surrogate_in_hysplit.toFixed(0)}%, median ${data.median_pct_surrogate_in_hysplit.toFixed(0)}%).`;
    renderPctHistogram("chart-mp-agreement", data.pairs.map(p => p.jaccard_pct), "Jaccard overlap (%)", cssVar("--series-cruise"));
    renderPctHistogram("chart-mp-hysplit-in-surrogate", data.pairs.map(p => p.pct_hysplit_in_surrogate), "% of HYSPLIT area in surrogate", cssVar("--series-climb"));
    renderPctHistogram("chart-mp-surrogate-in-hysplit", data.pairs.map(p => p.pct_surrogate_in_hysplit), "% of surrogate area in HYSPLIT", cssVar("--series-descent"));
    const columns = [
      { key: "flight_id", label: "Flight" },
      { key: "jaccard_pct", label: "Jaccard %" },
      { key: "pct_hysplit_in_surrogate", label: "% of HYSPLIT area in surrogate" },
      { key: "pct_surrogate_in_hysplit", label: "% of surrogate area in HYSPLIT" },
    ];
    const body = data.pairs.map(p => `
      <tr>
        <td>${p.flight_id}</td>
        <td class="num">${fmt1(p.jaccard_pct)}</td>
        <td class="num">${fmt1(p.pct_hysplit_in_surrogate)}</td>
        <td class="num">${fmt1(p.pct_surrogate_in_hysplit)}</td>
      </tr>
    `).join("");
    table.innerHTML = `
      <table class="data-table">
        <thead><tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>
        <tbody>${body}</tbody>
      </table>
      <button class="link-btn" id="export-mp-agreement" style="margin-top:8px;">Export CSV</button>
    `;
    document.getElementById("export-mp-agreement").addEventListener("click", () => {
      exportTableAsCSV(data.pairs, columns, "surrogate_vs_hysplit.csv");
    });
  }

  async function loadSusceptibilityAgreement() {
    const summary = document.getElementById("mp-susceptibility-summary");
    const table = document.getElementById("mp-susceptibility-table");
    summary.textContent = "Loading…";
    const data = await fetchJSON("/api/susceptibility_agreement");
    if (!data.computed) {
      summary.textContent = "Not run yet -- from hysplit_tools/, run: python validate_susceptibility_agreement.py";
      table.innerHTML = "";
      document.getElementById("chart-mp-susceptibility").innerHTML = "";
      return;
    }
    summary.textContent = `${data.n_flights} flight(s) -- mean ${data.mean_agreement_pct.toFixed(1)}%, ` +
      `median ${data.median_agreement_pct.toFixed(1)}% of each flight's own high-density region is ` +
      "susceptibility-confirmed.";
    renderPctHistogram("chart-mp-susceptibility", data.flights.map(f => f.agreement_pct), "Susceptibility agreement (%)", cssVar("--series-climb"));
    const columns = [
      { key: "flight_id", label: "Flight" },
      { key: "agreement_pct", label: "Susceptibility agreement %" },
    ];
    const body = data.flights.map(f => `
      <tr>
        <td>${f.flight_id}</td>
        <td class="num">${fmt1(f.agreement_pct)}</td>
      </tr>
    `).join("");
    table.innerHTML = `
      <table class="data-table">
        <thead><tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>
        <tbody>${body}</tbody>
      </table>
      <button class="link-btn" id="export-mp-susceptibility" style="margin-top:8px;">Export CSV</button>
    `;
    document.getElementById("export-mp-susceptibility").addEventListener("click", () => {
      exportTableAsCSV(data.flights, columns, "susceptibility_agreement.csv");
    });
  }

  function refresh() {
    loadStats();
    loadAgreement();
    loadSusceptibilityAgreement();
  }

  async function pollRetrain() {
    const data = await fetchJSON("/api/compute_status");
    if (data.status === "running") {
      if (data.job === "retrain") {
        retrainStatus.textContent = `Running, ${formatElapsed(data.elapsed_s)} elapsed. ${(data.log_tail || []).slice(-1)[0] || ""}`;
      }
      return; // keep polling regardless -- if it's someone else's job (a per-flight compute), wait for it to clear
    }
    stopPolling();
    retrainBtn.disabled = false;
    if (data.job !== "retrain") return; // some other job finished; not ours to report on
    if (data.status === "error") {
      retrainStatus.textContent = `Failed: ${(data.log_tail || []).slice(-1)[0] || "see server log"}`;
      return;
    }
    retrainStatus.textContent = "Done.";
    refresh();
  }

  retrainBtn.addEventListener("click", async () => {
    retrainBtn.disabled = true;
    retrainStatus.textContent = "Starting…";
    const res = await fetchJSON("/api/surrogate/retrain", { method: "POST" });
    if (res.status === "busy") {
      retrainStatus.textContent = `Busy: ${res.running.job} is already running. Try again once that finishes.`;
      retrainBtn.disabled = false;
      return;
    }
    retrainStatus.textContent = "Started. This can take a while with a lot of training data.";
    stopPolling();
    pollTimer = setInterval(pollRetrain, 5000);
    pollRetrain();
  });

  // Lazy -- only hits the (potentially slow, filesystem-scanning)
  // /api/surrogate/performance endpoint once the user actually opens this
  // tab, not on every page load.
  document.querySelector('[data-view="modelperf"]').addEventListener("click", refresh);
}

function setupRoutesView() {
  buildCheckboxes("routes-aircraft-checkboxes", defaults.aircraft_types, true);
  document.querySelectorAll("#routes-aircraft-checkboxes input").forEach(input => {
    input.addEventListener("change", debouncedRefreshRoutes);
  });
  const fromInput = document.getElementById("routes-date-from");
  const toInput = document.getElementById("routes-date-to");
  fromInput.min = defaults.date_min; fromInput.max = defaults.date_max; fromInput.value = defaults.date_min;
  toInput.min = defaults.date_min; toInput.max = defaults.date_max; toInput.value = defaults.date_max;
  fromInput.addEventListener("change", debouncedRefreshRoutes);
  toInput.addEventListener("change", debouncedRefreshRoutes);
  document.getElementById("routes-all-dates-btn").addEventListener("click", () => {
    fromInput.value = defaults.date_min;
    toInput.value = defaults.date_max;
    refreshRoutes();
  });
  refreshRoutes();
}

// =========================================================================
// COMPARE FILTERS VIEW (snapshot + overlay)
// =========================================================================

const FILTERCOMPARE_MAX = 8;
let heldSnapshots = [];
let snapshotCounter = 0;

function snapshotColor(i) {
  return cssVar(COMPARE_COLOR_VARS[i % COMPARE_COLOR_VARS.length]);
}

function describeFilters(filters) {
  const parts = [];
  parts.push(filters.aircraft_types.join("/") || "no aircraft");
  parts.push(`${filters.date_from} to ${filters.date_to}`);
  if (filters.phases.length < PHASES_ORDER.length) parts.push(filters.phases.join("/"));
  if (filters.altitude_min !== null) parts.push(`${filters.altitude_min}-${filters.altitude_max}ft`);
  if (filters.origin_icaos) parts.push(`from ${filters.origin_icaos.join("/")}`);
  if (filters.destination_icaos) parts.push(`to ${filters.destination_icaos.join("/")}`);
  return parts.join(" | ");
}

function holdSnapshot() {
  if (!lastAggregateData || lastAggregateData.n_flights === 0) {
    alert("No data to hold, adjust the aggregate view's filters so at least one flight matches first.");
    return;
  }
  if (heldSnapshots.length >= FILTERCOMPARE_MAX) {
    alert(`You can hold up to ${FILTERCOMPARE_MAX} snapshots. Remove one on the Compare filters tab first.`);
    return;
  }
  const suggested = describeFilters(lastAggregateFilters);
  const label = prompt("Name this snapshot:", suggested);
  if (label === null) return;
  heldSnapshots.push({ id: snapshotCounter++, label: label || suggested, data: lastAggregateData });
  renderFilterCompare();
}

function removeSnapshot(id) {
  heldSnapshots = heldSnapshots.filter(s => s.id !== id);
  renderFilterCompare();
}

function renderSnapshotLegend() {
  const container = document.getElementById("filtercompare-legend");
  container.innerHTML = heldSnapshots.map((snap, i) => `
    <div class="snapshot-chip" style="--chip-color: ${snapshotColor(i)}">
      ${snap.label}
      <button type="button" data-id="${snap.id}">✕</button>
    </div>
  `).join("");
  container.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => removeSnapshot(Number(btn.dataset.id)));
  });
}

function renderFCStatsTable() {
  const container = document.getElementById("fc-stats-table");
  ensureInfoOnlyCard("fc-stats-table", {
    title: "Snapshot stats",
    infoText: "Top-level numbers for each held snapshot, side by side.",
  });
  const columns = [
    { key: "label", label: "Snapshot" },
    { key: "n_flights", label: "Flights" },
    { key: "n_rows", label: "Timesteps" },
    { key: "total_dust_g", label: "Total dust (g)" },
    { key: "mean_dust_per_row", label: "Mean dust/timestep (g)" },
    { key: "mean_dust_per_flight", label: "Mean dust/flight (g)" },
  ];
  const rows = heldSnapshots.map((snap, i) => `
    <tr>
      <td><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${snapshotColor(i)};margin-right:6px;"></span>${snap.label}</td>
      <td class="num">${snap.data.n_flights.toLocaleString()}</td>
      <td class="num">${snap.data.n_rows.toLocaleString()}</td>
      <td class="num">${fmt(snap.data.total_dust_g)}</td>
      <td class="num">${fmt(snap.data.mean_dust_per_row)}</td>
      <td class="num">${fmt(snap.data.mean_dust_per_flight)}</td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table class="data-table">
      <thead><tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderFCTime() {
  const traces = heldSnapshots.map((snap, i) => {
    const series = snap.data.dust_per_time.overall;
    return {
      x: series.t.map(s => s / 60), y: series.mean_dust,
      type: "scatter", mode: "lines", name: snap.label,
      line: { width: 2, color: snapshotColor(i) },
    };
  });
  Plotly.react("chart-fc-time", traces, baseLayout("Elapsed time (min)", "Mean dust ingested (g)"), PLOTLY_CONFIG);
  ensureChartCard("chart-fc-time", {
    title: "Dust ingested per elapsed time",
    exportName: "compare_filters_dust_per_time",
    infoText: "Mean CoreDustIngested_g per 30-second elapsed-time bucket (all phases combined), one line per held snapshot.",
  });
}

function renderFCCumulative() {
  const traces = heldSnapshots.map((snap, i) => {
    const series = snap.data.cumulative_dust_per_time;
    return {
      x: series.t.map(s => s / 60), y: series.cumulative_mean_dust,
      type: "scatter", mode: "lines", name: snap.label,
      line: { width: 2, color: snapshotColor(i) },
    };
  });
  Plotly.react("chart-fc-cumulative", traces, baseLayout("Elapsed time (min)", "Cumulative mean dust ingested (g)"), PLOTLY_CONFIG);
  ensureChartCard("chart-fc-cumulative", {
    title: "Cumulative dust ingested per elapsed time",
    exportName: "compare_filters_cumulative_dust",
    infoText: "Running sum of the per-bucket mean dust values above, one line per held snapshot.",
  });
}

function renderFCPdf() {
  const traces = heldSnapshots
    .map((snap, i) => ({ snap, i, h: snap.data.pdf.overall }))
    .filter(({ h }) => h)
    .map(({ snap, i, h }) => ({
      x: h.bin_centers, y: h.density, type: "scatter", mode: "lines", name: snap.label,
      line: { width: 2, color: snapshotColor(i), shape: "hv" },
    }));
  const layout = baseLayout("Dust ingested per timestep (g, log scale)", "Probability density (per log₁₀ unit)");
  layout.xaxis.type = "log";
  Plotly.react("chart-fc-pdf", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-fc-pdf", {
    title: "Probability density of dust ingested per timestep",
    exportName: "compare_filters_pdf",
    infoText: "Histogram of per-timestep CoreDustIngested_g (all phases pooled, log-spaced bins), one line per held snapshot.",
  });
}

function renderFCAltitude() {
  const traces = heldSnapshots
    .map((snap, i) => ({ snap, i, series: snap.data.dust_per_altitude.overall }))
    .filter(({ series }) => series && series.alt.length > 0)
    .map(({ snap, i, series }) => ({
      x: series.alt, y: series.mean_dust, type: "scatter", mode: "lines", name: snap.label,
      line: { width: 2, color: snapshotColor(i) },
    }));
  Plotly.react("chart-fc-altitude", traces, baseLayout("Altitude (ft)", "Mean dust ingested (g)"), PLOTLY_CONFIG);
  ensureChartCard("chart-fc-altitude", {
    title: "Mean dust ingested vs altitude",
    exportName: "compare_filters_altitude",
    infoText: "Mean CoreDustIngested_g per 1,000ft altitude bucket (all phases combined), one line per held snapshot.",
  });
}

function renderFCHour() {
  const traces = heldSnapshots.map((snap, i) => {
    const series = snap.data.dust_by_hour;
    return {
      x: series.hour.map(h => `${String(h).padStart(2, "0")}:00`), y: series.mean_dust,
      type: "scatter", mode: "lines+markers", name: snap.label,
      line: { width: 2, color: snapshotColor(i) }, marker: { size: 5, color: snapshotColor(i) },
    };
  });
  const layout = baseLayout("Takeoff hour of day", "Mean dust ingested per timestep (g)");
  layout.xaxis.type = "category";
  Plotly.react("chart-fc-hour", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-fc-hour", {
    title: "Dust ingested vs takeoff hour of day",
    exportName: "compare_filters_by_hour",
    infoText: "Mean per-timestep CoreDustIngested_g by takeoff hour, one line per held snapshot.",
  });
}

function renderFCDate() {
  const traces = heldSnapshots.map((snap, i) => {
    const series = snap.data.dust_by_date;
    return {
      x: series.date, y: series.total_dust,
      type: "scatter", mode: "lines+markers", name: snap.label,
      line: { width: 2, color: snapshotColor(i) }, marker: { size: 4, color: snapshotColor(i) },
    };
  });
  const layout = baseLayout("Date", "Total dust ingested (g)");
  layout.xaxis.type = "category";
  layout.xaxis.tickangle = -45;
  layout.xaxis.nticks = 20;
  layout.margin.b = 70;
  Plotly.react("chart-fc-date", traces, layout, PLOTLY_CONFIG);
  ensureChartCard("chart-fc-date", {
    title: "Total dust ingested by date",
    exportName: "compare_filters_by_date",
    infoText: "Sum of CoreDustIngested_g per calendar date, one line per held snapshot.",
  });
}

function renderFilterCompare() {
  const emptyState = document.getElementById("filtercompare-empty-state");
  const panel = document.getElementById("filtercompare-panel");
  if (heldSnapshots.length === 0) {
    emptyState.hidden = false;
    panel.hidden = true;
    return;
  }
  emptyState.hidden = true;
  panel.hidden = false;
  renderSnapshotLegend();
  renderFCStatsTable();
  renderFCTime();
  renderFCCumulative();
  renderFCPdf();
  renderFCAltitude();
  renderFCHour();
  renderFCDate();
}

function setupFilterCompareView() {
  document.getElementById("hold-snapshot-btn").addEventListener("click", holdSnapshot);
  renderFilterCompare();
}

// =========================================================================
// SETTINGS
// =========================================================================

const SETTINGS_KEY = "dustDashboardSettings";
const DEFAULT_SETTINGS = { theme: "system", density: "comfortable", topN: 15 };
const DENSITY_HEIGHTS = {
  compact: { h: "240px", tall: "320px", short: "110px" },
  comfortable: { h: "320px", tall: "440px", short: "140px" },
  tall: { h: "420px", tall: "560px", short: "170px" },
};

let appSettings = { ...DEFAULT_SETTINGS };

function loadSettings() {
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    return { ...DEFAULT_SETTINGS, ...(raw || {}) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}
function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(appSettings));
}

function applyTheme(theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}

function applyDensity(density) {
  const heights = DENSITY_HEIGHTS[density] || DENSITY_HEIGHTS.comfortable;
  document.documentElement.style.setProperty("--chart-h", heights.h);
  document.documentElement.style.setProperty("--chart-h-tall", heights.tall);
  document.documentElement.style.setProperty("--chart-h-short", heights.short);
  requestAnimationFrame(() => {
    document.querySelectorAll(".chart").forEach(el => {
      if (el.offsetParent !== null) Plotly.Plots.resize(el);
    });
  });
}

function applySettings() {
  applyTheme(appSettings.theme);
  applyDensity(appSettings.density);
}

function setupSettingsModal() {
  const overlay = document.getElementById("settings-overlay");
  const openBtn = document.getElementById("settings-btn");
  const closeBtn = document.getElementById("settings-close");
  const topNSelect = document.getElementById("settings-top-n");

  document.querySelector(`input[name="theme"][value="${appSettings.theme}"]`).checked = true;
  document.querySelector(`input[name="density"][value="${appSettings.density}"]`).checked = true;
  topNSelect.value = String(appSettings.topN);

  openBtn.addEventListener("click", () => { overlay.hidden = false; });
  closeBtn.addEventListener("click", () => { overlay.hidden = true; });
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });

  document.querySelectorAll('input[name="theme"]').forEach(input => {
    input.addEventListener("change", () => {
      appSettings.theme = input.value;
      saveSettings();
      applyTheme(appSettings.theme);
    });
  });
  document.querySelectorAll('input[name="density"]').forEach(input => {
    input.addEventListener("change", () => {
      appSettings.density = input.value;
      saveSettings();
      applyDensity(appSettings.density);
    });
  });
  topNSelect.addEventListener("change", () => {
    appSettings.topN = Number(topNSelect.value);
    saveSettings();
    refresh();
  });
}

// =========================================================================
// TABS + INIT
// =========================================================================

function setupTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      buttons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const view = btn.dataset.view;
      document.getElementById("view-about").hidden = view !== "about";
      document.getElementById("view-aggregate").hidden = view !== "aggregate";
      document.getElementById("view-single").hidden = view !== "single";
      document.getElementById("view-dustsource").hidden = view !== "dustsource";
      document.getElementById("view-compare").hidden = view !== "compare";
      document.getElementById("view-filtercompare").hidden = view !== "filtercompare";
      document.getElementById("view-routes").hidden = view !== "routes";
      document.getElementById("view-modelperf").hidden = view !== "modelperf";
      setTimeout(() => {
        document.querySelectorAll(".chart").forEach(el => {
          if (el.offsetParent !== null) Plotly.Plots.resize(el);
        });
      }, 50);
    });
  });
}

function setupAltitudeFilter() {
  const enableBox = document.getElementById("altitude-enable");
  const minInput = document.getElementById("altitude-min");
  const maxInput = document.getElementById("altitude-max");
  enableBox.addEventListener("change", () => {
    const on = enableBox.checked;
    minInput.disabled = !on;
    maxInput.disabled = !on;
    debouncedRefresh();
  });
  minInput.addEventListener("change", debouncedRefresh);
  maxInput.addEventListener("change", debouncedRefresh);
}

function setupFlightRankingControls() {
  document.querySelectorAll('input[name="flights-order"]').forEach(input => {
    input.addEventListener("change", () => {
      flightRanking.order = input.value;
      debouncedRefresh();
    });
  });
  document.getElementById("flights-limit-select").addEventListener("change", (e) => {
    flightRanking.limit = Number(e.target.value);
    debouncedRefresh();
  });
}

async function init() {
  appSettings = loadSettings();
  applySettings();

  [defaults, airports] = await Promise.all([
    fetchJSON("/api/filters"),
    fetchJSON("/api/airports"),
  ]);
  applyDefaults();
  initSingleFlightView();
  setupAttributionTab();
  setupCompareView();
  setupRoutesView();
  setupFilterCompareView();
  setupModelPerformanceView();
  setupTabs();
  setupAltitudeFilter();
  setupLocationFilter();
  setupRegistrationFilter();
  setupFlightRankingControls();
  setupSettingsModal();

  document.getElementById("date-from").addEventListener("change", debouncedRefresh);
  document.getElementById("date-to").addEventListener("change", debouncedRefresh);
  document.getElementById("takeoff-from").addEventListener("change", debouncedRefresh);
  document.getElementById("takeoff-to").addEventListener("change", debouncedRefresh);
  document.getElementById("reset-btn").addEventListener("click", () => {
    applyDefaults();
    refresh();
  });
  document.getElementById("all-day-btn").addEventListener("click", () => {
    document.getElementById("takeoff-from").value = "00:00";
    document.getElementById("takeoff-to").value = "23:59";
    refresh();
  });
  document.getElementById("all-dates-btn").addEventListener("click", () => {
    document.getElementById("date-from").value = defaults.date_min;
    document.getElementById("date-to").value = defaults.date_max;
    refresh();
  });

  window.addEventListener("resize", debounce(() => {
    document.querySelectorAll(".chart").forEach(el => Plotly.Plots.resize(el));
  }, 200));

  refresh();
}

init();
