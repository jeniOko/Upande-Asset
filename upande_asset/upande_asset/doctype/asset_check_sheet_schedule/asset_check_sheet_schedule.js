// Copyright (c) 2026, jeniffer@upande.com,wycliffe@upande.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Asset Check Sheet Schedule", {
	refresh(frm) {
		// Always re-apply the template filter so it works on form load too.
		_set_template_filter(frm);

		if (frm.doc.check_sheet_template) {
			_show_frequency_badge(frm);
		}
		if (!frm.is_new()) {
			frm.add_custom_button(__("Refresh Status"), function () {
				frm.save().then(() => frm.reload_doc());
			});
		}
	},

	asset(frm) {
		frm.set_value("check_sheet_template", "");
		// fetch_from populates asset_category asynchronously; read it directly
		// from the database so the filter is set before the user opens the dropdown.
		if (frm.doc.asset) {
			frappe.db.get_value("Asset", frm.doc.asset, "asset_category", function (r) {
				if (r && r.asset_category) {
					frm.set_value("asset_category", r.asset_category);
				}
				_set_template_filter(frm);
			});
		} else {
			_set_template_filter(frm);
		}
	},

	// No handler for check_sheet_template — refresh already calls _show_frequency_badge.
});

function _set_template_filter(frm) {
	frm.set_query("check_sheet_template", function () {
		return {
			filters: { asset_category: frm.doc.asset_category || "__none__" },
		};
	});
}

function _show_frequency_badge(frm) {
	if (!frm.doc.check_sheet_template) return;
	frappe.db.get_value(
		"Asset Check Sheet Template",
		frm.doc.check_sheet_template,
		["frequency", "custom_frequency_days"],
		function (r) {
			if (!r) return;
			const label = r.frequency === "Custom"
				? `Every ${r.custom_frequency_days || "?"} days`
				: r.frequency;
			frm.set_intro(
				`<b>Frequency:</b> ${label}`,
				r.frequency ? "blue" : "orange"
			);
		}
	);
}
