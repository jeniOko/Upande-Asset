// Copyright (c) 2026, jeniffer@upande.com,wycliffe@upande.com and contributors
// For license information, please see license.txt

const SECTIONS = ["a", "b", "c", "d", "e", "f"];
const FAULT_STATUSES = ["Fault / Defect", "Affected"];

// ── Main form events ──────────────────────────────────────────────────────────

frappe.ui.form.on("Asset Check Sheet", {
	refresh(frm) {
		set_asset_filter(frm);
		set_template_filter(frm);

		if (frm.doc.docstatus !== 1) return;

		// "View" group — quick link to the repair already created from this sheet
		if (frm.doc.linked_asset_repair) {
			frm.add_custom_button(__("Asset Repair"), function () {
				frappe.set_route("Form", "Asset Repair", frm.doc.linked_asset_repair);
			}, __("View"));
		}

		// "Create" group — available on any submitted doc (not gated by status).
		// Asset Repair: one per check sheet — hidden once a repair is linked.
		if (!frm.doc.linked_asset_repair) {
			frm.add_custom_button(__("Asset Repair"), function () {
				create_asset_repair(frm);
			}, __("Create"));
		}

		// Maintenance Task: always available regardless of repair/maintenance state.
		frm.add_custom_button(__("Maintenance Task"), function () {
			create_or_open_maintenance(frm);
		}, __("Create"));

		// Make the Create dropdown visually prominent
		frm.page.get_inner_group_button(__("Create"))
			.find("button")
			.removeClass("btn-default")
			.addClass("btn-primary");
	},

	check_date(frm) {
		if (frm.doc.check_date && frm.doc.check_date > frappe.datetime.get_today()) {
			frappe.msgprint({
				title: __("Invalid Date"),
				message: __("Check date cannot be in the future. Checks must be recorded on or before today."),
				indicator: "red",
			});
			frm.set_value("check_date", frappe.datetime.get_today());
		}
	},

	asset_category(frm) {
		frm.set_value("asset", "");
		frm.set_value("asset_name", "");
		frm.set_value("check_sheet_template", "");
		clear_all_sections(frm);
		set_asset_filter(frm);
		set_template_filter(frm);
		fetch_default_template(frm);
	},

	asset(frm) {
		if (frm.doc.asset) {
			frappe.db.get_value("Asset", frm.doc.asset, "asset_name", function (r) {
				frm.set_value("asset_name", r.asset_name || "");
			});
		} else {
			frm.set_value("asset_name", "");
		}
	},

	check_sheet_template(frm) {
		if (frm.doc.check_sheet_template) {
			populate_from_template(frm);
		} else {
			clear_all_sections(frm);
		}
	},
});

// ── Child table events ────────────────────────────────────────────────────────

frappe.ui.form.on("Asset Check Sheet Item", {
	incident_percent(_frm, cdt, cdn) {
		recalc_threshold(cdt, cdn);
	},

	status(_frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!FAULT_STATUSES.includes(row.status)) {
			frappe.model.set_value(cdt, cdn, "incident_percent", null);
			frappe.model.set_value(cdt, cdn, "percent_above_threshold", null);
			frappe.model.set_value(cdt, cdn, "severity", "");
			frappe.model.set_value(cdt, cdn, "fault_description", "");
			frappe.model.set_value(cdt, cdn, "proposed_action", "");
		}
	},
});

// ── Filters & template helpers ────────────────────────────────────────────────

function set_asset_filter(frm) {
	frm.set_query("asset", function () {
		return { filters: { asset_category: frm.doc.asset_category || "__none__" } };
	});
}

function set_template_filter(frm) {
	frm.set_query("check_sheet_template", function () {
		return { filters: { asset_category: frm.doc.asset_category || "__none__" } };
	});
}

function fetch_default_template(frm) {
	if (!frm.doc.asset_category) return;
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Asset Check Sheet Template",
			filters: { asset_category: frm.doc.asset_category, is_default: 1 },
			fields: ["name"],
			limit: 1,
		},
		callback(r) {
			if (r.message && r.message.length) {
				frm.set_value("check_sheet_template", r.message[0].name);
			}
		},
	});
}

function populate_from_template(frm) {
	if (!frm.doc.check_sheet_template) return;
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "Asset Check Sheet Template", name: frm.doc.check_sheet_template },
		callback(r) {
			if (!r.message) return;
			const tmpl = r.message;
			clear_all_sections(frm);
			SECTIONS.forEach(function (sec) {
				const name_key  = "section_" + sec + "_name";
				const items_key = "section_" + sec + "_items";
				const label_key = "section_" + sec + "_label";
				if (tmpl[name_key]) {
					frm.set_value(label_key, tmpl[name_key]);
				}
				(tmpl[items_key] || []).forEach(function (src) {
					const row = frm.add_child(items_key);
					row.check_factor      = src.check_factor;
					row.threshold_percent = src.threshold_percent || null;
				});
			});
			frm.refresh_fields();
		},
	});
}

