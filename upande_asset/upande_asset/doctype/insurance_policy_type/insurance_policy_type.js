// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Insurance Policy Type", {
	refresh(frm) {
		// A standard IRA class is its own class; only your own types pick one.
		frm.set_df_property("ira_class", "read_only", frm.doc.is_standard ? 1 : 0);
		frm.set_df_property("policy_type_name", "read_only", frm.doc.is_standard && !frm.is_new() ? 1 : 0);
	},
});
