// Materials rows (Asset Service Record → materials, Asset Repair → repair_materials):
// once a row is on a Material Request or Stock Entry, what was requested is frozen.
// The server enforces the same (repair_materials.validate_locked_rows); this shows it.
frappe.provide("upande_asset");

upande_asset.lock_requested_rows = function (frm, table) {
	const field = frm.fields_dict[table];
	if (!field || !field.grid) return;
	const locked_fields = ["item_code", "qty", "uom", "warehouse", "estimated_rate"];
	const rows = frm.doc[table] || [];
	const child_dt = field.df.options;
	rows.forEach((row) => {
		const locked = !!(row.material_request || row.stock_entry);
		locked_fields.forEach((f) => {
			const df = frappe.meta.get_docfield(child_dt, f, row.name);
			if (df) df.read_only = locked ? 1 : 0;
		});
	});
	if (field.df.__orig_description === undefined) field.df.__orig_description = field.df.description || "";
	const any_locked = rows.some((r) => r.material_request || r.stock_entry);
	frm.set_df_property(table, "description", any_locked
		? __("Rows already on a Material Request or Stock Entry are locked. Cancel that document to change them, or add a new row.")
		: field.df.__orig_description);
	field.grid.refresh();
};
