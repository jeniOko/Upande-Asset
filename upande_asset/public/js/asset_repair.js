// Materials for an Asset Repair.
// One button. It shows the requests already raised for this repair, lets you
// backfill Consumed Stock Items from the approved ones, and lets you raise
// another request. Stock still only moves when the repair is submitted.

const API = "upande_asset.overrides.asset_repair";

frappe.ui.form.on("Asset Repair", {
	// Nothing here fails quietly. Whatever would stop this repair going through
	// is put on screen before the button does anything.
	before_workflow_action(frm) {
		return preflight_gate(frm);
	},
	before_submit(frm) {
		return preflight_gate(frm);
	},
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("View or Request other Materials"), () => open_materials(frm));
		frm.add_custom_button(__("Check Before Submitting"), () => preflight_gate(frm, true));
	},
});

function preflight_gate(frm, announce_when_clear) {
	if (!(frm.doc.stock_items || []).length) {
		if (announce_when_clear) {
			frappe.msgprint({
				title: __("Nothing to check"),
				indicator: "blue",
				message: __("This repair consumes no stock items."),
			});
		}
		return Promise.resolve();
	}

	return frappe
		.call({ method: `${API}.preflight`, args: { asset_repair: frm.doc.name } })
		.then((r) => {
			const res = (r && r.message) || {};
			const over = res.over || [];
			if (!over.length) {
				if (announce_when_clear) {
					frappe.msgprint({
						title: __("Good to go"),
						indicator: "green",
						message: __("Every consumed item is in stock and within what was requested."),
					});
				}
				return;
			}

			return new Promise((resolve, reject) => {
				frappe.confirm(
					over_html(over),
					() => resolve(),
					() => {
						frm.selected_workflow_action = null;
						reject(new Error("cancelled"));
					}
				);
			});
		});
}

function over_html(over) {
	const rows = over
		.map(
			(o) =>
				`<tr><td>${frappe.utils.escape_html(o.item_code)}</td>` +
				`<td class="text-right">${format_number(o.requested)}</td>` +
				`<td class="text-right">${format_number(o.already_issued)}</td>` +
				`<td class="text-right">${format_number(o.allowed)}</td>` +
				`<td class="text-right"><b>${format_number(o.used)}</b></td></tr>`
		)
		.join("");

	return (
		`<p>${__("This repair consumes more than its material requests allow.")}</p>` +
		'<table class="table table-bordered"><thead><tr>' +
		`<th>${__("Item")}</th><th class="text-right">${__("Requested")}</th>` +
		`<th class="text-right">${__("Already Issued")}</th>` +
		`<th class="text-right">${__("Left to Use")}</th>` +
		`<th class="text-right">${__("Using")}</th></tr></thead><tbody>${rows}</tbody></table>` +
		`<p>${__("Carry on anyway? The extra will be noted on this repair.")}</p>`
	);
}

function open_materials(frm) {
	frappe.call({ method: `${API}.get_repair_materials`, args: { asset_repair: frm.doc.name } })
		.then((r) => show_dialog(frm, (r && r.message) || {}));
}

