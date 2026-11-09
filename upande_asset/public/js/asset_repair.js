// Upande Asset additions on the ERPNext Asset Repair form.
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
	// Off: hidden; a repair that already has rows keeps showing them, read-only and without requests.
	frm.set_df_property("repair_materials", "read_only", on ? 0 : 1);
	if (on) {
		upande_asset.materials.setup(frm, { ...opts, table: "repair_materials", allow_direct_issue: !!opts.allow_direct_issue });
	} else if (has_rows) {
		upande_asset.lock_requested_rows(frm, "repair_materials");
	}
}
