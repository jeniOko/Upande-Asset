// Why-Why Analysis tab helpers (Asset Check Sheet + Asset Repair).
//  - Step cards in the guide jump to and highlight their field.
//  - "See an example" shows a worked example *inside* the real fields as
//    placeholders / greyed rows; nothing is saved to the document.
frappe.provide("upande_asset.why_why");

upande_asset.why_why = {
	EXAMPLE: {
		problem_statement: "e.g. Tractor lost hydraulic lift in the field on Tuesday morning.",
		whys: [
			"Because the hydraulic hose burst.",
			"Because the hose was rubbing on the frame.",
			"Because its clamp was missing after the last service.",
			"Because hose clamps are not on the service checklist.",
		],
		cause_type: "Maintenance",
		action: { action: "Add “check hose clamps” to the 250-hour service sheet", owner: "Workshop Lead", due: "Friday", status: "Open" },
	},

	FIELDS: ["why_why_tab", "why_why_section", "problem_statement", "why_why_analysis", "root_cause_section", "root_cause",
		"root_cause_category", "corrective_actions_section", "corrective_actions"],

	setup(frm) {
		const help = frm.fields_dict.why_why_help;
		if (!help) return;
		// Asset Settings → Display → Show the step guide (fetched once per session).
		if (upande_asset.why_why.__show === undefined) {
			upande_asset.why_why.__show = frappe.xcall("upande_asset.upande_asset.rca.show_guide").catch(() => true);
		}
		upande_asset.why_why.__show.then((r) => {
			// Older responses were a plain boolean (the guide switch only).
			const opts = typeof r === "object" && r !== null ? r : { enabled: true, guide: !!r };
			frm.toggle_display("why_why_help", !!opts.guide);
			// Asset Settings → Display → Use Why-Why analysis: hide the whole tab.
			frm.toggle_display(upande_asset.why_why.FIELDS, !!opts.enabled);
		});
		const $w = help.$wrapper;
		$w.off(".uaww");
		$w.on("click.uaww", ".ua-ww-step[data-target]", (e) => {
			let target = $(e.currentTarget).attr("data-target");
			if (target === "root_cause" && !frm.doc.root_cause) target = "why_why_analysis";
			this.highlight(frm, target);
		});
		$w.on("click.uaww", ".ua-ww-ex-toggle", (e) => {
			e.preventDefault();
			frm.__ua_ww_example = !frm.__ua_ww_example;
			this.apply(frm);
		});
		this.apply(frm);
	},

	highlight(frm, fieldname) {
		const field = frm.fields_dict[fieldname];
		if (!field || !field.$wrapper) return;
		const is_text = ["Small Text", "Text", "Data"].includes(field.df.fieldtype) && !field.df.read_only;
		frm.scroll_to_field(fieldname, is_text);
		const $el = field.$wrapper;
		$el.removeClass("ua-ww-flash");
		void $el[0].offsetWidth; // restart the animation
		$el.addClass("ua-ww-flash");
		setTimeout(() => $el.removeClass("ua-ww-flash"), 1800);
	},

	apply(frm) {
		const on = !!frm.__ua_ww_example;
		const ex = this.EXAMPLE;
		const help = frm.fields_dict.why_why_help;
		help.$wrapper.find(".ua-ww-ex-toggle").text(on ? __("Hide example") : __("See an example"));
		help.$wrapper.find(".ua-ww-ex-note").toggle(on);

		// 1. What happened? – placeholder text in the real box
		const ps = frm.fields_dict.problem_statement;
		if (ps && ps.$input) ps.$input.attr("placeholder", on ? ex.problem_statement : "");

		// 2. Why table – greyed example rows while the table is empty
		this.ghost_rows(frm, "why_why_analysis", on, ex.whys.map((w, i) =>
			`<div class="ua-ghost-row"><span class="ua-ghost-no">${i + 1}</span><span class="ua-ghost-text">${frappe.utils.escape_html(w)}</span>${
				i === ex.whys.length - 1 ? `<span class="ua-ghost-tag">${__("root cause")}</span>` : ""}</div>`).join(""));

		// 3. Cause Type – inline hint
		const ct = frm.fields_dict.root_cause_category;
		if (ct && ct.$wrapper) {
			ct.$wrapper.find(".ua-ghost-inline").remove();
			if (on && !frm.doc.root_cause_category) {
				ct.$wrapper.find(".control-input-wrapper").append(
					`<div class="ua-ghost-inline">${__("e.g. {0}", [__(ex.cause_type)])}</div>`);
			}
		}

		// 4. Actions table – greyed example row while empty
		const a = ex.action;
		this.ghost_rows(frm, "corrective_actions", on,
			`<div class="ua-ghost-row ua-ghost-cols"><span class="ua-ghost-no">1</span><span class="ua-ghost-text">${frappe.utils.escape_html(a.action)}</span>`
			+ `<span>${frappe.utils.escape_html(a.owner)}</span><span>${frappe.utils.escape_html(a.due)}</span><span>${__(a.status)}</span></div>`);
	},

	ghost_rows(frm, fieldname, on, html) {
		const field = frm.fields_dict[fieldname];
		if (!field || !field.grid) return;
		const $empty = field.grid.wrapper.find(".grid-empty");
		$empty.siblings(".ua-ghost").remove();
		// Only while the table is still empty – real rows replace the example.
		const show = on && !(frm.doc[fieldname] || []).length;
		$empty.toggleClass("ua-ghost-host", show);
		if (show) $empty.after(`<div class="ua-ghost"><div class="ua-ghost-label">${__("Example")}</div>${html}</div>`);
	},
};

// Re-apply after rows are added / removed (Frappe re-renders the grid).
["Asset Check Sheet", "Asset Repair"].forEach((dt) => {
	frappe.ui.form.on(dt, {
		why_why_analysis_add: (frm) => upande_asset.why_why.apply(frm),
		why_why_analysis_remove: (frm) => upande_asset.why_why.apply(frm),
		corrective_actions_add: (frm) => upande_asset.why_why.apply(frm),
		corrective_actions_remove: (frm) => upande_asset.why_why.apply(frm),
		root_cause_category: (frm) => upande_asset.why_why.apply(frm),
	});
});
