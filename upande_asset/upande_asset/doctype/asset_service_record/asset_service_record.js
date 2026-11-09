const ASR_API = "upande_asset.upande_asset.doctype.asset_service_record.asset_service_record";

frappe.ui.form.on("Asset Service Record", {
	setup(frm) {
		// "Complete" is the submit action here, so drop Frappe's "Submit this document to confirm" banner.
		frm.show_submit_message = () => {};
		frm.set_query("asset", () => ({ filters: { docstatus: 1 } }));
		frm.set_query("asset_maintenance_log", "tasks", (doc) => ({
			filters: { docstatus: 0, maintenance_status: ["in", ["Planned", "Overdue"]], ...(doc.asset ? { asset_name: doc.asset } : {}) },
		}));
		frm.set_query("purchase_invoice", "invoices", () => ({
			query: `${ASR_API}.get_purchase_invoice`,
			filters: { company: frm.doc.company, supplier: frm.doc.supplier },
		}));
		frm.set_query("expense_account", "invoices", (doc, cdt, cdn) => ({
			query: `${ASR_API}.get_expense_accounts`,
			filters: { purchase_invoice: locals[cdt][cdn].purchase_invoice },
		}));
	},

	onload_post_render(frm) {
		// Shared ERPNext child table – relabel its cost column for this form only.
		frm.fields_dict.invoices.grid.update_docfield_property("repair_cost", "label", __("Service Cost"));
	},

	invoices_remove: (frm) => refresh_total(frm),

	materials_add(frm, cdt, cdn) {
		const wh = (frm.doc.__onload || {}).service_materials_warehouse;
		if (wh && !locals[cdt][cdn].warehouse) frappe.model.set_value(cdt, cdn, "warehouse", wh);
	},

	service_by(frm) {
		apply_service_by(frm);
		if (frm.doc.service_by === "External") frm.trigger("service_type");  // offer the usual supplier
	},

	service_type(frm) {
		if (!frm.doc.service_type || frm.doc.docstatus !== 0) return;
		frappe.xcall(`${ASR_API}.get_service_type_defaults`, { service_type: frm.doc.service_type }).then((d) => {
			if (!d) return;
			if (!frm.doc.service_by && d.default_service_by) frm.set_value("service_by", d.default_service_by);
			const external = (frm.doc.service_by || d.default_service_by) === "External";
			if (external && !frm.doc.supplier && d.default_supplier) frm.set_value("supplier", d.default_supplier);
			if (external && d.default_service_item && !(frm.doc.materials || []).some((r) => r.item_code === d.default_service_item)) {
				const row = frm.add_child("materials", { item_code: d.default_service_item, qty: 1, estimated_rate: d.default_service_rate });
				frm.refresh_field("materials");
				frappe.model.trigger("item_code", d.default_service_item, row);
			}
		});
	},

	refresh(frm) {
		apply_service_by(frm);
		upande_asset.lock_requested_rows(frm, "materials");
		frm.set_indicator_formatter("item_code", (row) => {
			if (row.stock_entry || row.material_request) return "blue";  // already requested / issued
			if (!row.is_stock_item || !row.warehouse) return "gray";  // services: no stock to check
			return flt(row.actual_qty) >= flt(row.qty) ? "green" : "orange";
		});
		frm.set_query("item_code", "materials", () => ({ filters: { disabled: 0, has_variants: 0, is_fixed_asset: 0 } }));
		frm.set_query("warehouse", "materials", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
		if (frm.is_new()) return;

		// Status: Pending ⇄ In Progress while draft; Complete = submit.
		if (frm.doc.docstatus === 0) {
			if (frm.doc.status === "Pending") {
				frm.add_custom_button(__("Start Work"), () => change_status(frm, "In Progress"));
			} else if (frm.doc.status === "In Progress") {
				frm.add_custom_button(__("Back to Pending"), () => change_status(frm, "Pending"), __("Status"));
			}
			if (frm.perm[0] && frm.perm[0].submit && !frm.is_dirty()) {
				// Replaces the standard Submit button: completing the service is what submit means here.
				// Replaces the standard Submit button; one confirmation, then submit.
				frm.page.set_primary_action(__("Complete"), () => {
					frappe.confirm(__("Mark this service as Completed?{0} It can no longer be edited afterwards.",
						[(frm.doc.tasks || []).length ? " " + __("Its maintenance tasks are completed and the next ones scheduled.") : ""]),
						() => frm.save("Submit"));
				}, "check");
			}
		}

		// Material Requests (or a direct issue) from the Materials & Services rows.
		if (frm.doc.docstatus < 2 && frm.doc.service_by) {
			const open_rows = (frm.doc.materials || []).filter((r) => !r.material_request && !r.stock_entry);
			if (open_rows.length && (frm.doc.__onload || {}).allow_direct_material_issue) {
				frm.add_custom_button(__("Issue Used Materials"), () => issue_used(frm, open_rows), __("Material Request"));
			}
			if (open_rows.length) {
				const purposes = frm.doc.service_by === "External" ? ["Purchase"] : ["Material Issue", "Purchase"];
				purposes.forEach((purpose) => {
					const label = purpose === "Purchase"
						? (frm.doc.service_by === "External" ? __("Request Service Purchase") : __("Request Purchase"))
						: __("Request from Stores");
					frm.add_custom_button(label, () => make_request(frm, purpose, open_rows), __("Material Request"));
				});
			}
		}
		const open_tasks = (frm.doc.tasks || []).filter((t) => t.maintenance_task);
		if (open_tasks.length && frm.doc.docstatus === 0) {
			frm.dashboard.set_headline(open_tasks.length === 1
				? __("Completing this record completes maintenance task <b>{0}</b> (due {1}) and schedules the next one.",
					[frappe.utils.escape_html(open_tasks[0].maintenance_task), frappe.datetime.str_to_user(open_tasks[0].due_date)])
				: __("Completing this record completes {0} maintenance tasks and schedules their next occurrences.", [open_tasks.length]));
		}
	},
});

// ERPNext registers the same child events in asset_repair.js; act only on this form.
frappe.ui.form.on("Asset Repair Purchase Invoice", {
	purchase_invoice(frm, cdt, cdn) {
		if (frm.doctype !== "Asset Service Record") return;
		frappe.model.set_value(cdt, cdn, { expense_account: "", repair_cost: 0 });
	},

	expense_account(frm, cdt, cdn) {
		if (frm.doctype !== "Asset Service Record") return;
		const row = locals[cdt][cdn];
		if (!row.purchase_invoice || !row.expense_account) {
			frappe.model.set_value(cdt, cdn, "repair_cost", 0);
			return;
		}
		frappe.xcall(`${ASR_API}.get_unallocated_cost`, {
			purchase_invoice: row.purchase_invoice,
			expense_account: row.expense_account,
			service_record: frm.is_new() ? null : frm.doc.name,
		}).then((amount) => {
			frappe.model.set_value(cdt, cdn, "repair_cost", amount > 0 ? amount : 0);
			if (!(amount > 0)) {
				frappe.msgprint({
					message: __("Row {0}: the expense on {1} for {2} is already fully allocated to Asset Repairs / Service Records.",
						[row.idx, row.purchase_invoice, row.expense_account.bold()]),
					indicator: "orange",
				});
			}
		});
	},

	repair_cost(frm) {
		if (frm.doctype === "Asset Service Record") refresh_total(frm);
	},
});

function refresh_total(frm) {
	const services = (frm.doc.invoices || []).reduce((s, r) => s + flt(r.repair_cost), 0);
	frm.set_value("services_cost", services);
	frm.set_value("total_service_cost", services + flt(frm.doc.materials_cost));
}

function apply_service_by(frm) {
	const external = frm.doc.service_by === "External";
	frm.set_df_property("performed_by", "label", external ? __("Supplier's Technician") : __("Technician"));
	frm.set_df_property("sec_materials", "description", external
		? __("The service to buy (e.g. the supplier's service item) and any parts. Use Material Request → Request Service Purchase to raise the purchase request.")
		: __("Parts and consumables this job needs. Use Material Request → Request from Stores to issue them, or Request Purchase to buy them."));
	frm.fields_dict.invoices && frm.set_df_property("invoices", "label", external ? __("Supplier Invoices") : __("Service Invoices"));
}

function change_status(frm, status) {
	const run = () => frappe.xcall(`${ASR_API}.set_status`, { service_record: frm.doc.name, status }).then(() => frm.reload_doc());
	frm.is_dirty() ? frm.save().then(run) : run();
}

function make_request(frm, purpose, rows) {
	const go = () => frappe.xcall(`${ASR_API}.make_material_request`, {
		service_record: frm.doc.name, purpose, rows: rows.map((r) => r.name),
	}).then((name) => {
		frappe.show_alert({ message: __("Material Request {0} created", [name]), indicator: "green" });
		frm.reload_doc();
		frappe.set_route("Form", "Material Request", name);
	});
	frm.is_dirty() ? frm.save().then(go) : go();
}

function issue_used(frm, rows) {
	const go = () => frappe.confirm(
		__("Record the parts already used from the workshop store as a Material Issue Stock Entry (no Material Request)?"),
		() => frappe.xcall(`${ASR_API}.make_material_issue`, { service_record: frm.doc.name, rows: rows.map((r) => r.name) })
			.then((name) => {
				frappe.show_alert({ message: __("Stock Entry {0} created", [name]), indicator: "green" });
				frm.reload_doc();
				frappe.set_route("Form", "Stock Entry", name);
			}));
	frm.is_dirty() ? frm.save().then(go) : go();
}

function refresh_actual_qty(frm, cdt, cdn) {
	if (frm.doctype !== "Asset Service Record") return; // the same table is also used on Asset Repair
	const row = locals[cdt][cdn];
	if (!row.item_code || !row.warehouse) {
		frappe.model.set_value(cdt, cdn, "actual_qty", 0);
		return;
	}
	frappe.xcall("erpnext.stock.get_item_details.get_bin_details", {
		item_code: row.item_code, warehouse: row.warehouse, company: frm.doc.company,
	}).then((bin) => {
		frappe.model.set_value(cdt, cdn, "actual_qty", flt((bin || {}).actual_qty));
		frm.refresh_field("materials");
	});
}

frappe.ui.form.on("Asset Service Material", {
	item_code: refresh_actual_qty,
	warehouse: refresh_actual_qty,
	qty: (frm) => frm.doctype === "Asset Service Record" && frm.refresh_field("materials"),
});
