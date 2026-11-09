// Upande Asset additions on the ERPNext Asset Repair form.
const REPAIR_MATERIALS_API = "upande_asset.upande_asset.repair_materials";

frappe.ui.form.on("Asset Repair", {
	refresh(frm) {
		upande_asset.why_why.setup(frm);
		setup_repair_materials(frm);
	},
});

// Asset Settings → Request Repair Materials: a Materials table with Material Requests
// (from stores or to purchase) instead of ERPNext's issue-on-submit Stock Items.
function setup_repair_materials(frm) {
	const opts = (frm.doc.__onload || {}).repair_materials || {};
	const on = !!opts.enabled;
	const has_rows = (frm.doc.repair_materials || []).length > 0;
	frm.toggle_display(["repair_materials_section", "repair_materials", "requested_materials_cost"], on || has_rows);
	// Keep ERPNext's Stock Items for repairs that already use them; otherwise the request flow replaces them.
	frm.toggle_display("stock_items", !on || (frm.doc.stock_items || []).length > 0);
	if (on || has_rows) upande_asset.lock_requested_rows(frm, "repair_materials");
	if (!on || frm.is_new() || frm.doc.docstatus !== 0) return;

	const open_rows = (frm.doc.repair_materials || []).filter((r) => r.item_code && !r.material_request && !r.stock_entry);
	if (!open_rows.length) return;
	const group = __("Material Request");
	if (open_rows.some((r) => r.is_stock_item)) {
		frm.add_custom_button(__("Request from Stores"), () => request(frm, "Material Issue", open_rows), group);
	}
	frm.add_custom_button(__("Request Purchase"), () => request(frm, "Purchase", open_rows), group);
	if (opts.allow_direct_issue && open_rows.some((r) => r.is_stock_item)) {
		frm.add_custom_button(__("Issue Used Materials"), () => issue_used(frm, open_rows), group);
	}
}

function request(frm, purpose, rows) {
	const go = () => frappe.xcall(`${REPAIR_MATERIALS_API}.make_material_request`, {
		asset_repair: frm.doc.name, purpose, rows: rows.map((r) => r.name),
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
		() => frappe.xcall(`${REPAIR_MATERIALS_API}.make_material_issue`, { asset_repair: frm.doc.name, rows: rows.map((r) => r.name) })
			.then((name) => {
				frappe.show_alert({ message: __("Stock Entry {0} created", [name]), indicator: "green" });
				frm.reload_doc();
				frappe.set_route("Form", "Stock Entry", name);
			}));
	frm.is_dirty() ? frm.save().then(go) : go();
}

function repair_material_qty(frm, cdt, cdn) {
	if (frm.doctype !== "Asset Repair") return;
	const row = locals[cdt][cdn];
	if (!row.item_code || !row.warehouse) {
		frappe.model.set_value(cdt, cdn, "actual_qty", 0);
		return;
	}
	frappe.xcall("erpnext.stock.get_item_details.get_bin_details", {
		item_code: row.item_code, warehouse: row.warehouse, company: frm.doc.company,
	}).then((bin) => frappe.model.set_value(cdt, cdn, "actual_qty", flt((bin || {}).actual_qty)));
}

frappe.ui.form.on("Asset Service Material", {
	item_code: repair_material_qty,
	warehouse: repair_material_qty,
});
