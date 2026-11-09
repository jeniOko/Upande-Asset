frappe.listview_settings["Insurance Claim"] = {
	add_fields: ["status", "outstanding_amount"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colors = {
			Draft: "red", Recorded: "orange", "Under Assessment": "yellow", Approved: "green", Rejected: "red",
			"Partly Settled": "cyan", Settled: "green", Closed: "gray", Cancelled: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
