frappe.listview_settings["Asset Service Record"] = {
	add_fields: ["status", "service_by", "docstatus"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colors = { Pending: "orange", "In Progress": "blue", Completed: "green", Cancelled: "red" };
		const status = doc.docstatus === 2 ? "Cancelled" : doc.docstatus === 1 ? "Completed" : doc.status || "Pending";
		return [__(status), colors[status] || "gray", "status,=," + status];
	},
};