function clear_all_sections(frm) {
	SECTIONS.forEach(function (sec) {
		frm.set_value("section_" + sec + "_label", "");
		frm.set_value("section_" + sec + "_items", []);
	});
}

function recalc_threshold(cdt, cdn) {
	const row = locals[cdt][cdn];
	if (row.threshold_percent && row.incident_percent != null) {
		const above = flt(row.incident_percent) - flt(row.threshold_percent);
		frappe.model.set_value(cdt, cdn, "percent_above_threshold", above > 0 ? above : 0);
	} else {
		frappe.model.set_value(cdt, cdn, "percent_above_threshold", null);
	}
}

// ── Create: Asset Repair ──────────────────────────────────────────────────────
// Server builds the full doc (asset, asset_name, company, failure_date,
// description from fault rows). frappe.model.sync registers it in the client
// model before the form loads — no setTimeout hacks needed.

function create_asset_repair(frm) {
	frappe.call({
		method: "upande_asset.upande_asset.doctype.asset_check_sheet.asset_check_sheet.make_asset_repair",
		args: { check_sheet: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const doclist = frappe.model.sync(r.message);
			frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
		},
	});
}

// ── Create / Open: Maintenance Task ──────────────────────────────────────────
// If an Asset Maintenance record already exists for this asset, confirms before
// opening it (user can manually add the tasks).
// If none exists, server builds the parent doc; fault rows become maintenance
// tasks added via frappe.model.add_child before routing to the form.

function create_or_open_maintenance(frm) {
	const fault_rows = collect_fault_rows(frm);
	if (!fault_rows.length) {
		frappe.msgprint(
			__("No fault rows found. Mark at least one row as Fault / Defect or Affected."),
			__("Nothing to Create")
		);
		return;
	}
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Asset Maintenance",
			filters: { asset_name: frm.doc.asset },
			fields: ["name"],
			limit: 1,
		},
		callback(r) {
			if (r.message && r.message.length) {
				_open_existing_maintenance(r.message[0].name, fault_rows);
			} else {
				_create_new_maintenance(frm, fault_rows);
			}
		},
	});
}

function _open_existing_maintenance(existing_name, fault_rows) {
	const task_summary = fault_rows.map(function (r) {
		return "- [" + r.section + "] " + r.check_factor + ": " + r.proposed_action;
	}).join("\n");
	frappe.confirm(
		__("Asset Maintenance {0} already exists for this asset. Open it to add the new tasks?",
			[existing_name]),
		function () {
			frappe.show_alert({
				message: __("Proposed tasks to add:") + "<br><pre>" + task_summary + "</pre>",
				indicator: "blue",
			}, 20);
			frappe.set_route("Form", "Asset Maintenance", existing_name);
		}
	);
}

function _create_new_maintenance(frm, fault_rows) {
	frappe.call({
		method: "upande_asset.upande_asset.doctype.asset_check_sheet.asset_check_sheet.make_asset_maintenance",
		args: { check_sheet: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			// Safety: if another maintenance was created between the client check and
			// this server call, just open it.
			if (r.message.action === "open") {
				frappe.set_route("Form", "Asset Maintenance", r.message.name);
				return;
			}
			// Sync the parent doc into the client model.
			const doclist = frappe.model.sync(r.message.doc);
			const parent  = frappe.get_doc(doclist[0].doctype, doclist[0].name);

			// Add one maintenance task per fault row.
			fault_rows.forEach(function (row) {
				const task = frappe.model.add_child(
					parent, "Asset Maintenance Task", "asset_maintenance_tasks"
				);
				frappe.model.set_value(
					task.doctype, task.name,
					"maintenance_task",
					"[" + row.section + "] " + row.check_factor + ": " + row.proposed_action
				);
				frappe.model.set_value(task.doctype, task.name, "periodicity", "One Time");
			});

			frappe.set_route("Form", parent.doctype, parent.name);
		},
	});
}

// ── Utility ───────────────────────────────────────────────────────────────────

function collect_fault_rows(frm) {
	const results = [];
	SECTIONS.forEach(function (sec) {
		const label = frm.doc["section_" + sec + "_label"] || ("Section " + sec.toUpperCase());
		(frm.doc["section_" + sec + "_items"] || []).forEach(function (row) {
			if (FAULT_STATUSES.includes(row.status)) {
				results.push({
					section:           label,
					check_factor:      row.check_factor,
					proposed_action:   row.proposed_action || "",
					fault_description: row.fault_description || "",
					severity:          row.severity,
				});
			}
		});
	});
	return results;
}