function show_dialog(frm, data) {
	const requests = data.requests || [];
	const to_pull = data.to_pull || [];
	const esc = frappe.utils.escape_html;

	const dialog = new frappe.ui.Dialog({
		title: __("Materials for this Repair"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Request Materials"),
		primary_action() {
			dialog.hide();
			raise_request(frm);
		},
	});

	let html = "";

	if (requests.length) {
		html += `<h5 class="text-muted">${__("Requests raised for this repair")}</h5>`;
		html += '<table class="table table-bordered" style="margin-bottom:18px"><thead><tr>';
		[__("Request"), __("Type"), __("Date"), __("Status")].forEach((h) => {
			html += `<th>${h}</th>`;
		});
		html += "</tr></thead><tbody>";
		requests.forEach((d) => {
			const indicator = d.docstatus === 1 ? "green" : "orange";
			html +=
				`<tr><td><a href="/app/material-request/${encodeURIComponent(d.name)}">${esc(d.name)}</a></td>` +
				`<td>${esc(d.material_request_type || "")}</td>` +
				`<td>${d.transaction_date ? frappe.datetime.str_to_user(d.transaction_date) : ""}</td>` +
				`<td><span class="indicator ${indicator}">${esc(d.workflow_state || d.status || "")}</span></td></tr>`;
		});
		html += "</tbody></table>";
	} else {
		html += `<div class="text-muted" style="margin-bottom:18px">${__(
			"No material request has been raised for this repair yet."
		)}</div>`;
	}

	if (to_pull.length) {
		html += `<h5 class="text-muted">${__("Approved and waiting to be used")}</h5>`;
		html += '<table class="table table-bordered" style="margin-bottom:12px"><thead><tr>';
		[__("Item"), __("Warehouse"), __("Quantity")].forEach((h) => {
			html += `<th>${h}</th>`;
		});
		html += "</tr></thead><tbody>";
		to_pull.forEach((d) => {
			html +=
				`<tr><td>${esc(d.item_name || d.item_code)}<br><span class="text-muted small">${esc(d.item_code)}</span></td>` +
				`<td>${esc(d.warehouse || "")}</td>` +
				`<td>${format_number(d.qty)} ${esc(d.uom || "")}</td></tr>`;
		});
		html += "</tbody></table>";
		if (data.is_draft) {
			html +=
				`<button class="btn btn-sm btn-primary pull-materials">${__("Add to Consumed Stock Items")}</button>` +
				`<div class="text-muted small" style="margin-top:8px">${__(
					"Stock leaves the store only when this repair is submitted."
				)}</div>`;
		} else {
			html += `<div class="text-muted small">${__(
				"This repair is submitted, so its consumed items can no longer be changed."
			)}</div>`;
		}
	} else if (requests.length) {
		html += `<div class="text-muted">${__(
			"Nothing is waiting to be used. Approved issue requests are either already in Consumed Stock Items, or the requests are still awaiting approval, or they are purchase requests that bring the part in rather than issue it."
		)}</div>`;
	}

	dialog.fields_dict.body.$wrapper.html(html);
	dialog.fields_dict.body.$wrapper.find(".pull-materials").on("click", () => {
		dialog.hide();
		pull(frm);
	});
	dialog.show();
}

function pull(frm) {
	frappe.call({
		method: `${API}.pull_materials`,
		args: { asset_repair: frm.doc.name },
		freeze: true,
		freeze_message: __("Adding requested materials..."),
	}).then((r) => {
		const res = (r && r.message) || {};
		frm.reload_doc();
		frappe.show_alert({
			message: __("Added {0} item(s) to Consumed Stock Items", [res.count || 0]),
			indicator: res.count ? "green" : "orange",
		});
	});
}

// The whole request is filled in here. The Material Request form is never opened.
function raise_request(frm) {
	frappe.call({ method: `${API}.get_request_spec`, args: { asset_repair: frm.doc.name } })
		.then((r) => request_dialog(frm, (r && r.message) || {}));
}

function request_dialog(frm, spec) {
	const fields = [
		{
			fieldname: "material_request_type",
			label: __("Request Type"),
			fieldtype: "Select",
			options: ["Material Issue", "Purchase"],
			default: "Material Issue",
			reqd: 1,
			description: __(
				"Material Issue draws from stock. Purchase buys the part in first, then it is issued."
			),
		},
		{ fieldname: "cb_top", fieldtype: "Column Break" },
		{
			fieldname: "schedule_date",
			label: __("Required By"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), 1),
			reqd: 1,
		},
		{ fieldname: "sb_extra", fieldtype: "Section Break" },
	];

	// whatever this site makes mandatory on a Material Request
	(spec.extra_fields || []).forEach((df) => {
		fields.push({
			fieldname: df.fieldname,
			label: __(df.label),
			fieldtype: df.fieldtype,
			options: df.options,
			default: df.default || undefined,
			reqd: df.reqd,
			depends_on: df.depends_on,
			mandatory_depends_on: df.mandatory_depends_on,
		});
	});

	if (spec.employee_field) {
		fields.push({
			fieldname: "employee",
			label: __("For Employee"),
			fieldtype: "Link",
			options: "Employee",
			depends_on: "eval:doc.material_request_type=='Material Issue'",
			mandatory_depends_on: "eval:doc.material_request_type=='Material Issue'",
			get_query: () => ({ filters: { company: spec.company, status: "Active" } }),
		});
	}

	const columns = [
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			in_list_view: 1,
			columns: 3,
			reqd: 1,
			get_query: () => ({ filters: { disabled: 0 } }),
		},
		{ fieldname: "qty", label: __("Qty"), fieldtype: "Float", in_list_view: 1, columns: 1, reqd: 1, default: 1 },
		{
			fieldname: "warehouse",
			label: __("From Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			in_list_view: 1,
			columns: 3,
			get_query: () => ({ filters: { company: spec.company, is_group: 0 } }),
		},
	];
	if (spec.has_purpose) {
		columns.push({ fieldname: "purpose", label: __("Purpose"), fieldtype: "Data", in_list_view: 1, columns: 3 });
	}

	fields.push({ fieldname: "sb_items", fieldtype: "Section Break", label: __("Parts Needed") });
	fields.push({
		fieldname: "items",
		fieldtype: "Table",
		label: __("Items"),
		reqd: 1,
		cannot_add_rows: false,
		in_place_edit: false,
		data: [],
		get_data: () => dialog.get_value("items") || [],
		fields: columns,
	});

	const dialog = new frappe.ui.Dialog({
		title: __("Request Materials for {0}", [frm.doc.asset_name || frm.doc.asset]),
		size: "large",
		fields: fields,
		primary_action_label: __("Create Request"),
		primary_action(values) {
			const rows = (values.items || []).filter((row) => row.item_code && flt(row.qty) > 0);
			if (!rows.length) {
				frappe.msgprint(__("Add at least one item with a quantity."));
				return;
			}

			const extras = {};
			(spec.extra_fields || []).forEach((df) => {
				if (values[df.fieldname]) extras[df.fieldname] = values[df.fieldname];
			});

			frappe.call({
				method: `${API}.create_material_request`,
				args: {
					asset_repair: frm.doc.name,
					material_request_type: values.material_request_type,
					schedule_date: values.schedule_date,
					employee: values.employee || "",
					extras: JSON.stringify(extras),
					items: JSON.stringify(
						rows.map((row) => ({
							item_code: row.item_code,
							qty: flt(row.qty),
							warehouse: row.warehouse || "",
							purpose: row.purpose || "",
						}))
					),
				},
				freeze: true,
				freeze_message: __("Raising the request..."),
				callback(r) {
					if (!r.message) return;
					dialog.hide();
					frm.reload_doc();
					frappe.msgprint({
						title: __("Request Raised"),
						indicator: "green",
						message: __(
							"{0} created as a draft with {1} item(s). It still has to clear its approval before stores can act on it.",
							[
								`<a href="/app/material-request/${r.message.name}">${r.message.name}</a>`,
								r.message.rows,
							]
						),
					});
				},
			});
		},
	});

	dialog.show();
	dialog.fields_dict.items.grid.add_new_row();
}
