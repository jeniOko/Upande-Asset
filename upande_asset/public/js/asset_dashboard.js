/* Asset Dashboard (/asset-dashboard) — summarised, visual and clickable.
   Data: upande_asset.upande_asset.dashboard.get_dashboard / get_events (permission-aware). */
(function () {
	"use strict";
	const root = document.getElementById("uad");
	if (!root) return;
	const $ = (sel) => root.querySelector(`[data-el="${sel}"]`);
	const API = "upande_asset.upande_asset.dashboard";
	const PALETTE = ["#0a0a0a", "#228883", "#b87400", "#5a5a52", "#73b3a0", "#c4302b", "#8a8780", "#3a3a34", "#d9a441", "#4960a0"];
	const EVENT_TYPES = [
		["renewal", "Policy renewals", "#b87400"],
		["service", "Service jobs", "#228883"],
		["maintenance", "Maintenance tasks", "#0a0a0a"],
		["todo", "ToDos", "#4960a0"],
	];
	const NAV = [
		["overview", "Overview", '<rect x="3" y="3" width="7" height="9" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/><rect x="3" y="16" width="7" height="5" rx="2"/>'],
		["schedules", "Schedules", '<rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 2v4M16 2v4"/>'],
		["maintenance", "Maintenance & Service", '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>'],
		["insurance", "Insurance", '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>'],
	];
	const TITLES = {
		overview: ["Assets", "Overview", "Your asset base, cover and what needs attention today."],
		schedules: ["Plan", "Schedules", "Policy renewals, service jobs coming due and your ToDos."],
		maintenance: ["Upkeep", "Maintenance & Service", "Service jobs, maintenance tasks, repairs and check sheets."],
		insurance: ["Cover", "Insurance", "Policies, renewals, claims and gaps in cover."],
	};

	// Shortest span first; "This week" starts on the instance's first day of the week.
	const PRESETS = { week: "This week", month: "This month", last30: "Last 30 days", quarter: "This quarter",
		last90: "Last 90 days", ytd: "This year", last12: "Last 12 months", lastyear: "Last year", custom: "Custom" };
	const store = {
		get(k) { try { return JSON.parse(localStorage.getItem("uad:" + k)); } catch (e) { return null; } },
		set(k, v) { try { localStorage.setItem("uad:" + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
	};
	const params = new URLSearchParams(location.search);
	const tabParam = params.get("tab") === "calendar" ? "schedules" : params.get("tab");
	const saved = store.get("filters") || {};
	const state = {
		tab: NAV.some((n) => n[0] === tabParam) ? tabParam : "overview",
		company: params.get("company") || root.dataset.company || "",
		data: null,
		cal: {
			view: ["month", "week", "day"].includes(store.get("calView")) ? store.get("calView") : "month",
			anchor: new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()),
			who: store.get("calWho") || "", // "" everyone · "__me" · "__none" unassigned · or a user id / role
			q: "", // typed name: filters by any part of the assignee's name
			events: [], hidden: new Set(),
		},
		agenda: [],
		fdow: 1, // first day of the week (0 = Sunday); replaced by the instance's System Settings
		dims: [],
		f: {
			preset: params.get("from") ? "custom" : (PRESETS[saved.preset] ? saved.preset : "ytd"),
			from: params.get("from") || saved.from || "",
			to: params.get("to") || saved.to || "",
			dimension: params.get("dimension") || saved.dimension || "",
			dimvalue: params.get("dimension_value") || saved.dimvalue || "",
		},
	};

	// ── helpers ──────────────────────────────────────────────────────────
	const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
	const slug = (dt) => dt.toLowerCase().replace(/ /g, "-");
	const docUrl = (dt, name) => `/app/${slug(dt)}/${encodeURIComponent(name)}`;
	const listUrl = (dt, filters) => `/app/${slug(dt)}` + (filters ? "?" + new URLSearchParams(filters).toString() : "");
	const reportUrl = (name, filters) => `/app/query-report/${encodeURIComponent(name)}` + (filters ? "?" + new URLSearchParams(filters).toString() : "");
	const cur = () => (state.data && state.data.currency) || "";
	const num = (v) => new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(v || 0);
	const compact = (v) => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(v || 0);
	const money = (v) => `<small>${esc(cur())}</small>${compact(v)}`;
	const moneyFull = (v) => `${cur()} ${num(v)}`;
	const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
	const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
	const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
	const fmtDate = (s, opts) => (s ? parse(String(s).slice(0, 10)).toLocaleDateString("en-GB", opts || { day: "numeric", month: "short", year: "numeric" }) : "");
	const daysFrom = (s) => Math.round((parse(String(s).slice(0, 10)) - parse(iso(new Date()))) / 86400000);
	const company = () => state.company || (state.data && state.data.company) || "";
	const withCo = (f) => Object.assign({ company: company() }, f || {});

	// ── filters ──────────────────────────────────────────────────────────
	function presetRange(p) {
		const t = new Date(), y = t.getFullYear(), m = t.getMonth();
		const back = (days) => { const d = new Date(t); d.setDate(d.getDate() - days); return d; };
		switch (p) {
			case "week": return [back((t.getDay() - state.fdow + 7) % 7), t];
			case "month": return [new Date(y, m, 1), t];
			case "quarter": return [new Date(y, m - (m % 3), 1), t];
			case "last30": return [back(29), t];
			case "last90": return [back(89), t];
			case "last12": return [new Date(y, m - 11, 1), t];
			case "lastyear": return [new Date(y - 1, 0, 1), new Date(y - 1, 11, 31)];
			default: return [new Date(y, 0, 1), t];
		}
	}
	function period() {
		if (state.f.preset === "custom" && state.f.from && state.f.to) return [state.f.from, state.f.to];
		return presetRange(state.f.preset).map(iso);
	}
	function periodLabel() {
		if (state.f.preset !== "custom") return PRESETS[state.f.preset].toLowerCase();
		const [a, b] = period();
		return `${fmtDate(a, { day: "numeric", month: "short" })} – ${fmtDate(b)}`;
	}
	const dimArgs = () => (state.f.dimension && state.f.dimvalue ? { dimension: state.f.dimension, dimension_value: state.f.dimvalue } : {});
	const dimLabel = () => {
		const d = state.dims.find((x) => x.fieldname === state.f.dimension);
		return d && state.f.dimvalue ? `${d.label}: ${state.f.dimvalue}` : "";
	};
	function saveFilters() { store.set("filters", state.f); }

	function renderFilters() {
		const dsel = $("dimension"), vsel = $("dimvalue");
		$("dimfilter").hidden = !state.dims.length;
		dsel.innerHTML = state.dims.map((d) => `<option value="${esc(d.fieldname)}" ${d.fieldname === state.f.dimension ? "selected" : ""}>${esc(d.label)}</option>`).join("");
		if (!state.f.dimension && state.dims.length) state.f.dimension = state.dims[0].fieldname;
		$("dimfilter").classList.toggle("on", !!state.f.dimvalue);
		$("datefilter").hidden = state.tab === "schedules"; // the calendar has its own month navigation
		$("preset").value = state.f.preset;
		const [a, b] = period();
		$("from").value = a; $("to").value = b;
		$("range").hidden = state.f.preset !== "custom";
		$("datefilter").classList.toggle("on", state.f.preset !== "ytd");
		vsel.value = state.f.dimvalue;
	}
	async function loadDimValues() {
		const list = state.f.dimension ? (await call("get_dimension_values", { dimension: state.f.dimension, company: company() })) || [] : [];
		$("dimvalue").innerHTML = `<option value="">All</option>` + list.map((v) =>
			`<option value="${esc(v.name)}">${v.is_group ? "▸ " : ""}${esc(v.name)}</option>`).join("");
		if (state.f.dimvalue && !list.some((v) => v.name === state.f.dimvalue)) state.f.dimvalue = "";
		$("dimvalue").value = state.f.dimvalue;
	}

	async function call(method, args) {
		const qs = new URLSearchParams(Object.entries(args || {}).filter(([, v]) => v !== undefined && v !== null && v !== ""));
		const r = await fetch(`/api/method/${API}.${method}?${qs}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
		if (r.status === 403 || r.status === 401) { location.href = "/login?redirect-to=" + encodeURIComponent(location.pathname + location.search); return null; }
		const j = await r.json();
		if (!r.ok) throw new Error((j && (j._error_message || j.exc_type)) || r.statusText);
		return j.message;
	}

	function syncUrl() {
		const p = new URLSearchParams();
		if (company()) p.set("company", company());
		p.set("tab", state.tab);
		if (state.f.preset === "custom") { const [a, b] = period(); p.set("from", a); p.set("to", b); }
		if (state.f.dimvalue) { p.set("dimension", state.f.dimension); p.set("dimension_value", state.f.dimvalue); }
		history.replaceState(null, "", `${location.pathname}?${p}`);
	}

	// ── chrome ───────────────────────────────────────────────────────────
	function renderNav() {
		const k = state.data ? state.data.kpis : {};
		const badge = { maintenance: k.overdue_service, insurance: (k.uninsured || 0) + (k.expiring_policies || 0) };
		$("nav").innerHTML = NAV.map(([id, label, icon]) => `
			<button class="ua-nav ${state.tab === id ? "on" : ""}" data-tab="${id}" title="${esc(label)}">
				<svg viewBox="0 0 24 24">${icon}</svg><span class="ua-nav__lbl">${esc(label)}</span>
				${badge[id] ? `<span class="ua-nav__cnt bad">${badge[id]}</span>` : ""}
			</button>`).join("");
		const [eyebrow, title, sub] = TITLES[state.tab];
		$("eyebrow").textContent = eyebrow;
		$("title").textContent = title;
		const scope = [state.tab !== "schedules" ? `Period: ${periodLabel()}` : "", dimLabel()].filter(Boolean);
		$("sub").textContent = scope.length ? `${sub} ${scope.join(" · ")}.` : sub;
		renderFilters();
	}

	function renderCompanies() {
		const sel = $("company");
		const list = (state.data && state.data.companies) || [];
		sel.innerHTML = list.map((c) => `<option ${c === company() ? "selected" : ""}>${esc(c)}</option>`).join("");
	}

	// Drill spec → attribute. Clicking an element with data-drill opens a pop-up of its records;
	// its data-href is the "open full list" link inside the pop-up.
	const dd = (spec) => (spec ? ` data-drill="${esc(JSON.stringify(spec))}"` : "");

	// ── chart builders ───────────────────────────────────────────────────
	function donut(items, { valueKey = "count", href, label = "", center, drill }) {
		const total = items.reduce((s, i) => s + (i[valueKey] || 0), 0);
		if (!total) return `<div class="ua-empty">Nothing to show yet.</div>`;
		const R = 80, r = 52, C = 100;
		let a0 = -Math.PI / 2;
		const arcs = items.map((it, idx) => {
			const v = it[valueKey] || 0, frac = v / total;
			const a1 = a0 + frac * Math.PI * 2 - (items.length > 1 ? 0.012 : 0);
			const large = a1 - a0 > Math.PI ? 1 : 0;
			const p = (ang, rad) => `${C + rad * Math.cos(ang)},${C + rad * Math.sin(ang)}`;
			const d = frac >= 0.9999
				? `M${C},${C - R}A${R},${R} 0 1 1 ${C - 0.01},${C - R}L${C - 0.01},${C - r}A${r},${r} 0 1 0 ${C},${C - r}Z`
				: `M${p(a0, R)}A${R},${R} 0 ${large} 1 ${p(a1, R)}L${p(a1, r)}A${r},${r} 0 ${large} 0 ${p(a0, r)}Z`;
			const tip = `<b>${esc(it.name)}</b>${valueKey === "value" ? moneyFull(v) : num(v)} · ${pct(v, total)}%`;
			const out = `<path d="${d}" fill="${PALETTE[idx % PALETTE.length]}" data-href="${esc(href(it))}"${dd(drill && drill(it))} data-tip="${esc(tip)}" style="animation-delay:${idx * 0.04}s"/>`;
			a0 += frac * Math.PI * 2;
			return out;
		}).join("");
		const mid = center || (valueKey === "value" ? compact(total) : num(total));
		const svg = `<svg class="ua-donut" viewBox="0 0 200 200">${arcs}
			<text x="100" y="98" text-anchor="middle" style="font:600 22px var(--f);fill:#0a0a0a">${esc(mid)}</text>
			<text x="100" y="118" text-anchor="middle" style="font:500 9px var(--f);letter-spacing:1.4px;fill:#8a8780;text-transform:uppercase">${esc(label)}</text></svg>`;
		const legend = items.map((it, idx) => `
			<div class="ua-lg__r" data-href="${esc(href(it))}"${dd(drill && drill(it))}>
				<i style="background:${PALETTE[idx % PALETTE.length]}"></i>
				<div><div class="ua-lg__n">${esc(it.name)}</div><small>${num(it.count)} ${it.count === 1 ? "item" : "items"}</small></div>
				<b>${valueKey === "value" ? esc(cur()) + " " + compact(it.value) : pct(it[valueKey], total) + "%"}</b>
			</div>`).join("");
		return `<div class="ua-dw"><div>${svg}</div><div>${legend}</div></div>`;
	}

	function hbars(items, { valueKey = "value", href, cls = "", sub, drill }) {
		if (!items.length) return `<div class="ua-empty">Nothing to show yet.</div>`;
		const max = Math.max(...items.map((i) => i[valueKey] || 0)) || 1;
		return items.map((it, idx) => `
			<div class="ua-hb" data-href="${esc(href(it))}"${dd(drill && drill(it))} data-tip="${esc(`<b>${esc(it.name)}</b>${moneyFull(it[valueKey])}`)}">
				<div class="ua-hb__n">${esc(it.name)}${sub ? `<small>${esc(sub(it))}</small>` : ""}</div>
				<div class="ua-hb__lane"><div class="ua-hb__a ${cls}" style="width:${Math.max(2, ((it[valueKey] || 0) / max) * 100)}%;animation-delay:${idx * 0.05}s"></div></div>
				<div class="ua-hb__v">${compact(it[valueKey])}</div>
			</div>`).join("");
	}

	function columns(months) {
		if (!months.length) return `<div class="ua-empty">Nothing to show yet.</div>`;
		const W = 1100, H = 210, pad = 40, bw = (W - pad) / Math.max(months.length, 6);
		const max = Math.max(...months.map((m) => m.materials + m.services)) || 1;
		const multiYear = new Set(months.map((m) => m.year)).size > 1;
		const cols = months.map((m, i) => {
			const x = pad + i * bw + bw * 0.18, w = bw * 0.64;
			const hs = ((m.services) / max) * (H - 40), hm = ((m.materials) / max) * (H - 40);
			const href = listUrl("Asset Service Record", withCo({ docstatus: 1 }));
			const dt = new Date(m.year, m.month - 1, 1);
			const lbl = dt.toLocaleString("en", { month: months.length > 12 ? "narrow" : "short" }) + (multiYear && (m.month === 1 || i === 0) && months.length <= 12 ? ` ’${String(m.year).slice(2)}` : "");
			const tip = `<b>${dt.toLocaleString("en", { month: "long", year: "numeric" })}</b>Materials ${moneyFull(m.materials)}<br>Services ${moneyFull(m.services)}`;
			const ym = `${m.year}-${String(m.month).padStart(2, "0")}`;
			return `<g class="ua-col" data-href="${esc(href)}"${dd({ k: "done", g: "month", v: ym, t: "Service jobs completed", s: dt.toLocaleString("en", { month: "long", year: "numeric" }) })} data-tip="${esc(tip)}">
				<rect class="ua-hov" x="${pad + i * bw}" y="0" width="${bw}" height="${H - 16}" fill="transparent" rx="8"/>
				<rect class="ua-gy" x="${x}" y="${H - 20 - hs - hm}" width="${w}" height="${hs}" rx="4" fill="#228883" style="animation-delay:${i * 0.03}s"/>
				<rect class="ua-gy" x="${x}" y="${H - 20 - hm}" width="${w}" height="${hm}" rx="4" fill="#0a0a0a" style="animation-delay:${i * 0.03}s"/>
				<text x="${x + w / 2}" y="${H - 4}" text-anchor="middle">${esc(lbl)}</text></g>`;
		}).join("");
		const grid = [0.25, 0.5, 0.75, 1].map((f) => {
			const y = H - 20 - f * (H - 40);
			return `<line x1="${pad}" x2="${W}" y1="${y}" y2="${y}" stroke="rgba(10,10,10,.06)"/><text x="0" y="${y + 3}">${compact(max * f)}</text>`;
		}).join("");
		return `<svg class="ua-chart" viewBox="0 0 ${W} ${H}" style="max-height:260px">${grid}${cols}</svg>`;
	}

	function row(cls, rank, title, meta, right, href, tip) {
		return `<div class="ua-row" data-href="${esc(href)}"${tip ? ` data-tip="${esc(tip)}"` : ""}>
			<div class="ua-rank ${cls}">${rank}</div>
			<div style="min-width:0"><div class="ua-row__n">${esc(title)}</div><div class="ua-row__m">${meta}</div></div>
			<div class="ua-row__q">${right}</div></div>`;
	}
	const card = (title, meta, body, extra = "") => `<section class="ua-card" ${extra}><div class="ua-card__h"><h3>${esc(title)}</h3>${meta ? `<span class="ua-meta">${meta}</span>` : ""}</div>${body}</section>`;
	const kpi = (label, value, sub, href, bar, drill) => `<a class="ua-kpi" href="${esc(href)}"${dd(drill && { t: label, ...drill })}>
		<div class="ua-kpi__l">${esc(label)}</div><div class="ua-kpi__v">${value}</div>
		${sub ? `<div class="ua-kpi__u">${sub}</div>` : ""}${bar ? `<div class="ua-bar"><i class="${bar[1] || ""}" style="width:${Math.min(100, bar[0])}%"></i></div>` : ""}</a>`;
	const more = (href, text = "Open list →") => `<a href="${esc(href)}">${text}</a>`;
	const dueBadge = (d) => {
		if (d === null || d === undefined) return "";
		if (d < 0) return `<span class="ua-badge bad">${-d}d overdue</span>`;
		if (d === 0) return `<span class="ua-badge warn">Today</span>`;
		return `<span class="ua-badge ${d <= 7 ? "warn" : ""}">in ${d}d</span>`;
	};
	const statusBadge = (s) => {
		const map = { Pending: "warn", "In Progress": "", Completed: "good", Approved: "good", "Partly Settled": "warn", Settled: "good",
			Rejected: "bad", Recorded: "warn", "Under Assessment": "warn", Expiring: "warn", Active: "good", Expired: "bad", Overdue: "bad" };
		return `<span class="ua-badge ${map[s] || ""}">${esc(s)}</span>`;
	};

	// ── views ────────────────────────────────────────────────────────────
	function viewOverview(d) {
		const k = d.kpis;
		const coverPct = pct(k.insured, k.insurance_required);
		const flags = d.flags.length ? d.flags.map((f) => `
			<a class="ua-flag sev-${f.sev}" href="${esc(f.route)}"${dd({ k: f.key, t: f.label, s: f.desc })}><div class="ua-flag__h"><span class="ua-flag__dot"></span>${esc(f.label)}</div>
			<div class="ua-flag__v">${num(f.value)}</div><div class="ua-flag__d">${esc(f.desc)}</div></a>`).join("")
			: `<div class="ua-flag ok"><div class="ua-flag__h"><span class="ua-flag__dot"></span>All clear</div><div class="ua-flag__d">Nothing overdue, expiring or uninsured.</div></div>`;
		const soon = state.agenda.filter((e) => daysFrom(e.date) <= 14).slice(0, 8);
		return `
		<div class="ua-kpis">
			${kpi("Assets in use", num(k.assets), `${num(k.disposed)} disposed`, listUrl("Asset", withCo({ docstatus: 1 })), null, { k: "assets" })}
			${kpi("Book value", money(k.book_value), `Cost ${esc(cur())} ${compact(k.gross_value)}`, reportUrl("Fixed Asset Register", withCo()),
				[pct(k.book_value, k.gross_value), "good"], { k: "assets" })}
			${kpi("Insurance cover", `${coverPct}%`, `${num(k.insured)} of ${num(k.insurance_required)} assets insured`,
				reportUrl("Insurance Register", withCo()), [coverPct, coverPct < 100 ? "bad" : "good"], { k: "insured" })}
			${kpi("Open service jobs", num(k.open_service), k.overdue_service ? `<span class="ua-down">${num(k.overdue_service)} overdue</span>` : "None overdue",
				listUrl("Asset Service Record", withCo({ docstatus: 0 })), null, { k: "open_service" })}
			${kpi("Open claims", num(k.open_claims), `${esc(cur())} ${compact(k.recoverable)} recoverable`, listUrl("Insurance Claim", withCo()), null, { k: "open_claims" })}
		</div>
		${card("Needs attention", "", `<div class="ua-flags">${flags}</div>`)}
		${card(`Activity · ${periodLabel()}`, "", `<div class="ua-kpis ua-kpis--flat">
			${kpi("Service jobs completed", num(k.completed_month), "", listUrl("Asset Service Record", withCo({ docstatus: 1 })), null, { k: "done", s: periodLabel() })}
			${kpi("Service cost", money(k.service_cost_year), `Materials ${compact(k.materials_cost_year)} · Services ${compact(k.services_cost_year)}`,
				listUrl("Asset Service Record", withCo({ docstatus: 1 })), null, { k: "done", s: periodLabel() })}
			${kpi("Check sheets", num(k.checks_30d), `${num(k.checks_passed_30d)} passed`, listUrl("Asset Check Sheet", withCo({ docstatus: 1 })), null, { k: "sheets", s: periodLabel() })}
			${kpi("Claims raised", num(k.claims_period), `${esc(cur())} ${compact(k.claimed_period)} claimed`, listUrl("Insurance Claim", withCo()), null, { k: "claims", s: periodLabel() })}
			${kpi("Premium", money(k.premium_year), "Policies starting in period", reportUrl("Premium Analysis", withCo()), null, { k: "premium", s: periodLabel() })}
		</div>`)}
		<div class="ua-r2e">
			${card("Book value by category", more(listUrl("Asset", withCo({ docstatus: 1 }))),
				donut(d.by_category, { valueKey: "value", label: "book value", href: (i) => listUrl("Asset", withCo({ asset_category: i.name, docstatus: 1 })),
					drill: (i) => ({ k: "assets", g: "asset_category", v: i.name, t: "Assets", s: i.name }) }))}
			${card("Assets by status", "", donut(d.by_status, { label: "assets", href: (i) => listUrl("Asset", withCo({ status: i.name, docstatus: 1 })),
				drill: (i) => ({ k: "assets", g: "status", v: i.name, t: "Assets", s: i.name }) }))}
		</div>
		<div class="ua-r2e">
			${card("Book value by cost center", "", hbars(d.by_cost_center, { href: (i) => listUrl("Asset", withCo({ cost_center: i.name, docstatus: 1 })), sub: (i) => `${i.count} assets`,
				drill: (i) => ({ k: "assets", g: "cost_center", v: i.name, t: "Assets", s: i.name }) }))}
			${card("Coming up in 14 days", more(`?${new URLSearchParams({ company: company(), tab: "schedules" })}`, "Schedules →"),
				soon.length ? soon.map(eventRow).join("") : `<div class="ua-empty">Nothing due in the next two weeks.</div>`)}
		</div>`;
	}

	function viewMaintenance(d) {
		const k = d.kpis, s = d.service;
		const open = s.open.map((r) => row(r.overdue ? "bad" : (r.status === "In Progress" ? "good" : "warn"),
			r.service_by === "External" ? "EX" : "IN", r.title || r.name,
			`${esc(r.asset_name || r.asset)}${r.tasks && r.tasks.length ? " · " + esc(r.tasks.join(", ")) : ""}`,
			`${r.due ? dueBadge(daysFrom(r.due)) : ""}<small>${esc(r.status)}</small>`, docUrl("Asset Service Record", r.name))).join("");
		const overdue = s.overdue_logs.map((l) => row("bad", "!", l.task_name || l.name, esc(l.asset_name), dueBadge(daysFrom(l.due_date)),
			docUrl("Asset Maintenance Log", l.name))).join("");
		const repairs = s.repairs.map((r) => row("warn", "RP", r.asset_name || r.asset, `${esc(r.name)} · failed ${fmtDate(r.failure_date)}`,
			statusBadge(r.repair_status), docUrl("Asset Repair", r.name))).join("");
		const issues = s.open_issues.map((c) => row("warn", "CS", c.asset_name || c.asset, `${esc(c.name)} · ${fmtDate(c.check_date)}`,
			`<span class="ua-badge warn">Needs attention</span>`, docUrl("Asset Check Sheet", c.name))).join("");
		return `
		<div class="ua-kpis">
			${kpi("Open service jobs", num(k.open_service), "Pending or in progress", listUrl("Asset Service Record", withCo({ docstatus: 0 })), null, { k: "open_service" })}
			${kpi("Overdue", `<span class="${k.overdue_service ? "ua-down" : ""}">${num(k.overdue_service)}</span>`, "Jobs and tasks past due",
				listUrl("Asset Maintenance Log", { maintenance_status: "Overdue" }), null, { k: "overdue" })}
			${kpi("Completed", num(k.completed_month), `Service records, ${esc(periodLabel())}`, listUrl("Asset Service Record", withCo({ docstatus: 1 })), null, { k: "done", s: periodLabel() })}
			${kpi("Service cost", money(k.service_cost_year), `Materials ${compact(k.materials_cost_year)} · Services ${compact(k.services_cost_year)}`,
				listUrl("Asset Service Record", withCo({ docstatus: 1 })), [pct(k.materials_cost_year, k.service_cost_year), "good"], { k: "done", s: periodLabel() })}
			${kpi("Open repairs", num(k.open_repairs), "Pending or in progress", listUrl("Asset Repair", withCo({ repair_status: "Pending" })), null, { k: "repairs" })}
		</div>
		<div class="ua-r2">
			${card("Open service jobs", more(listUrl("Asset Service Record", withCo({ docstatus: 0 }))), open || `<div class="ua-empty">No open service jobs.</div>`)}
			${card("Open jobs by status", "", donut(s.by_status, { label: "open jobs",
				href: (i) => listUrl("Asset Service Record", withCo({ status: i.name, docstatus: 0 })),
				drill: (i) => ({ k: "open_service", g: "status", v: i.name, t: "Open service jobs", s: i.name }) }))}
		</div>
		${card(`Service cost · ${periodLabel()}`, `<span class="ua-legend"><span><i style="background:#0a0a0a"></i>Materials</span><span><i style="background:#228883"></i>Services</span></span>`, columns(s.by_month))}
		<div class="ua-r2e">
			${card("Cost by service type", "", hbars(s.by_type, { cls: "sig", href: (i) => listUrl("Asset Service Record", withCo({ service_type: i.name, docstatus: 1 })), sub: (i) => `${i.count} jobs`,
				drill: (i) => ({ k: "done", g: "service_type", v: i.name, t: "Service jobs completed", s: `${i.name} · ${periodLabel()}` }) }))}
			${card("Overdue maintenance tasks", more(listUrl("Asset Maintenance Log", { maintenance_status: "Overdue" })), overdue || `<div class="ua-empty">No overdue tasks.</div>`)}
		</div>
		<div class="ua-r2e">
			${card(`Check sheets · ${periodLabel()}`, `${num(k.checks_passed_30d)} of ${num(k.checks_30d)} passed`,
				donut(s.check_status, { label: "checks", href: (i) => listUrl("Asset Check Sheet", withCo({ overall_status: i.name, docstatus: 1 })),
					drill: (i) => ({ k: "sheets", g: "overall_status", v: i.name, t: "Check sheets", s: `${i.name} · ${periodLabel()}` }) }))}
			${card("Open issues & repairs", "", (issues + repairs) || `<div class="ua-empty">No open check-sheet issues or repairs.</div>`)}
		</div>`;
	}

	function viewInsurance(d) {
		const k = d.kpis, ins = d.insurance;
		const expiring = ins.expiring.map((p) => row(p.days <= 7 ? "bad" : "warn", `${p.days}d`, p.policy_number || p.name,
			`${esc(p.policy_type)} · ${esc(p.insurer_name || "")}`, `${statusBadge(p.status)}<small>${fmtDate(p.end_date)}</small>`,
			docUrl("Insurance Policy", p.name))).join("");
		const claims = ins.open_claims.map((c) => row("", c.incident_type ? esc(c.incident_type.slice(0, 2).toUpperCase()) : "CL", c.asset_name || c.asset,
			`${esc(c.name)} · ${fmtDate(c.incident_date)}`, `${esc(cur())} ${compact(c.outstanding_amount || c.amount_approved || c.amount_claimed)}<small>${esc(c.status)}</small>`,
			docUrl("Insurance Claim", c.name))).join("");
		const under = ins.under.map((a) => row("warn", "UI", a.asset_name || a.name, `Insured ${esc(cur())} ${compact(a.insured_value)} · book ${compact(a.value_after_depreciation)}`,
			`<span class="ua-down">−${compact(a.gap)}</span>`, docUrl("Asset", a.name))).join("");
		const unins = ins.uninsured.map((a) => row("bad", "!", a.asset_name || a.name, esc(a.asset_category || ""),
			`<span class="ua-badge bad">No cover</span>`, docUrl("Asset", a.name))).join("");
		return `
		<div class="ua-kpis">
			${kpi("Active policies", num(k.active_policies), `${num(k.expiring_policies)} expiring within 30 days`, listUrl("Insurance Policy", withCo({ docstatus: 1 })), null, { k: "policies" })}
			${kpi("Assets sum insured", money(k.sum_insured), `Book value ${esc(cur())} ${compact(k.book_value)}${k.other_sum_insured
				? ` · other cover ${compact(k.other_sum_insured)}` : ""}`, reportUrl("Insurance Register", withCo()),
				[pct(k.sum_insured, k.book_value), k.sum_insured < k.book_value ? "bad" : "good"], { k: "asset_policies" })}
			${kpi("Premium", money(k.premium_year), `Policies starting ${esc(periodLabel())}`, reportUrl("Premium Analysis", withCo()), null, { k: "premium", s: periodLabel() })}
			${kpi("Open claims", num(k.open_claims), `${esc(cur())} ${compact(k.recoverable)} still due from insurers`, listUrl("Insurance Claim", withCo()), null, { k: "open_claims" })}
			${kpi("Uninsured assets", `<span class="${k.uninsured ? "ua-down" : ""}">${num(k.uninsured)}</span>`, "Require cover but have none",
				reportUrl("Uninsured Assets", withCo()), null, { k: "uninsured" })}
		</div>
		<div class="ua-r2">
			${card("Renewals in the next 60 days", more(listUrl("Insurance Policy", withCo({ docstatus: 1 }))), expiring || `<div class="ua-empty">No policy ends in the next 60 days.</div>`)}
			${card("Cover by policy type", "", donut(ins.by_type, { valueKey: "value", label: "sum insured",
				href: (i) => listUrl("Insurance Policy", withCo({ policy_type: i.name, docstatus: 1 })),
				drill: (i) => ({ k: "policies", g: "policy_type", v: i.name, t: "Active policies", s: i.name }) }))}
		</div>
		<div class="ua-r2e">
			${card(`Claims · ${periodLabel()}`, more(reportUrl("Insurance Claims Register", withCo())), donut(ins.claims_by_status, { label: "claims",
				href: (i) => listUrl("Insurance Claim", withCo({ status: i.name })),
				drill: (i) => ({ k: "claims", g: "status", v: i.name, t: "Claims", s: `${i.name} · ${periodLabel()}` }) }))}
			${card("Open claims", more(listUrl("Insurance Claim", withCo())), claims || `<div class="ua-empty">No open claims.</div>`)}
		</div>
		<div class="ua-r3">
			${card("Premium by insurer", esc(periodLabel()), hbars(ins.premium_by_insurer, { href: (i) => listUrl("Insurance Policy", withCo({ insurer_name: i.name })), sub: (i) => `${i.count} policies`,
				drill: (i) => ({ k: "premium", g: "insurer_name", v: i.name, t: "Premium", s: `${i.name} · ${periodLabel()}` }) }))}
			${card("Under-insured", more(reportUrl("Insurance Register", withCo())), under || `<div class="ua-empty">All insured at or above book value.</div>`)}
			${card("Uninsured", more(reportUrl("Uninsured Assets", withCo())), unins || `<div class="ua-empty">Every asset that needs cover has it.</div>`)}
		</div>`;
	}

	// ── calendar ─────────────────────────────────────────────────────────
	const typeColor = Object.fromEntries(EVENT_TYPES.map(([t, , c]) => [t, c]));
	const ME = root.dataset.uid || "";
	const initialsOf = (n) => String(n || "?").split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
	const who = (e) => (e.assignee ? `<span class="ua-who" title="${esc(e.assignee_name)}"><b>${esc(initialsOf(e.assignee_name))}</b><span>${esc(e.assignee_name)}</span></span>`
		: `<span class="ua-who none">Unassigned</span>`);
	function eventRow(e) {
		const d = daysFrom(e.date);
		const cls = e.overdue ? "bad" : e.type === "renewal" ? "warn" : e.type === "service" ? "good" : "";
		const label = { renewal: "RN", service: "SV", maintenance: "MT", todo: "TD" }[e.type] || "•";
		return row(cls, label, e.title, `${esc(e.sub || "")}${e.assignee_name ? ` · ${esc(e.assignee_name)}` : ""}`,
			`${dueBadge(e.overdue && d >= 0 ? -1 : d)}<small>${fmtDate(e.date, { day: "numeric", month: "short" })}</small>`,
			docUrl(e.doctype, e.name));
	}

	// Calendar range for the current view: Month (whole weeks), Week, or Day.
	const lead = (d) => (d.getDay() - state.fdow + 7) % 7; // position of a day within the instance's week
	const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
	function gridRange() {
		const a = state.cal.anchor;
		if (state.cal.view === "day") return [new Date(a), new Date(a)];
		if (state.cal.view === "week") { const s = addDays(a, -lead(a)); return [s, addDays(s, 6)]; }
		const first = new Date(a.getFullYear(), a.getMonth(), 1), last = new Date(a.getFullYear(), a.getMonth() + 1, 0);
		return [addDays(first, -lead(first)), addDays(last, 6 - lead(last))];
	}
	function shiftCal(step) {
		const a = state.cal.anchor;
		state.cal.anchor = state.cal.view === "month" ? new Date(a.getFullYear(), a.getMonth() + step, 1)
			: addDays(a, step * (state.cal.view === "week" ? 7 : 1));
	}
	async function loadEvents() {
		const [start, end] = gridRange();
		state.cal.events = (await call("get_events", { company: company(), start: iso(start), end: iso(end), ...dimArgs() })) || [];
	}
	function calVisible() {
		const w = state.cal.who, q = state.cal.q.trim().toLowerCase();
		return state.cal.events.filter((e) => !state.cal.hidden.has(e.type)
			&& (w ? (w === "__me" ? e.assignee === ME : w === "__none" ? !e.assignee : e.assignee === w)
				: !q || String(e.assignee_name || "").toLowerCase().includes(q)));
	}
	function calTitle() {
		const [s, e] = gridRange();
		if (state.cal.view === "day") return fmtDate(iso(s), { weekday: "long", day: "numeric", month: "long", year: "numeric" });
		if (state.cal.view === "week") {
			const sameMonth = s.getMonth() === e.getMonth();
			return `${s.getDate()}${sameMonth ? "" : " " + s.toLocaleString("en", { month: "short" })} – ${fmtDate(iso(e), { day: "numeric", month: "short", year: "numeric" })}`;
		}
		return state.cal.anchor.toLocaleString("en", { month: "long", year: "numeric" });
	}
	function calPeople() {
		const people = {};
		state.cal.events.forEach((e) => { if (e.assignee) people[e.assignee] = e.assignee_name || e.assignee; });
		return Object.entries(people).sort((a, b) => (a[0].startsWith("role:") - b[0].startsWith("role:")) || a[1].localeCompare(b[1]));
	}
	function whoLabel() {
		const w = state.cal.who;
		if (!w) return state.cal.q;
		if (w === "__me") return "Assigned to me";
		if (w === "__none") return "Unassigned";
		return (calPeople().find(([u]) => u === w) || [w, w])[1];
	}
	function whoMenu() {
		const q = (state.cal.who ? "" : state.cal.q).trim().toLowerCase();
		const count = (fn) => state.cal.events.filter((e) => !state.cal.hidden.has(e.type) && fn(e)).length;
		const opts = [["__me", "Assigned to me", count((e) => e.assignee === ME)], ["__none", "Unassigned", count((e) => !e.assignee)],
			...calPeople().map(([u, n]) => [u, n, count((e) => e.assignee === u)])]
			.filter(([v, l]) => !q || l.toLowerCase().includes(q) || (v === "__me" && "me".includes(q)));
		return opts.length ? opts.map(([v, l, n]) => `<button type="button" class="${v === state.cal.who ? "on" : ""}" data-act="cal-who" data-who="${esc(v)}">
			${v.startsWith("__") ? "" : `<b>${esc(initialsOf(l))}</b>`}<span>${esc(l)}</span><small>${n}</small></button>`).join("")
			: `<div class="ua-who__nomatch">No one matches “${esc(q)}”</div>`;
	}
	const evChip = (e) => `<div class="ua-ev ${e.type} ${e.overdue ? "overdue" : ""}" data-href="${esc(docUrl(e.doctype, e.name))}"
		data-tip="${esc(`<b>${esc(e.title)}</b>${esc(e.sub || "")}${e.assignee_name ? "<br>" + esc(e.assignee_name) : ""}`)}"><i></i><span>${esc(e.title)}</span></div>`;
	const evCard = (e, full) => `<a class="ua-evc ${e.type} ${e.overdue ? "overdue" : ""}" href="${esc(docUrl(e.doctype, e.name))}">
		<div class="ua-evc__t"><i></i><span>${esc(e.title)}</span></div>
		${e.sub ? `<div class="ua-evc__s">${esc(e.sub)}</div>` : ""}
		<div class="ua-evc__f">${who(e)}${e.overdue ? `<span class="ua-badge bad">Overdue</span>` : e.status ? `<span class="ua-badge">${esc(e.status)}</span>` : ""}</div>
		${full ? `<div class="ua-evc__k">${esc({ renewal: "Policy renewal", service: "Service job", maintenance: "Maintenance task", todo: "ToDo" }[e.type] || "")} · ${esc(e.doctype)} ${esc(e.name)}</div>` : ""}
	</a>`;

	function viewCalendar() {
		const [start, end] = gridRange();
		const today = iso(new Date());
		const evs = calVisible();
		const byDay = {};
		evs.forEach((e) => (byDay[e.date] = byDay[e.date] || []).push(e));
		const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
		const dows = DOW.map((_, i) => DOW[(i + state.fdow) % 7]);
		let body = "";
		if (state.cal.view === "month") {
			let cells = dows.map((d) => `<div class="ua-cal__dow">${d}</div>`).join("");
			for (let dt = new Date(start); dt <= end; dt.setDate(dt.getDate() + 1)) {
				const key = iso(dt), list = byDay[key] || [];
				const out = dt.getMonth() !== state.cal.anchor.getMonth(), wk = dt.getDay() === 0 || dt.getDay() === 6;
				cells += `<div class="ua-cal__d ${out ? "out" : ""} ${wk ? "wk" : ""} ${key === today ? "today" : ""}" data-day="${key}">
					<div class="ua-cal__n">${dt.getDate()}</div>${list.slice(0, 4).map(evChip).join("")}
					${list.length > 4 ? `<div class="ua-ev__more">+${list.length - 4} more</div>` : ""}</div>`;
			}
			body = `<div class="ua-cal">${cells}</div>`;
		} else if (state.cal.view === "week") {
			let cols = "";
			for (let dt = new Date(start); dt <= end; dt.setDate(dt.getDate() + 1)) {
				const key = iso(dt), list = byDay[key] || [];
				cols += `<div class="ua-wk__col ${key === today ? "today" : ""}">
					<div class="ua-wk__h" data-day="${key}"><span>${DOW[dt.getDay()]}</span><b>${dt.getDate()}</b></div>
					<div class="ua-wk__b">${list.map((e) => evCard(e)).join("") || `<div class="ua-wk__none">—</div>`}</div></div>`;
			}
			body = `<div class="ua-wk">${cols}</div>`;
		} else {
			const list = byDay[iso(start)] || [];
			const groups = EVENT_TYPES.map(([t, label]) => [label, list.filter((e) => e.type === t)]).filter(([, l]) => l.length);
			body = groups.length ? `<div class="ua-dayv">${groups.map(([label, l]) => `
				<div class="ua-dayv__g"><div class="ua-ag__day"><span>${esc(label)}</span><span>${l.length}</span></div>
				<div class="ua-dayv__l">${l.map((e) => evCard(e, true)).join("")}</div></div>`).join("")}</div>`
				: `<div class="ua-empty" style="padding:60px 0">Nothing scheduled${state.cal.who ? " for this person" : ""} on this day.</div>`;
		}
		const legend = EVENT_TYPES.map(([t, label, c]) => `<span data-type="${t}" class="${state.cal.hidden.has(t) ? "off" : ""}"><i style="background:${c}"></i>${label}
			<b>${evs.filter((e) => e.type === t).length}</b></span>`).join("") + `<span style="cursor:default"><i style="background:#c4302b"></i>Overdue</span>`;
		const views = [["month", "Month"], ["week", "Week"], ["day", "Day"]].map(([v, l]) =>
			`<button class="${state.cal.view === v ? "on" : ""}" data-act="cal-view" data-view="${v}">${l}</button>`).join("");
		return `<section class="ua-card ua-cal__card">
			<div class="ua-cal__bar">
				<div class="ua-cal__m">${esc(calTitle())}</div>
				<div class="ua-cal__tools">
					<div class="ua-cal__who ${state.cal.who || state.cal.q ? "set" : ""}" title="Assigned to">
						<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>
						<input type="search" data-el="calq" placeholder="Everyone · type a name" value="${esc(whoLabel())}" autocomplete="off" aria-label="Filter by assigned person">
						${state.cal.who || state.cal.q ? `<button type="button" class="ua-who__x" data-act="cal-who" data-who="" title="Show everyone">✕</button>` : ""}
						<div class="ua-who__menu" data-el="whomenu">${whoMenu()}</div>
					</div>
					<div class="ua-seg">${views}</div>
					<div class="ua-cal__nav"><button class="ua-btn" data-act="cal-prev" title="Previous">‹</button><button class="ua-btn dark" data-act="cal-today">Today</button><button class="ua-btn" data-act="cal-next" title="Next">›</button></div>
				</div>
			</div>
			${body}
			<div class="ua-cal__lg">${legend}</div>
		</section>`;
	}
	async function openDrill(el) {
		const spec = JSON.parse(el.dataset.drill);
		const href = el.dataset.href || el.getAttribute("href") || "";
		const pop = $("pop");
		const head = `<div class="ua-pop__h"><div><div class="ua-dh__e">${esc(spec.s || "")}</div><h2 class="ua-dh__t">${esc(spec.t || "")}</h2></div>
			<button class="ua-icon ua-close" data-act="close" title="Close">✕</button></div>`;
		pop.innerHTML = `${head}<div class="ua-pop__b"><div class="ua-state"><div class="ua-loader"></div>Loading…</div></div>`;
		pop.classList.add("on");
		root.querySelector(".ua-scrim").classList.add("on");
		const [from_date, to_date] = period();
		try {
			const r = await call("get_drilldown", { key: spec.k, group: spec.g, value: spec.v, company: company(), from_date, to_date, ...dimArgs() });
			if (!r || !pop.classList.contains("on")) return;
			const rows = r.rows.map((x) => row(x.status === "Overdue" || x.status === "No cover" ? "bad" : "", esc(x.doctype.split(" ").map((w) => w[0]).join("").slice(0, 2)),
				x.title, `${esc(x.sub)}${x.date ? " · " + fmtDate(x.date) : ""}`,
				`${x.amount ? `<b>${esc(r.currency)} ${num(x.amount)}</b>` : ""}${x.status ? statusBadge(x.status) : ""}`, docUrl(x.doctype, x.name))).join("");
			pop.innerHTML = `${head}
				<div class="ua-pop__sum"><span><b>${num(r.count)}</b> record${r.count === 1 ? "" : "s"}</span>${r.total ? `<span>Total <b>${esc(r.currency)} ${num(r.total)}</b></span>` : ""}
					${r.count > r.rows.length ? `<span>Showing first ${r.rows.length}</span>` : ""}</div>
				<div class="ua-pop__b">${rows || `<div class="ua-empty">No records.</div>`}</div>
				${href ? `<div class="ua-pop__f"><a class="ua-btn dark" href="${esc(href)}">Open full list in desk →</a></div>` : ""}`;
		} catch (e) {
			pop.querySelector(".ua-pop__b").innerHTML = `<div class="ua-state"><h3>Could not load</h3><p>${esc(e.message)}</p></div>`;
		}
	}
	const closeDrawer = () => {
		$("pop").classList.remove("on"); $("drawer").classList.remove("on"); root.querySelector(".ua-scrim").classList.remove("on"); };

	// ── render / load ────────────────────────────────────────────────────
	function render() {
		renderNav();
		syncUrl();
		const d = state.data;
		if (!d) return;
		if (!d.company) {
			$("body").innerHTML = `<div class="ua-state"><h3>No company available</h3><p>You do not have access to any company yet.</p></div>`;
			return;
		}
		const views = { overview: viewOverview, schedules: viewCalendar, maintenance: viewMaintenance, insurance: viewInsurance };
		$("body").innerHTML = views[state.tab](d);
	}
	async function load() {
		const btn = root.querySelector('[data-act="refresh"]');
		btn && btn.classList.add("spin");
		try {
			const today = new Date(), in30 = new Date(); in30.setDate(today.getDate() + 30);
			if (!state.optsLoaded) {
				const opts = (await call("get_filter_options", { company: state.company })) || {};
				state.dims = opts.dimensions || [];
				if (Number.isInteger(opts.first_day_of_week)) state.fdow = opts.first_day_of_week;
				if (state.f.dimension && !state.dims.some((d) => d.fieldname === state.f.dimension)) { state.f.dimension = ""; state.f.dimvalue = ""; }
				if (!state.f.dimension && state.dims.length) state.f.dimension = state.dims[0].fieldname;
				state.optsLoaded = true;
				await loadDimValues();
			}
			const [from_date, to_date] = period();
			const data = await call("get_dashboard", { company: state.company, from_date, to_date, ...dimArgs() });
			if (!data) return;
			state.data = data;
			state.company = data.company || "";
			renderCompanies();
			const [agenda] = await Promise.all([
				call("get_events", { company: company(), start: iso(today), end: iso(in30), ...dimArgs() }),
				state.tab === "schedules" ? loadEvents() : null,
			]);
			state.agenda = agenda || [];
			render();
		} catch (e) {
			$("body").innerHTML = `<div class="ua-state"><h3>Could not load the dashboard</h3><p>${esc(e.message)}</p>
				<button class="ua-btn dark" data-act="refresh">Try again</button></div>`;
		} finally {
			btn && btn.classList.remove("spin");
		}
	}

	// ── events ───────────────────────────────────────────────────────────
	root.addEventListener("click", async (ev) => {
		const t = ev.target;
		const tab = t.closest("[data-tab]");
		if (tab) {
			state.tab = tab.dataset.tab;
			root.querySelector('[data-el="shell"]').classList.remove("menu");
			if (state.tab === "schedules" && !state.cal.events.length) await loadEvents();
			render();
			window.scrollTo({ top: 0, behavior: "smooth" });
			return;
		}
		const act = t.closest("[data-act]");
		if (act) {
			const a = act.dataset.act;
			if (a === "refresh") { state.cal.events = []; return load(); }
			if (a === "toggle-side") return setCollapsed(!root.querySelector('[data-el="shell"]').classList.contains("collapsed"));
			if (a === "menu") return root.querySelector('[data-el="shell"]').classList.toggle("menu");
			if (a === "close") return closeDrawer();
			if (a === "cal-who") {
				state.cal.who = act.dataset.who;
				state.cal.q = "";
				store.set("calWho", state.cal.who);
				render();
				if (!state.cal.who) { const i = root.querySelector('[data-el="calq"]'); i && i.focus(); }
				else document.activeElement && document.activeElement.blur();
				return;
			}
			if (a.startsWith("cal-")) {
				if (a === "cal-view") { state.cal.view = act.dataset.view; store.set("calView", state.cal.view); }
				else if (a === "cal-today") { const t = new Date(); state.cal.anchor = new Date(t.getFullYear(), t.getMonth(), t.getDate()); }
				else shiftCal(a === "cal-next" ? 1 : -1);
				await loadEvents();
				return render();
			}
		}
		const lg = t.closest(".ua-cal__lg [data-type]");
		if (lg) {
			const type = lg.dataset.type;
			state.cal.hidden.has(type) ? state.cal.hidden.delete(type) : state.cal.hidden.add(type);
			return render();
		}
		const drill = t.closest("[data-drill]");
		if (drill && !ev.ctrlKey && !ev.metaKey) {
			ev.preventDefault();
			return openDrill(drill);
		}
		if (drill) { ev.preventDefault(); return window.open(drill.dataset.href || drill.getAttribute("href"), "_blank"); }
		const link = t.closest("[data-href]");
		if (link) {
			ev.preventDefault();
			const href = link.dataset.href;
			return ev.ctrlKey || ev.metaKey ? window.open(href, "_blank") : (location.href = href);
		}
		const day = t.closest("[data-day]");
		if (day) { // a day opens the Day view, with owners and details
			state.cal.anchor = parse(day.dataset.day);
			state.cal.view = "day";
			store.set("calView", "day");
			await loadEvents();
			return render();
		}
	});
	$("company").addEventListener("change", async (e) => {
		state.company = e.target.value;
		state.cal.events = [];
		state.f.dimvalue = "";
		await loadDimValues();
		load();
	});
	const refilter = () => { saveFilters(); state.cal.events = []; load(); };
	// Typing in the Assigned to box filters live by name; the calendar redraws and the caret stays put.
	root.addEventListener("input", (e) => {
		if (!e.target.matches('[data-el="calq"]')) return;
		const pos = e.target.selectionStart;
		state.cal.q = e.target.value;
		if (state.cal.who) { state.cal.who = ""; store.set("calWho", ""); }
		render();
		const i = root.querySelector('[data-el="calq"]');
		if (i) { i.focus(); i.setSelectionRange(pos, pos); }
	});
	root.addEventListener("keydown", (e) => {
		if (!e.target.matches('[data-el="calq"]')) return;
		if (e.key === "Enter") { // pick the first suggestion
			const first = root.querySelector('[data-el="whomenu"] [data-act="cal-who"]');
			if (first) { e.preventDefault(); first.click(); }
		} else if (e.key === "Escape" && (state.cal.q || state.cal.who)) {
			e.stopPropagation();
			state.cal.q = ""; state.cal.who = ""; store.set("calWho", "");
			render();
		}
	});
	// Keep the box focused while a suggestion is clicked.
	root.addEventListener("mousedown", (e) => { if (e.target.closest(".ua-who__menu")) e.preventDefault(); });
	$("dimension").addEventListener("change", async (e) => {
		state.f.dimension = e.target.value;
		state.f.dimvalue = "";
		await loadDimValues();
		refilter();
	});
	$("dimvalue").addEventListener("change", (e) => { state.f.dimvalue = e.target.value; refilter(); });
	$("preset").addEventListener("change", (e) => {
		if (e.target.value === "custom") { [state.f.from, state.f.to] = period(); }
		state.f.preset = e.target.value;
		if (state.f.preset === "custom") { renderFilters(); saveFilters(); return; } // wait for dates
		refilter();
	});
	["from", "to"].forEach((k) => $(k).addEventListener("change", (e) => {
		state.f[k] = e.target.value;
		if (state.f.from && state.f.to) refilter();
	}));

	function setCollapsed(on) {
		const shell = root.querySelector('[data-el="shell"]');
		shell.classList.toggle("collapsed", on);
		const btn = root.querySelector('[data-act="toggle-side"]');
		if (btn) btn.title = on ? "Expand sidebar" : "Collapse sidebar";
		root.querySelector(".ua-collapse__lbl").textContent = on ? "Expand" : "Collapse";
		store.set("collapsed", on);
	}
	if (store.get("collapsed")) setCollapsed(true);
	document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

	// tooltip
	const tip = $("tip");
	root.addEventListener("mousemove", (e) => {
		const el = e.target.closest("[data-tip]");
		if (!el) return tip.classList.remove("on");
		tip.innerHTML = el.dataset.tip;
		tip.classList.add("on");
		const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 10);
		tip.style.left = `${x}px`;
		tip.style.top = `${e.clientY + 16}px`;
	});
	root.addEventListener("mouseleave", () => tip.classList.remove("on"));

	renderNav();
	load();
})();
