// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Insurance Claims Register"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date (Incident)"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date (Incident)"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "insurer",
			label: __("Insurer"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options:
				"\nDraft\nRecorded\nUnder Assessment\nApproved\nRejected\nPartly Settled\nSettled\nClosed\nCancelled",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "status" && data.status) {
			const colors = {
				Draft: "gray",
				Recorded: "orange",
				"Under Assessment": "orange",
				Approved: "green",
				Rejected: "red",
				"Partly Settled": "yellow",
				Settled: "green",
				Closed: "gray",
				Cancelled: "red",
			};
			value = `<span class="indicator-pill ${colors[data.status] || "gray"}">${__(data.status)}</span>`;
		}
		if (column.fieldname === "outstanding_amount" && flt(data.outstanding_amount) > 0) {
			value = `<span style="color:var(--orange-500);font-weight:600">${value}</span>`;
		}
		return value;
	},
};

upande_asset.reports.add_scope_filters("Insurance Claims Register");
