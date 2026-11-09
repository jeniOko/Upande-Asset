frappe.ui.form.on("Asset Service Type", {
	setup(frm) {
		frm.set_query("default_service_item", () => ({ filters: { is_stock_item: 0, is_fixed_asset: 0, disabled: 0 } }));
		frm.set_query("default_supplier", () => ({ filters: { disabled: 0 } }));
	},
});
