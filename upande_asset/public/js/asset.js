// Asset Insurance additions on the ERPNext Asset form.
frappe.ui.form.on("Asset", {
	setup(frm) {
		frm.set_df_property("insurance_policy", "read_only", 1);
	},

	asset_category(frm) {
		// Copy explicitly: fetch_from treats a 0 Check as empty and would not overwrite.
		if (!frm.is_new() || !frm.doc.asset_category) return;
		frappe.db.get_value("Asset Category", frm.doc.asset_category, "insurance_required").then((r) => {
			frm.set_value("insurance_required", cint(r.message && r.message.insurance_required));
		});
	},

	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1) return;
		show_insurance_headline(frm);

		if (frm.doc.insurance_required && !["Scrapped", "Sold"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Insurance Claim"), () => {
				frappe.new_doc("Insurance Claim", {
					asset: frm.doc.name,
					company: frm.doc.company,
				});
			}, __("Create"));
		}
	},
});

function show_insurance_headline(frm) {
	if (!frm.doc.insurance_required) return;
	frappe.xcall("upande_asset.upande_asset.insurance.asset.get_asset_insurance_status", { asset: frm.doc.name })
		.then((s) => {
			if (!s || !s.required) return;
			if (s.state === "Uninsured") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator red">${__("Uninsured")}</span>
						${__("This asset requires insurance but is not on any active Insurance Policy.")}
					</div></div>`
				);
			} else if (s.state === "Cover expiring") {
				frm.dashboard.set_headline_alert(
					`<div class="row"><div class="col-xs-12">
						<span class="indicator orange">${__("Cover expiring")}</span>
						${__("Insurance cover ends in {0} day(s).", [s.days_left])}
					</div></div>`
				);
			}
		});
}
