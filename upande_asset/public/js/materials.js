// Materials rows (Asset Service Record → materials, Asset Repair → repair_materials).
// Server side: upande_asset/upande_asset/materials.py (same rules, enforced there).
//  - Each row has a Status from live stock and the documents raised for it.
//  - One Material Request button: a pop-up picks the purpose (in stock → from stores / issue used
//    materials; out of stock → purchase or transfer; received → issue it; asset items and
//    services → purchase) and the rows, then opens the document unsaved.
//  - Saving the document links its rows; removing a line from it and saving frees that row.
//  - Documents open unsaved; the user completes and saves them.
//  - Rows already on a request or issue are frozen until that document is cancelled.
frappe.provide("upande_asset");

const MATERIALS_API = "upande_asset.upande_asset.materials";

upande_asset.lock_requested_rows = function (frm, table) {
	const field = frm.fields_dict[table];
	if (!field || !field.grid) return;
	const locked_fields = ["item_code", "qty", "uom", "warehouse"];
	const rows = frm.doc[table] || [];
	const child_dt = field.df.options;
	const is_locked = (r) => !!(r.material_request || r.stock_entry || r.purchase_request || r.purchase_order);
	rows.forEach((row) => {
		locked_fields.forEach((f) => {
			const df = frappe.meta.get_docfield(child_dt, f, row.name);
			if (df) df.read_only = is_locked(row) ? 1 : 0;
		});
	});
	if (field.df.__orig_description === undefined) field.df.__orig_description = field.df.description || "";
	frm.set_df_property(table, "description", rows.some(is_locked)
		? __("Rows already on a request or issue are locked. Cancel that document to change them, or add a new row.")
		: field.df.__orig_description);
	field.grid.refresh();
};

