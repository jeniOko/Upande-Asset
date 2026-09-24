// A request raised for an asset repair is issued the ordinary way: Create →
// Issue Material. That stock entry is what relieves the store, and submitting
// it copies the lines onto the repair's Consumed Stock Items, which are a
// record of what was fitted rather than a second stock movement.

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (!frm.doc.custom_repair_reference) return;

		const repair = frm.doc.custom_repair_reference;

		frm.add_custom_button(__("Open Asset Repair"), () => {
			frappe.set_route("Form", "Asset Repair", repair);
		});

		if (frm.doc.docstatus === 1) {
			frm.dashboard.add_comment(
				__("Raised for asset repair {0}. Issue it from here — the parts land on that repair's Consumed Stock Items on their own.", [
					'<a href="/app/asset-repair/' + encodeURIComponent(repair) + '">' +
						frappe.utils.escape_html(repair) + "</a>",
				]),
				"blue",
				true
			);
		}
	},
});
