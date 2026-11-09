frappe.listview_settings["Insurance Policy"] = {
	add_fields: ["status", "end_date", "docstatus"],
	get_indicator(doc) {
		const colors = { Draft: "red", Active: "green", Expiring: "orange", Expired: "gray", Renewed: "blue", Cancelled: "red",
			"Renewal In Progress": "purple", "Awaiting Premium": "yellow" };
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