upande_asset.materials = {
	COLORS: {
		"In Stock": "green", "To Issue": "orange", "Out of Stock": "red", "Not Requested": "gray",
		"Received, Out of Stock": "red", "Purchase Requested": "blue", "Transfer Requested": "blue", "Ordered": "blue",
		"Issue Requested": "blue", "Partly Issued": "purple", "Issued": "darkgrey",
	},

	// What each choice does and which rows it takes (the server checks again).
	ACTIONS: [
		{ purpose: "Material Issue", method: "make_material_request", label: __("Request from Stores"), needs: "can_request",
			help: __("Ask stores to issue items that are in stock (Material Request: Material Issue)."),
			rows: (r) => ["In Stock", "To Issue"].includes(r.material_status) },
		{ purpose: "Material Transfer", method: "make_material_request", label: __("Request Transfer"), needs: "can_request",
			help: __("Move out-of-stock items here from another warehouse that has them (Material Request: Material Transfer)."),
			rows: (r) => r.is_stock_item && r.material_status === "Out of Stock" },
		{ purpose: "Purchase", method: "make_material_request", label: __("Request Purchase"), purchase_with: "Material Request",
			help: __("Ask purchasing to buy items, assets or services (Material Request: Purchase)."),
			rows: (r) => ["Out of Stock", "Not Requested"].includes(r.material_status) },
		{ purpose: "Purchase Order", method: "make_purchase_order", label: __("Create Purchase Order"), no_purpose: true, purchase_with: "Purchase Order",
			help: __("Order straight from a supplier, without a Material Request. Choose the supplier on the order."),
			rows: (r) => ["Out of Stock", "Not Requested"].includes(r.material_status) },
		{ purpose: "Direct Issue", method: "make_material_issue", direct_issue: true, label: __("Issue Used Materials"), needs: "can_issue",
			help: __("Record in-stock parts the workshop has already used, without a request (Stock Entry: Material Issue)."),
			rows: (r) => ["In Stock", "To Issue"].includes(r.material_status) },
	],

	/**
	 * @param {object} opts  { table, allow_direct_issue, purchase_label, allow_submitted,
	 *                         purchase_documents, can_request, can_issue }  (materials.client_options)
	 */
	setup(frm, opts) {
		const table = opts.table;
		upande_asset.lock_requested_rows(frm, table);
		frm.set_indicator_formatter("item_code", (row) => this.COLORS[row.material_status] || "gray");
		if (frm.is_new() || frm.doc.docstatus === 2 || (frm.doc.docstatus === 1 && !opts.allow_submitted)) return;

		const rows = (frm.doc[table] || []).filter((r) => r.item_code);
		// Purchases start with the document(s) Asset Settings allow and the user may create.
		const purchase_documents = opts.purchase_documents || ["Material Request"];
		const actions = this.ACTIONS.filter((a) => (!a.direct_issue || opts.allow_direct_issue)
			&& (!a.needs || opts[a.needs] !== false)
			&& (!a.purchase_with || purchase_documents.includes(a.purchase_with))
			&& rows.some(a.rows));
		if (actions.length) {
			frm.add_custom_button(__("Material Request"), () => this.dialog(frm, opts, rows, actions));
		}
		const to_issue = rows.filter((r) => r.material_status === "To Issue");
		if (to_issue.length) {
			frm.dashboard.add_comment(
				__("{0} received and ready to issue: {1}. Use Material Request → Request from Stores{2}.", [
					to_issue.length === 1 ? __("1 item is") : __("{0} items are", [to_issue.length]),
					to_issue.map((r) => frappe.utils.escape_html(r.item_name || r.item_code)).join(", "),
					opts.allow_direct_issue ? __(" or Issue Used Materials") : "",
				]),
				"orange", true);
		}
	},

	// Pop-up: choose the purpose, see / untick the rows that qualify, then open the document.
	// Large, so long item names can be read.
	dialog(frm, opts, rows, actions) {
		const label = (a) => (a.purpose === "Purchase" && opts.purchase_label) ? opts.purchase_label : a.label;
		const d = new frappe.ui.Dialog({
			title: __("Material Request"),
			size: "large",
			fields: [
				{ fieldname: "purpose", fieldtype: "Select", label: __("Purpose"), reqd: 1,
					options: actions.map((a) => ({ value: a.purpose, label: label(a) })),
					default: actions[0].purpose, onchange: () => render() },
				{ fieldname: "rows_html", fieldtype: "HTML" },
			],
			primary_action_label: __("Continue"),
			primary_action: (v) => {
				const a = actions.find((x) => x.purpose === v.purpose);
				const picked = d.fields_dict.rows_html.$wrapper.find("input.ua-mr-row:checked").map((_i, el) => el.value).get();
				if (!picked.length) {
					frappe.msgprint(__("Tick at least one row."));
					return;
				}
				d.hide();
				const args = { rows: picked };
				if (!a.direct_issue && !a.no_purpose) args.purpose = a.purpose;
				this.open(frm, a.method, args);
			},
		});
		const render = () => {
			const a = actions.find((x) => x.purpose === d.get_value("purpose"));
			const esc = frappe.utils.escape_html;
			const $w = d.fields_dict.rows_html.$wrapper;
			$w.html(`
				<p class="text-muted small">${esc(a.help)}</p>
				<table class="table table-bordered table-condensed small" style="margin-bottom:0">
					<thead><tr><th style="width:32px"></th><th style="width:40%">${__("Item")}</th><th class="text-right">${__("Qty")}</th>
						<th>${__("Warehouse")}</th><th class="text-right">${__("Actual Qty")}</th><th>${__("Status")}</th></tr></thead>
					<tbody>${rows.filter(a.rows).map((r) => `<tr>
						<td><input type="checkbox" class="ua-mr-row" value="${esc(r.name)}"></td>
						<td style="white-space:normal;word-break:break-word">${esc(r.item_name || r.item_code)}</td>
						<td class="text-right">${format_number(r.qty)} ${esc(r.uom || "")}</td>
						<td>${esc(r.warehouse || "")}</td>
						<td class="text-right">${r.is_stock_item ? format_number(r.actual_qty) : "–"}</td>
						<td>${esc(__(r.material_status || ""))}</td></tr>`).join("")}</tbody>
				</table>`);
			$w.find("input.ua-mr-row").prop("checked", true);
		};
		d.show();
		render();
	},

	// Opens the request / issue as a new, unsaved form (like ERPNext's "Create →" buttons).
	open(frm, method, args) {
		const go = () => frappe.xcall(`${MATERIALS_API}.${method}`, { doctype: frm.doctype, name: frm.doc.name, ...args })
			.then((doc) => {
				frappe.model.sync(doc);
				frappe.set_route("Form", doc.doctype, doc.name);
			});
		frm.is_dirty() ? frm.save().then(go) : go();
	},

	// Live Actual Qty while editing a row (the server refreshes it, and the Status, on load and save).
	refresh_actual_qty(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code || !row.warehouse) {
			frappe.model.set_value(cdt, cdn, "actual_qty", 0);
			return;
		}
		frappe.xcall("erpnext.stock.get_item_details.get_bin_details", {
			item_code: row.item_code, warehouse: row.warehouse, company: frm.doc.company,
		}).then((bin) => frappe.model.set_value(cdt, cdn, "actual_qty", flt((bin || {}).actual_qty)));
	},
};

frappe.ui.form.on("Asset Service Material", {
	item_code: (frm, cdt, cdn) => upande_asset.materials.refresh_actual_qty(frm, cdt, cdn),
	warehouse: (frm, cdt, cdn) => upande_asset.materials.refresh_actual_qty(frm, cdt, cdn),
});
