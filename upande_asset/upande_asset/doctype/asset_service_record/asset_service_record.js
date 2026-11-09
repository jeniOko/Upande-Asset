const ASR_API = "upande_asset.upande_asset.doctype.asset_service_record.asset_service_record";

frappe.ui.form.on("Asset Service Record", {
	setup(frm) {
		// "Complete" is the submit action here, so drop Frappe's "Submit this document to confirm" banner.
		frm.show_submit_message = () => {};
		frm.set_query("asset", () => ({ filters: { docstatus: 1 } }));
		frm.set_query("cost_center", (doc) => ({ filters: { company: doc.company, is_group: 0 } }));
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

	onload(frm) {
		erpnext.accounts.dimensions.setup_dimension_filters(frm, frm.doctype);
	},

	company(frm) {
		erpnext.accounts.dimensions.update_dimension(frm, frm.doctype);
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
			const materials_on = !!(frm.doc.__onload || {}).materials_enabled;  // the table may be off
			if (external && !frm.doc.supplier && d.default_supplier) frm.set_value("supplier", d.default_supplier);
			if (materials_on && external && d.default_service_item && !(frm.doc.materials || []).some((r) => r.item_code === d.default_service_item)) {
				const row = frm.add_child("materials", { item_code: d.default_service_item, qty: 1 });
				frm.refresh_field("materials");
				frappe.model.trigger("item_code", d.default_service_item, row);
			}
		});
	},

	refresh(frm) {
		apply_service_by(frm);
		// Materials: requests and issues by stock availability (public/js/materials.js). Hidden when
		// Asset Settings → Use Material Requests is off; records that already have rows keep showing them.
		const materials_on = !!(frm.doc.__onload || {}).materials_enabled;
		const has_rows = (frm.doc.materials || []).length > 0;
		frm.toggle_display(["sec_materials", "materials", "sec_materials_total", "materials_cost"], materials_on || has_rows);
		// Off: parts are recorded as on ERPNext's Asset Repair (Consumed Stock Items, issued on Complete).
		const has_stock_items = (frm.doc.stock_items || []).length > 0;
		frm.toggle_display(["sec_stock_items", "stock_items", "sec_stock_items_total", "consumed_items_cost", "consumption_stock_entry"],
			!materials_on || has_stock_items);
		frm.set_query("item_code", "stock_items", () => ({ filters: { is_stock_item: 1, disabled: 0, has_variants: 0 } }));
		frm.set_query("warehouse", "stock_items", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
		frm.set_df_property("materials", "read_only", materials_on ? 0 : 1);
		if (!materials_on && has_rows) upande_asset.lock_requested_rows(frm, "materials");
		if (materials_on && frm.doc.service_by) {
			upande_asset.materials.setup(frm, {
				table: "materials",
				allow_submitted: true,
				allow_direct_issue: !!(frm.doc.__onload || {}).allow_direct_material_issue,
				...((frm.doc.__onload || {}).materials_options || {}),
				purchase_label: frm.doc.service_by === "External" ? __("Request Service Purchase") : null,
			});
		}
		frm.set_query("item_code", "materials", () => ({ filters: { disabled: 0, has_variants: 0 } }));  // stock, asset and service items
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
	const consumed = (frm.doc.stock_items || []).reduce((s, r) => s + flt(r.total_value), 0);
	frm.set_value("services_cost", services);
	frm.set_value("consumed_items_cost", consumed);
	frm.set_value("total_service_cost", services + flt(frm.doc.materials_cost) + consumed);
}

// Consumed Stock Items: ERPNext's own child table; its events live in Asset Repair's form script,
// so the Service Record fetches the valuation rate and line total itself.
function consumed_item_rate(frm, cdt, cdn) {
	if (frm.doctype !== "Asset Service Record") return;
	const row = locals[cdt][cdn];
	if (!row.item_code || !row.warehouse) return;
	frappe.call({
		method: "erpnext.stock.utils.get_incoming_rate",
		args: { args: { item_code: row.item_code, warehouse: row.warehouse, qty: flt(row.consumed_quantity) || 1, company: frm.doc.company,
			posting_date: frappe.datetime.get_today(), posting_time: frappe.datetime.now_time() } },
		callback: (r) => frappe.model.set_value(cdt, cdn, "valuation_rate", flt(r.message)),
	});
}

function consumed_item_total(frm, cdt, cdn) {
	if (frm.doctype !== "Asset Service Record") return;
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "total_value", flt(row.consumed_quantity) * flt(row.valuation_rate));
	refresh_total(frm);
}

frappe.ui.form.on("Asset Repair Consumed Item", {
	item_code: consumed_item_rate,
	warehouse: consumed_item_rate,
	consumed_quantity: consumed_item_total,
	valuation_rate: consumed_item_total,
});

frappe.ui.form.on("Asset Service Record", {
	stock_items_add(frm, cdt, cdn) {
		const wh = (frm.doc.__onload || {}).service_materials_warehouse;
		if (wh) frappe.model.set_value(cdt, cdn, "warehouse", wh);
	},
	stock_items_remove: (frm) => refresh_total(frm),
});

function apply_service_by(frm) {
	const external = frm.doc.service_by === "External";
	frm.set_df_property("performed_by", "label", external ? __("Supplier's Technician") : __("Technician"));
	frm.set_df_property("sec_materials", "description", external
		? __("The service to buy (e.g. the supplier's service item) and any parts. Use Material Request → Request Service Purchase to raise the purchase request.")
		: __("Parts and consumables this job needs. In stock: Material Request → Request from Stores. Out of stock: Request Purchase or Request Transfer, then issue them once received."));
	frm.fields_dict.invoices && frm.set_df_property("invoices", "label", external ? __("Supplier Invoices") : __("Service Invoices"));
}

function change_status(frm, status) {
	const run = () => frappe.xcall(`${ASR_API}.set_status`, { service_record: frm.doc.name, status }).then(() => frm.reload_doc());
	frm.is_dirty() ? frm.save().then(run) : run();
}
